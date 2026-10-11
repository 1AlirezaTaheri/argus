"""Loading the login form must not spend the budget that protects a password.

`GET /api/captcha` used to draw from the same `LoginRateLimiter` as
`POST /api/login`, and the login page fetches a challenge on every mount. The
measured cost of that, with `CAPTCHA_ENABLED=false` and no password ever sent:

    1..5 -> HTTP 200
    6    -> HTTP 429 "Too many login attempts. Try again shortly."

So five reloads of a page nobody submitted locked an operator out of the admin
panel, and nothing they did resembles a failed login. Behind a shared egress
address it is worse -- one person's reloads lock out everyone behind it.

`tests/test_captcha_api.py` already carried an `issue_directly` helper whose
docstring said the endpoint "deliberately shares the login limiter, so a test
that needs several challenges to submit cannot obtain them over HTTP without
spending the very budget it is trying to measure". A test working around the
product in order to measure the product is a signal, and it was the only thing
recording that this was wrong.

These cases hold the separation in place: the captcha budget is generous and
its own, the login budget is unchanged at five, and neither endpoint can starve
the other.
"""
from __future__ import annotations

import pytest

from admin.services import auth as auth_service

# The `client` fixture is defined inside test_captcha_api.py rather than in
# conftest.py, so it is imported rather than redefined. Re-declaring it here
# would be a second copy that drifts: the two would disagree about the database
# and the app context, and these cases are about the very limiters that live on
# that context.
from tests.test_captcha_api import client, peer_ip  # noqa: F401


@pytest.fixture
def ctx(client):
    """The application context, which owns both limiters."""
    return client.app_ctx.state.ctx


@pytest.fixture
def ip(client):
    return peer_ip(client)


class TestTheTwoBudgetsAreSeparate:
    def test_both_limiters_exist(self, ctx) -> None:
        assert isinstance(ctx.captcha_limiter, auth_service.LoginRateLimiter)
        assert isinstance(ctx.login_limiter, auth_service.LoginRateLimiter)

    def test_they_are_different_objects(self, ctx) -> None:
        """Sharing an instance is the original defect in a different shape."""
        assert ctx.captcha_limiter is not ctx.login_limiter

    def test_the_login_budget_is_unchanged(self, ctx) -> None:
        """The protection that matters must not be relaxed to fix the lockout."""
        assert ctx.login_limiter._max == 5, (
            "the login attempt budget moved; this change was about the "
            "captcha budget, not this one"
        )

    def test_the_captcha_budget_is_larger(self, ctx) -> None:
        assert ctx.captcha_limiter._max == auth_service.CAPTCHA_REQUESTS_PER_WINDOW
        assert ctx.captcha_limiter._max > ctx.login_limiter._max


class TestPageLoadsDoNotLockYouOut:
    def test_reloading_the_login_page_stays_allowed(self, client, ctx, ip) -> None:
        """The defect itself: five renders, no password, no lockout.

        Uses the captcha budget deliberately over the login budget's limit, so
        it fails against the old code and passes against the new.
        """
        for _ in range(auth_service.CAPTCHA_REQUESTS_PER_WINDOW):
            resp = client.get("/api/captcha")
            assert resp.status_code == 200, (
                f"the login page became unviewable after "
                f"{_ + 1} reload(s)"
            )

    def test_the_captcha_endpoint_is_still_limited(self, client, ip) -> None:
        """Bulk collection is still refused.

        A generous budget is not an absent one: the endpoint's whole reason to
        be throttled is a script harvesting challenges.
        """
        ctx = client.app_ctx.state.ctx
        over = auth_service.CAPTCHA_REQUESTS_PER_WINDOW + 2
        statuses = [client.get("/api/captcha").status_code for _ in range(over)]

        assert 429 in statuses, (
            "GET /api/captcha is no longer rate limited at all"
        )
        first_refused = statuses.index(429)
        assert first_refused >= auth_service.CAPTCHA_REQUESTS_PER_WINDOW, (
            f"refused after {first_refused} requests, which is below the "
            f"documented budget of {auth_service.CAPTCHA_REQUESTS_PER_WINDOW}"
        )

    def test_the_refusal_says_what_it_is(self, client, ip) -> None:
        """It must not claim a password attempt happened.

        The old message said "Too many login attempts", which is how an operator
        with five reloads and one correct password is told to go and wait.
        """
        for _ in range(auth_service.CAPTCHA_REQUESTS_PER_WINDOW + 2):
            resp = client.get("/api/captcha")
        assert resp.status_code == 429
        detail = resp.json()["detail"].lower()
        assert "login attempt" not in detail, (
            f"the 429 blames password attempts: {detail!r}"
        )


class TestTheLoginBudgetIsStillReal:
    def test_five_bad_passwords_then_refused(self, client, ip) -> None:
        """The protection the shared budget was meant to provide.

        Kept deliberately, because "fixing" the lockout by removing the limit
        would pass every other case in this file.
        """
        from tests.test_captcha_api import get_captcha

        refused = None
        for attempt in range(8):
            token, answer = get_captcha(client)
            resp = client.post("/api/login", json={
                "username": "operator",
                "password": "wrong-password",
                "captcha": {"token": token, "answer": answer},
            })
            if resp.status_code == 429:
                refused = attempt
                break
        assert refused is not None, "the login limiter never engaged"
        assert refused <= 5, (
            f"it took {refused + 1} wrong passwords to be refused, "
            "so the login budget has been loosened"
        )

    def test_a_successful_login_clears_the_budget(self, client, ip) -> None:
        """Reset-on-success is unchanged, and it is what makes the two usable.

        Without it a burst of successful logins -- every verification probe
        does one -- would accumulate into a lockout.
        """
        ctx = client.app_ctx.state.ctx
        # `check` appends before it answers, so the budget of five is spent by
        # the fifth call and it is the sixth that is refused.
        for _ in range(ctx.login_limiter._max):
            assert ctx.login_limiter.check(ip)[0] is True
        assert ctx.login_limiter.check(ip)[0] is False

        ctx.login_limiter.reset(ip)
        assert ctx.login_limiter.check(ip)[0] is True