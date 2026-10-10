"""The per-client CAPTCHA cap must not become a lockout.

`CaptchaStore.issue` refuses a sixth challenge to a client that already holds
five outstanding ones. That is the right behaviour for a flooding client, and it
was the wrong behaviour for everyone else, because the release path was
unreachable:

    count = self._per_client.get(client_ip, 0)
    if count >= _MAX_PENDING_PER_CLIENT:
        return False          # <- returns here
    self._pending[nonce] = ...
    self._per_client[client_ip] = count + 1
    self._prune(now)          # <- only reachable below the guard

`_prune` is the only thing that releases a client once its challenges age out,
and `check_and_spend` only releases a client that *submits* something. So a
client that reached the cap could never leave it: five challenges, none solved,
and every later request refused for the lifetime of the process. Reloading the
login page five times was enough -- no wrong answers required -- and behind a
shared egress IP that locks out every user behind it.

Seen live in the admin log:

    captcha challenge refused: client already holds 5 unsolved
"""
from __future__ import annotations

import pytest

from admin.services import captcha as captcha_module
from admin.services.captcha import TOKEN_TTL_SECONDS

MAX = captcha_module._MAX_PENDING_PER_CLIENT


@pytest.fixture
def store():
    return captcha_module.CaptchaStore()


def _fill(store, client_ip: str, now: float) -> None:
    """Give `client_ip` the maximum number of outstanding challenges."""
    for i in range(MAX):
        assert store.issue(f"n{i}", f"digest{i}", client_ip, now) is True, (
            f"the store refused challenge {i} before the cap was reached"
        )


class TestCapIsNotALockout:
    def test_a_client_at_the_cap_is_refused(self, store) -> None:
        """The cap itself is still wanted; this pins the part that was broken."""
        now = 1_000.0
        _fill(store, "10.0.0.1", now)
        assert store.issue("extra", "digest", "10.0.0.1", now) is False

    def test_the_cap_lifts_once_the_challenges_expire(self, store) -> None:
        """The regression: a client at the cap could never leave it.

        No challenge is solved here, because a real user does not have to solve
        one to be locked out -- five page loads is enough. Only the passage of
        TTL should release the slots.
        """
        now = 1_000.0
        _fill(store, "10.0.0.1", now)

        later = now + TOKEN_TTL_SECONDS + 1
        assert store.issue("after", "digest", "10.0.0.1", later) is True, (
            "a client whose challenges have all expired is still refused, so it "
            "is locked out permanently"
        )

    def test_the_cap_lifts_without_any_waiting_if_one_is_solved(self, store) -> None:
        """The pre-existing release path, kept working."""
        now = 1_000.0
        _fill(store, "10.0.0.1", now)
        ok, reason = store.check_and_spend("n0", "digest0", "10.0.0.1", now)
        assert ok, reason
        assert store.issue("after", "digest", "10.0.0.1", now) is True

    def test_repeated_refusal_does_not_leak_slots_to_others(self, store) -> None:
        """The cap stays per client while it is held."""
        now = 1_000.0
        _fill(store, "10.0.0.1", now)
        for i in range(5):
            assert store.issue(f"x{i}", "d", "10.0.0.1", now) is False
        # A different client is unaffected.
        assert store.issue("other", "d", "10.0.0.2", now) is True
        # And the first is still released by expiry.
        assert store.issue("late", "d", "10.0.0.1", now + TOKEN_TTL_SECONDS + 1) is True

    def test_partial_expiry_frees_exactly_the_expired_slots(self, store) -> None:
        """Staggered issues must not clear a slot that is still live."""
        now = 1_000.0
        for i in range(MAX):
            assert store.issue(f"n{i}", f"d{i}", "10.0.0.1", now + i) is True
        assert store.issue("x", "d", "10.0.0.1", now) is False

        # Advance past the TTL of the *oldest* challenge only.
        just_after_first = now + 1 + TOKEN_TTL_SECONDS
        assert store.issue("y", "d", "10.0.0.1", just_after_first) is True, (
            "a slot freed by expiry was not reused"
        )
        # The most recent challenge is still outstanding, so exactly one slot
        # was freed and the cap is reached again.
        assert store.issue("z", "d", "10.0.0.1", just_after_first) is False


class TestPruneKeepsItsBookkeepingStraight:
    def test_pruning_does_not_leave_a_stale_per_client_count(self, store) -> None:
        """A pruned challenge must decrement the counter it incremented.

        Wrong here and the cap would drift: challenges disappear from
        _pending while _per_client keeps counting them, so the client is refused
        challenges it never actually holds.
        """
        now = 1_000.0
        _fill(store, "10.0.0.1", now)
        later = now + TOKEN_TTL_SECONDS + 1
        store.issue("again", "d", "10.0.0.1", later)

        # Pruning released all five, and the new challenge took one back, so the
        # counter is exactly 1 -- not 5, and not 0.
        assert store._per_client.get("10.0.0.1") == 1
        # Four more fit before the cap is reached again.
        for i in range(MAX - 1):
            assert store.issue(f"more{i}", "d", "10.0.0.1", later) is True
        assert store.issue("over", "d", "10.0.0.1", later) is False
