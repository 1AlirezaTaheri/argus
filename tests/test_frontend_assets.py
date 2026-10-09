"""Regression tests for frontend defects that no other test could see.

Every item here corresponds to a bug that shipped and was invisible to both
``tsc --noEmit`` and the API suite, because each one lives in the *served* HTML
or the built CSS rather than in Python or in the component graph:

* the CSP allowed the anti-flash theme script by neither hash nor
  ``unsafe-inline``, so the browser refused to run it and the whole app rendered
  in the HTML's default theme until React mounted;
* ``index.html`` linked Inter from ``fonts.googleapis.com``, which the CSP blocks
  (``style-src 'self'``) and which is unreachable from Iran, so the declared font
  silently never loaded anywhere;
* three files of frontend source carried UTF-8 that had been decoded as cp1252
  and re-encoded, rendering as ``â€”`` and ``â€¦`` in the UI;
* ``tailwind.config.js`` declared ``darkMode: ['class']`` while the app themes via
  a ``data-theme`` attribute, which is a trap rather than a bug: nothing used a
  ``dark:`` variant, but the first person to write one would get no styling.

These assert the files directly. They are cheap, hermetic, and they fail loudly
the moment the served markup and the header stop agreeing with each other.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
FRONTEND = REPO / "admin" / "frontend"
INDEX = FRONTEND / "index.html"
BOOTSTRAP = REPO / "admin" / "services" / "bootstrap.py"
TAILWIND = FRONTEND / "tailwind.config.js"
PACKAGE = FRONTEND / "package.json"
SRC = FRONTEND / "src"

# The byte sequences produced by taking UTF-8 and re-encoding each byte as
# cp1252. Verified against the actual files, not assumed: an earlier repair pass
# guessed these and matched a third of them.
MOJIBAKE = {
    "c3a2e282ace2809d": "em dash",
    "c3a2e282acc2a6": "ellipsis",
    "c3a2e280a0e28099": "right arrow",
    "c382c2b7": "middle dot",
}


def inline_theme_script() -> str:
    """The text content of the one inline ``<script>`` in index.html."""
    index = INDEX.read_text(encoding="utf-8")
    match = re.search(r"<script>\n(?P<body>.*?)\n\s*</script>", index, re.DOTALL)
    assert match is not None, "the anti-flash theme script is missing from index.html"
    return match.group("body")


class TestCspAllowsTheThemeScript:
    """The theme script must be allowed by hash, or it silently never runs."""

    def test_csp_hash_matches_the_inline_script(self) -> None:
        expected = "sha256-" + hashlib.sha256(
            inline_theme_script().encode("utf-8")
        ).hexdigest()

        boot = BOOTSTRAP.read_text(encoding="utf-8")
        found = re.search(r"'sha256-([0-9a-f]{64})'", boot)
        assert found is not None, "the CSP carries no sha256 for the inline script"
        assert f"sha256-{found.group(1)}" == expected, (
            "the CSP hash is stale: it no longer matches the script in "
            "index.html, so the browser will refuse to run the theme script and "
            "light-theme users get a flash of the dark background"
        )

    def test_script_src_does_not_permit_unsafe_inline(self) -> None:
        # A hash is only meaningful if 'unsafe-inline' is absent. Allowing it
        # would silently re-open script injection and make the hash decorative.
        boot = BOOTSTRAP.read_text(encoding="utf-8")
        match = re.search(r'"script-src ([^"]*)"', boot)
        assert match is not None
        assert "unsafe-inline" not in match.group(1), (
            "script-src permits unsafe-inline, which voids the hash"
        )

    def test_no_inline_event_handlers_in_index(self) -> None:
        index = INDEX.read_text(encoding="utf-8")
        handlers = re.findall(r"\son(?:click|load|error|submit|change)\s*=", index)
        assert not handlers, f"index.html has inline event handlers: {handlers}"


class TestFontIsSelfHosted:
    """A font declared in the markup must actually be loadable."""

    def test_no_external_stylesheet_hosts_in_index(self) -> None:
        index = INDEX.read_text(encoding="utf-8")
        for host in ("fonts.googleapis.com", "fonts.gstatic.com", "cdn.", "unpkg"):
            assert host not in index, (
                f"index.html references {host}, which style-src 'self' blocks and "
                "which is unreachable from Iran; the font would silently not load"
            )

    def test_font_package_is_declared_and_imported(self) -> None:
        package = json.loads(PACKAGE.read_text(encoding="utf-8"))
        deps = {**package.get("dependencies", {}), **package.get("devDependencies", {})}
        font_pkgs = [name for name in deps if "fontsource" in name]
        assert font_pkgs, (
            "no self-hosted font package is declared, so the CSS stack falls "
            "back to the system UI font with no warning"
        )

        entry = (SRC / "main.tsx").read_text(encoding="utf-8")
        for pkg in font_pkgs:
            assert pkg in entry, (
                f"{pkg} is a dependency but is never imported, so its @font-face "
                "rules are never emitted into the bundle"
            )

    def test_font_stack_names_the_family_the_package_declares(self) -> None:
        """`Inter` is not the family @fontsource-variable/inter declares.

        It emits ``font-family: Inter Variable``. Asking for ``Inter`` matches no
        face at all: every one of the seven @font-face rules stays ``unloaded``
        and the browser never requests the woff2, while the fallback stack
        renders perfectly so nothing looks wrong.
        """
        config = TAILWIND.read_text(encoding="utf-8")
        assert "Inter Variable" in config, (
            "the font stack does not name 'Inter Variable', which is the family "
            "the self-hosted font declares; the font will never load"
        )


class TestSourceEncoding:
    """The UI must not ship double-encoded UTF-8."""

    def test_no_mojibake_in_frontend_source(self) -> None:
        offenders = []
        for path in sorted(SRC.rglob("*.tsx")):
            raw = path.read_bytes()
            for hexed, label in MOJIBAKE.items():
                if bytes.fromhex(hexed) in raw:
                    offenders.append(f"{path.relative_to(SRC)}: {label}")
        assert not offenders, (
            "double-encoded UTF-8 found in the frontend source, which renders "
            f"as mojibake: {offenders}"
        )

    @pytest.mark.parametrize(
        "relative",
        ["pages/telegram-users.tsx", "pages/target-detail.tsx"],
    )
    def test_multibyte_text_is_valid_utf8(self, relative: str) -> None:
        """Persian strings and dashes must survive.

        The repair for the mojibake above could have been done with a blanket
        latin-1 rewrite, which would have quietly destroyed every non-ASCII
        character. Decoding strictly proves they are intact and well formed.
        """
        raw = (SRC / relative).read_bytes()
        text = raw.decode("utf-8")  # raises on invalid UTF-8
        assert any(ord(ch) > 0x7F for ch in text), (
            f"{relative} has no non-ASCII characters left, which suggests the "
            "encoding repair flattened it"
        )


def _strip_js_comments(text: str) -> str:
    """Remove /* */ and // comments so prose about a setting is not the setting."""
    without_blocks = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", without_blocks)


def _theme_token_blocks() -> dict[str, str]:
    """The colour-token block for each theme.

    index.css declares ``[data-theme='dark']`` twice -- once for colours and once
    for the shadow tokens -- so matching every block and building a dict from
    them silently keeps the *last* one, which is the shadow block and has no
    ``--surface``. Pick the block that actually carries the surface token.
    """
    css = (SRC / "index.css").read_text(encoding="utf-8")
    blocks: dict[str, str] = {}
    for theme, body in re.findall(
        r"\[data-theme='(dark|light)'\]\s*\{(.*?)\n\}", css, re.DOTALL
    ):
        if "--surface:" in body:
            blocks[theme] = body
    return blocks


class TestTheming:
    def test_no_darkmode_class_config(self) -> None:
        code = _strip_js_comments(TAILWIND.read_text(encoding="utf-8"))
        assert "darkMode" not in code, (
            "darkMode is configured but the app themes through a data-theme "
            "attribute, so any dark: variant written against this config would "
            "compile to nothing"
        )

    def test_surface_differs_from_background_in_both_themes(self) -> None:
        """Cards must be distinguishable from the page they sit on.

        The light theme had a white page and grey cards, which inverted the
        layering and made every panel read as a hole cut out of the background.
        """
        blocks = _theme_token_blocks()
        assert set(blocks) == {"dark", "light"}, (
            f"expected colour tokens for both themes, found {sorted(blocks)}"
        )
        for theme, body in blocks.items():
            tokens = dict(
                (name, tuple(int(x) for x in value.split()))
                for name, value in re.findall(
                    r"--([a-z-]+):\s*(\d+ \d+ \d+);", body
                )
            )
            assert tokens["surface"] != tokens["bg"], (
                f"{theme}: --surface equals --bg, so a card is invisible against "
                "the page"
            )
            assert tokens["border"] != tokens["elevated"], (
                f"{theme}: --border equals --elevated, so a bordered panel and a "
                "raised control are indistinguishable"
            )