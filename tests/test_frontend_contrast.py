"""Every colour pairing the panel actually makes must meet its contrast.

Three real failures came out of rendering the panel and computing the ratio,
none of which any existing check looked at:

  * `--primary-fg` on `--primary` in the light theme -- white on #059669 -- is
    3.77:1. That is the label on the primary button, the most important control
    in the application, and it needs 4.5:1.
  * `--primary` as *text* on the light page is 3.48:1, so the active nav item's
    label -- the thing telling you where you are -- was below AA too.
  * `AVATAR_COLORS` pairs `text-{colour}-400` on `{colour}-500/15`, which is
    tuned for a near-black surface and drops to about 2.5:1 on white. The avatar
    palette had no light-theme variant at all.

Checking this by sampling the DOM only covers pairings a page happens to render
today; a token can fail and no test notice until someone puts it on screen. So
the pairings are declared here and checked directly against the token values
parsed out of index.css -- the same file the browser reads, so a token edit
that breaks a ratio fails here rather than in production.

Thresholds are WCAG 2.1 AA: 4.5:1 for body text, 3:1 for large text and for the
non-text contrast that chart strokes and borders need.
"""
from __future__ import annotations

import pathlib
import re
from dataclasses import dataclass

import pytest

CSS = pathlib.Path(__file__).resolve().parent.parent / (
    "admin/frontend/src/index.css"
)


def _theme_blocks() -> dict[str, dict[str, tuple[int, int, int]]]:
    """Parse `--name: R G B;` declarations out of each theme block."""
    text = CSS.read_text(encoding="utf-8")
    blocks: dict[str, dict[str, tuple[int, int, int]]] = {}

    # Each theme is a selector list followed by a block.
    for selector, name in (
        (r":root,\s*\[data-theme='dark'\]", "dark"),
        (r"\[data-theme='light'\]", "light"),
    ):
        match = re.search(
            selector + r"\s*\{(.*?)\n\}", text, re.DOTALL
        )
        assert match, f"could not find the {name} theme block in index.css"
        tokens: dict[str, tuple[int, int, int]] = {}
        for token, r, g, b in re.findall(
            r"--([a-z0-9-]+):\s*(\d+)\s+(\d+)\s+(\d+)\s*;", match.group(1)
        ):
            tokens[token] = (int(r), int(g), int(b))
        assert tokens, f"the {name} theme block declared no colour tokens"
        blocks[name] = tokens
    return blocks


THEMES = _theme_blocks()


def _relative_luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        s = value / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(
    fg: tuple[int, int, int], bg: tuple[int, int, int]
) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


# ---------------------------------------------------------------------------
# The pairings the design system makes.
#
# Each is (foreground, background, minimum ratio, what it is, where it is used).
# Declaring them is the point: an unlisted pairing is one nothing checks.
# ---------------------------------------------------------------------------
TEXT_PAIRINGS = [
    ("fg", "bg", "body text on the page"),
    ("fg", "surface", "body text on a card"),
    ("fg", "elevated", "body text on a raised panel"),
    ("muted", "bg", "secondary text on the page"),
    ("muted", "surface", "secondary text on a card"),
    ("muted", "elevated", "secondary text on a raised panel"),
    ("faint", "bg", "tertiary text on the page"),
    ("faint", "surface", "tertiary text on a card"),
    # Found by rendering, not by reading: the sidebar and every hover state put
    # text on --elevated, and the active nav item puts --primary text on
    # --primary-soft. Neither pairing was declared, and both were failing.
    #
    # Only the combinations the app actually renders are listed. `--primary-fg`
    # is a label on the primary *fill* and is never used on `primary-soft`;
    # declaring it there would have contorted the palette to satisfy a pairing
    # that does not exist, which is worse than an undeclared one.
    ("faint", "elevated", "tertiary text on a raised panel"),
    ("primary", "primary-soft", "the active nav item's label"),
    # Status text also appears directly on the page and on cards, not only on
    # its own soft chip -- a scan state in a table row, for instance.
    ("success", "bg", "a success label on the page"),
    ("danger", "bg", "a danger label on the page"),
    ("info", "bg", "an info label on the page"),
    ("warning", "bg", "a warning label on the page"),
    ("info", "surface", "an info label on a card"),
    ("info", "info-soft", "an info badge"),
    ("primary", "bg", "a link or accent label on the page"),
    ("primary", "surface", "a link or accent label on a card"),
    ("primary", "elevated", "a link or accent label on a raised panel"),
    ("primary-fg", "primary", "the label on a primary button"),
    ("primary-fg", "primary-strong", "the label on a hovered primary button"),
    ("danger-fg", "danger", "the label on a destructive button"),
    ("danger-fg", "danger-strong", "the label on a hovered destructive button"),
    ("danger", "danger-soft", "a danger badge"),
    ("warning", "warning-soft", "a warning badge"),
    ("success", "success-soft", "a success badge"),
    ("info", "info-soft", "an info badge"),
    ("success", "surface", "a success label on a card"),
    ("danger", "surface", "a danger label on a card"),
    ("warning", "surface", "a warning label on a card"),
]

# Non-text: chart strokes and control borders. 3:1 is the AA requirement for
# anything the user has to be able to see but not necessarily read.
GRAPHIC_PAIRINGS = [
    ("chart-1", "surface", "chart series 1"),
    ("chart-2", "surface", "chart series 2"),
    ("chart-3", "surface", "chart series 3"),
    ("chart-4", "surface", "chart series 4"),
    ("chart-5", "surface", "chart series 5"),
    ("border-strong", "surface", "a control's resting border"),
    ("border-strong", "bg", "a control's resting border on the page"),
    ("ring", "bg", "the focus ring on the page"),
    ("ring", "surface", "the focus ring on a card"),
]


def _ids() -> list[str]:
    return [f"{theme}-{fg}-on-{bg}" for theme in THEMES
            for fg, bg, _ in TEXT_PAIRINGS]


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("fg,bg,what", TEXT_PAIRINGS, ids=lambda v: v if isinstance(v, str) else "")
def test_text_contrast(theme: str, fg: str, bg: str, what: str) -> None:
    tokens = THEMES[theme]
    assert fg in tokens, f"{theme}: --{fg} is not declared"
    assert bg in tokens, f"{theme}: --{bg} is not declared"
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= 4.5, (
        f"{theme} {what}: --{fg} on --{bg} is {ratio:.2f}:1, needs 4.5:1 "
        f"(rgb{tokens[fg]} on rgb{tokens[bg]})"
    )


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("fg,bg,what", GRAPHIC_PAIRINGS, ids=lambda v: v if isinstance(v, str) else "")
def test_graphic_contrast(theme: str, fg: str, bg: str, what: str) -> None:
    tokens = THEMES[theme]
    assert fg in tokens, f"{theme}: --{fg} is not declared"
    assert bg in tokens, f"{theme}: --{bg} is not declared"
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= 3.0, (
        f"{theme} {what}: --{fg} on --{bg} is {ratio:.2f}:1, needs 3:1 "
        f"(rgb{tokens[fg]} on rgb{tokens[bg]})"
    )


class TestBothThemesDeclareTheSameVocabulary:
    """A token that exists in one theme and not the other is a latent crash.

    `rgb(var(--missing) / 0.5)` resolves to nothing and the element paints
    transparent, which looks like a deliberate choice in a screenshot.
    """

    def test_the_vocabularies_match(self) -> None:
        dark = set(THEMES["dark"])
        light = set(THEMES["light"])
        assert dark - light == set(), f"missing from the light theme: {dark - light}"
        assert light - dark == set(), f"missing from the dark theme: {light - dark}"

    def test_every_declared_pairing_uses_declared_tokens(self) -> None:
        for theme, tokens in THEMES.items():
            for fg, bg, what in TEXT_PAIRINGS + GRAPHIC_PAIRINGS:
                for name in (fg, bg):
                    assert name in tokens, (
                        f"{theme}: {what} refers to --{name}, which the theme "
                        "does not declare"
                    )


class TestTheAvatarPaletteIsThemeAware:
    """Avatars used hardcoded Tailwind steps with no light-theme variant.

    `AVATAR_COLORS` was `text-{hue}-400` on `{hue}-500/15`, tuned for the dark
    surface: on white, rose-400 sits at about 2.5:1 and an avatar's initials are
    text. There was no light-theme answer at all, which is also why nothing
    checked it -- there was nothing theme-dependent to check.

    The pair is now declared per theme, so it belongs in the pairings above
    rather than in a test of its own. What is checked here is that the six
    tints are actually distinct: a hash collision would quietly give several
    users the same colour, and six identical tokens would satisfy every ratio
    while looking like a bug.
    """

    @staticmethod
    def _source() -> str:
        return (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin/frontend/src/lib/utils.ts"
        ).read_text(encoding="utf-8")

    def test_the_palette_reads_tokens(self) -> None:
        source = self._source()
        assert "bg-avatar-1 text-avatar-1-fg" in source, (
            "the avatar palette no longer reads the per-theme tokens"
        )
        assert not re.search(r"text-(emerald|sky|amber|violet|rose|teal)-\d", source), (
            "the avatar palette is still hardcoded to raw Tailwind steps, which "
            "is what had no light-theme variant"
        )

    def test_it_uses_theme_aware_classes(self) -> None:
        source = self._source()
        for name in ("bg-avatar-1", "bg-avatar-6"):
            assert name in source, f"{name} is not used"

    @pytest.mark.parametrize("theme", sorted(THEMES))
    def test_the_six_tints_are_distinct(self, theme: str) -> None:
        tokens = THEMES[theme]
        backgrounds = []
        for index in range(1, 7):
            name = f"avatar-{index}"
            assert name in tokens, f"{theme}: --{name} is not declared"
            assert f"{name}-fg" in tokens, f"{theme}: --{name}-fg is not declared"
            backgrounds.append(tokens[name])
        assert len(set(backgrounds)) == 6, (
            f"{theme}: two avatar tints are identical, so two users share a "
            f"colour: {backgrounds}"
        )

    @pytest.mark.parametrize("theme", sorted(THEMES))
    @pytest.mark.parametrize("index", range(1, 7))
    def test_each_avatar_is_readable(self, theme: str, index: int) -> None:
        tokens = THEMES[theme]
        ratio = contrast(tokens[f"avatar-{index}-fg"], tokens[f"avatar-{index}"])
        assert ratio >= 4.5, (
            f"{theme} avatar {index}: initials are {ratio:.2f}:1 on their own "
            f"tint, needs 4.5:1"
        )