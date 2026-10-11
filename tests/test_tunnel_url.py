"""The Mini App URL must never be served from a stale setting.

`MINIAPP_URL` is static, and a Cloudflare quick tunnel's hostname is not: the
tunnel is restarted by deploys, reboots and Docker's own restart policy, and
every restart mints a new random name. When they drift apart the bot's Web App
button points at a name that no longer resolves. `/app` in Telegram then opens
nothing, and nothing raises anywhere -- the bot sent a perfectly valid button to
a host that is gone.

This happened on this deployment: `MINIAPP_URL` named
`few-preston-conference-congressional.trycloudflare.com`, which is NXDOMAIN,
while the tunnel was serving `reply-five-centered-empire.trycloudflare.com`.

So the tunnel writes the hostname it was given to a shared file, and
`bot.tunnel_url.py` reads that file in preference to the setting. These cases pin
the preference order, the validation, and the fallback.
"""
from __future__ import annotations

import pytest

from bot.tunnel_url import read_tunnel_url, resolve_miniapp_url

STALE = "https://few-preston-conference-congressional.trycloudflare.com"
LIVE = "https://reply-five-centered-empire.trycloudflare.com"


class TestReadTunnelUrl:
    def test_reads_a_published_url(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text(LIVE + "\n", encoding="utf-8")
        assert read_tunnel_url(str(f)) == LIVE

    def test_tolerates_surrounding_whitespace(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text(f"  {LIVE}  \n", encoding="utf-8")
        assert read_tunnel_url(str(f)) == LIVE

    def test_missing_file_is_not_an_error(self, tmp_path) -> None:
        assert read_tunnel_url(str(tmp_path / "absent")) is None

    def test_empty_file_is_not_an_error(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text("", encoding="utf-8")
        assert read_tunnel_url(str(f)) is None

    def test_a_non_https_value_is_refused(self, tmp_path) -> None:
        """Telegram only loads a Mini App over HTTPS.

        Forwarding anything else would produce a button that silently fails, which
        is the symptom this whole change exists to remove.
        """
        f = tmp_path / "tunnel-url.conf"
        f.write_text("http://example.com", encoding="utf-8")
        assert read_tunnel_url(str(f)) is None

    @pytest.mark.parametrize(
        "host",
        [
            "https://example.com",
            "https://attacker.test",
            "https://trycloudflare.com.evil.example",
        ],
    )
    def test_an_unexpected_host_is_refused(self, tmp_path, host: str) -> None:
        """The value becomes a button URL in a Telegram client.

        It is written by a file, not by the operator, so it is validated rather
        than trusted -- and a hostname that merely *contains* a tunnel suffix is
        the shape an attacker would use.
        """
        f = tmp_path / "tunnel-url.conf"
        f.write_text(host, encoding="utf-8")
        assert read_tunnel_url(str(f)) is None

    def test_a_named_tunnel_host_is_accepted(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text("https://argus.example.cfargotunnel.com", encoding="utf-8")
        assert read_tunnel_url(str(f)) == (
            "https://argus.example.cfargotunnel.com"
        )

    def test_a_trailing_path_is_stripped(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text(LIVE + "/app", encoding="utf-8")
        assert read_tunnel_url(str(f)) == LIVE


class TestResolvePrefersTheLiveUrl:
    def test_the_file_wins_over_a_stale_setting(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text(LIVE, encoding="utf-8")
        assert resolve_miniapp_url(STALE, str(f)) == LIVE

    def test_the_setting_is_used_when_no_file_exists(self, tmp_path) -> None:
        assert resolve_miniapp_url(STALE, str(tmp_path / "absent")) == STALE

    def test_no_file_and_no_setting_is_none(self, tmp_path) -> None:
        assert resolve_miniapp_url("", str(tmp_path / "absent")) is None
        assert resolve_miniapp_url(None, str(tmp_path / "absent")) is None

    def test_an_unusable_file_falls_back_rather_than_failing(self, tmp_path) -> None:
        """A corrupt or tampered file must not disable the Mini App.

        Falling back to the setting is worse than the live URL but still a
        working button, which is what matters when a deployment has no tunnel.
        """
        f = tmp_path / "tunnel-url.conf"
        f.write_text("not a url at all", encoding="utf-8")
        assert resolve_miniapp_url(STALE, str(f)) == STALE

    def test_a_trailing_slash_is_normalised(self, tmp_path) -> None:
        f = tmp_path / "tunnel-url.conf"
        f.write_text(LIVE, encoding="utf-8")
        assert resolve_miniapp_url(STALE + "/", str(f)) == LIVE


class TestBotUsesTheResolver:
    """The wiring, not just the helper.

    A resolver that only some callers use leaves the rest pointing at the dead
    hostname, and the symptom is "it works sometimes" -- the /app command reads
    one path and the menu button another.
    """

    def test_no_module_reads_settings_miniapp_url_directly(self) -> None:
        """Only resolved_url() may touch the setting, and only via getattr.

        Scanned over code lines only. The prose in resolved_url's docstring
        *mentions* ``settings.miniapp_url`` in order to explain why it must not
        be read elsewhere, and an earlier version of this test flagged that
        sentence -- the check failing on its own explanation, which is the same
        mistake as flagging a comment that documents an inline script.
        """
        import io
        import pathlib
        import tokenize

        source = pathlib.Path(__file__).resolve().parent.parent / "bot/miniapp.py"
        code_lines: set[int] = set()
        with source.open("rb") as handle:
            try:
                for tok in tokenize.tokenize(handle.readline):
                    if tok.type in (tokenize.STRING, tokenize.COMMENT):
                        continue
                    code_lines.update(range(tok.start[0], tok.end[0] + 1))
            except tokenize.TokenError:
                code_lines = set(
                    range(1, len(source.read_text(encoding="utf-8").splitlines()) + 1)
                )

        offenders = []
        for number, line in enumerate(
            source.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if number not in code_lines:
                continue
            stripped = line.strip()
            if "settings.miniapp_url" not in stripped:
                continue
            # The single legitimate reader: getattr inside resolved_url.
            if stripped.startswith("return resolve_miniapp_url(getattr("):
                continue
            offenders.append(f"{number}: {stripped}")
        assert not offenders, (
            "bot/miniapp.py reads settings.miniapp_url outside resolved_url(): "
            f"{offenders}"
        )

    def test_the_resolver_is_the_only_reader(self) -> None:
        """And the wiring exists, so the previous case is not passing vacuously."""
        from bot.miniapp import is_configured, resolved_url

        assert callable(resolved_url)
        assert callable(is_configured)

    def test_is_configured_follows_the_live_url(self) -> None:
        """A stale setting must not keep a dead button advertised.

        The old code tested `settings.miniapp_url`, which still started with
        https://, so it kept returning True and kept publishing a button that
        opened nothing.
        """
        from bot.miniapp import is_configured, resolved_url

        class Settings:
            miniapp_url = STALE

        s = Settings()
        # No tunnel file, so the setting is used and this is configured.
        assert is_configured(s) is True
        assert resolved_url(s) == STALE

        # The resolved value is what the button is built from.
        from bot.miniapp import app_url
        assert app_url(s).startswith("https://")
