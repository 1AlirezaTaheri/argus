"""Regression tests for frontend defects that no other test could see.

Every item here corresponds to a bug that shipped and was invisible to both
``tsc --noEmit`` and the API suite, because each one lives in the *served* HTML
or the built CSS rather than in Python or in the component graph:

* the CSP allowed the anti-flash theme script by neither hash nor
  ``unsafe-inline``, so the browser refused to run it and the whole app rendered
  in the HTML's default theme until React mounted. A follow-up attempt to allow
  it by hash was also wrong, and wrong *silently*: a browser hashes the script
  element's complete child text, whitespace included, so a digest of the tidied
  body never matches and the script never runs. The script is now a file;
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
    """Deprecated helper, kept only so the failure message below reads clearly.

    The panel used to carry its pre-paint theme script inline. It no longer does,
    and the tests assert that instead -- see TestNoInlineScript.
    """
    index = INDEX.read_text(encoding="utf-8")
    match = re.search(r"<script>\n(?P<body>.*?)\n\s*</script>", index, re.DOTALL)
    return match.group("body") if match else ""


class TestNoInlineScript:
    """The panel must not have any inline script at all.

    This is the real invariant, and it is stronger and more durable than the hash
    check that preceded it.

    The theme script was inline once, and the CSP allowed it by sha256. A browser
    hashes the script element's *complete* child content -- the newline after
    `<script>` and the indentation before `</script>` included. The CSP held the
    digest of the tidied body, so on every page the browser demanded a different
    hash and refused to execute it. Nothing failed: no build error, no exception,
    no failing test, because the test also hashed the tidied body. The only
    symptom was one console line and a theme that was never applied before first
    paint.

    So: the script now lives in public/theme.js and the hash is gone. These
    cases keep it gone and keep the replacement in place.
    """

    def test_index_html_has_no_inline_script(self) -> None:
        markup = _index_markup()
        inline = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>", markup)
        assert not inline, (
            f"index.html has an inline script: {inline}. Under script-src 'self' "
            "with no 'unsafe-inline' it would need a sha256 of its complete child "
            "text, which silently rots when the build tool reformats the HTML."
        )

    def test_theme_script_shipped_and_referenced(self) -> None:
        index = INDEX.read_text(encoding="utf-8")
        assert 'src="/theme.js"' in index, (
            "index.html no longer loads the anti-flash theme script; a "
            "light-theme user gets a flash of the dark background"
        )
        script = FRONTEND / "public/theme.js"
        assert script.exists(), f"{script} is referenced but does not exist"
        body = script.read_text(encoding="utf-8")
        assert "data-theme" in body, "theme.js never sets data-theme"
        # The current key, not the pre-rename one. Both strings are present in
        # theme.js after the rename -- the old key is still read so existing
        # preferences survive -- so asserting the old name would keep passing
        # while checking nothing.
        assert "argus-theme" in body, "theme.js never reads the stored preference"

    def test_theme_script_still_reads_the_pre_rename_key(self) -> None:
        """The migration is a requirement, not an accident of the rename.

        Renaming a localStorage key outright silently resets every existing
        user's chosen theme to their OS default. Nothing errors and the page
        still renders correctly, so the only thing holding this in place is a
        test.
        """
        body = (FRONTEND / "public/theme.js").read_text(encoding="utf-8")
        assert "ns-theme" in body, (
            "theme.js no longer reads the pre-rename key; existing users' "
            "stored theme preference will be discarded"
        )
        assert "argus-theme" in body, (
            "the new key is gone too, so nothing is being read at all"
        )

    def test_theme_script_is_not_deferred(self) -> None:
        """A module or a defer/async attribute would run after first paint."""
        index = INDEX.read_text(encoding="utf-8")
        tag = re.search(r"<script[^>]*theme\.js[^>]*>", index)
        assert tag is not None
        attrs = tag.group(0)
        assert 'type="module"' not in attrs, (
            "theme.js is loaded as a module, which defers it until after the "
            "document is parsed -- too late to prevent the flash"
        )
        for attr in ("defer", "async"):
            assert attr not in attrs, (
                f"theme.js has {attr}, so it may run after first paint"
            )


class TestCspAllowsTheThemeScript:
    """The theme script is a file, so the header has nothing to allow by hash."""

    def test_csp_carries_no_stale_hash(self) -> None:
        boot = BOOTSTRAP.read_text(encoding="utf-8")
        stale = re.findall(r"'sha256-[0-9a-f]{64}'", boot)
        assert not stale, (
            f"the CSP still carries {stale}, but no inline script remains for it "
            "to allow. A digest nobody verifies rots; delete it rather than "
            "correct it."
        )

    def test_script_src_does_not_permit_unsafe_inline(self) -> None:
        # A hash or a file is only meaningful if 'unsafe-inline' is absent.
        # Allowing it would silently re-open script injection.
        boot = BOOTSTRAP.read_text(encoding="utf-8")
        match = re.search(r'"script-src ([^"]*)"', boot)
        assert match is not None
        assert "unsafe-inline" not in match.group(1), (
            "script-src permits unsafe-inline, which voids the hardening"
        )
        assert "https://telegram.org" in match.group(1), (
            "script-src no longer names telegram.org, which the Mini App WebApp "
            "SDK needs"
        )

    def test_no_inline_event_handlers_in_index(self) -> None:
        markup = _index_markup()
        handlers = re.findall(r"\son(?:click|load|error|submit|change)\s*=", markup)
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


def _index_markup() -> str:
    """index.html with HTML comments removed.

    The comment above the theme script *documents* the inline-script problem, so
    it necessarily contains the literal text ``<script>`` twice. Scanning the raw
    file finds those two and reports them as inline scripts -- the check failing
    because of its own explanation.
    """
    return re.sub(r"<!--.*?-->", "", INDEX.read_text(encoding="utf-8"), flags=re.DOTALL)


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

    def test_elevated_differs_from_surface_in_both_themes(self) -> None:
        """Every `bg-elevated` fill must be visible on a `--surface` card.

        `bg-elevated` is the fill behind roughly thirty call sites: hover
        states, icon chips, skeletons, inline code spans, tooltips and menus.
        The light theme had `--elevated` and `--surface` both set to pure white,
        so every one of them vanished on a white card -- no hover feedback,
        skeletons you could not see, tooltips that looked like the page.

        The step goes in opposite directions per theme (lighter on near-black,
        darker on white), so this asserts only that a step exists.
        """
        blocks = _theme_token_blocks()
        assert set(blocks) == {"dark", "light"}
        for theme, body in blocks.items():
            tokens = dict(
                (name, tuple(int(x) for x in value.split()))
                for name, value in re.findall(
                    r"--([a-z-]+):\s*(\d+ \d+ \d+);", body
                )
            )
            assert tokens["elevated"] != tokens["surface"], (
                f"{theme}: --elevated equals --surface, so every bg-elevated fill "
                "(hover states, icon chips, skeletons, tooltips) is invisible on "
                "a card"
            )

    def test_elevated_is_used_on_a_surface_somewhere(self) -> None:
        """Guards the guard: if bg-elevated stopped being used, the test above
        would be asserting something no longer true."""
        users = [
            path
            for path in SRC.rglob("*.tsx")
            if "bg-elevated" in path.read_text(encoding="utf-8")
        ]
        assert len(users) >= 5, (
            "bg-elevated is barely used any more, so the --elevated token check "
            "may no longer describe anything that renders"
        )