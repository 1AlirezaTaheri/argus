#!/usr/bin/env python3
"""Every `shadow-*` utility in use must resolve to a theme token.

## The bug this pins

`tailwind.config.js` overrides the `boxShadow` scale with `var(--...)` tokens:

    boxShadow: {
      sm: 'var(--shadow-sm)',
      md: 'var(--shadow-md)',
      lg: 'var(--shadow-lg)',
      glow: 'var(--shadow-glow)',
      'elevate-1': ..., elevate: ..., 'elevate-3': ...,
      well: ...,
    }

Tailwind's `extend` merges *keys*, so any key not listed keeps its published
default. `2xl` was not listed, and two full-height drawers used `shadow-2xl`.
The shipped stylesheet therefore contained, verbatim:

    .shadow-2xl{--tw-shadow: 0 25px 50px -12px rgb(0 0 0 / .25); ...}
    .shadow-lg  {--tw-shadow: var(--shadow-lg); ...}

A hardcoded light-theme shadow. On the dark page (`#090c14`) it darkens the
background by about 2/255 and reads as nothing; on white it is 25% black where
the theme uses 14% slate. So two prominent overlay surfaces were lit differently
from every other surface in the app, and nothing anywhere complained.

## Why a source scan rather than a built-CSS check

Reading the built CSS would be the more direct proof -- it is what ships -- but
the test suite runs in a container with no build step, so it would skip on every
CI run and pin nothing. Scanning the source against the config catches the same
defect earlier and always runs.

The invariant asserted is one sentence: *if a component asks for a shadow, the
config must say what that name means.* A name the config does not define is a
name that silently means "Tailwind's opinion about light", which is exactly the
disagreement the token system exists to prevent.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

FRONTEND = pathlib.Path(__file__).resolve().parents[1] / "admin" / "frontend"
SRC = FRONTEND / "src"
CONFIG = FRONTEND / "tailwind.config.js"


def _declared_shadow_keys() -> set[str]:
    """Keys the config actually defines in its boxShadow scale."""
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r"boxShadow:\s*\{(.*?)\n      \}", text, re.DOTALL)
    if not match:
        pytest.fail("could not find the boxShadow scale in tailwind.config.js")
    # Both `sm:` and 'elevate-1': spellings appear.
    return set(re.findall(r"['\"]?([a-z0-9-]+)['\"]?\s*:", match.group(1)))


def _shadow_utilities_in_source() -> dict[str, list[str]]:
    """Every `shadow-<key>` reference, with where it appears.

    Comments are skipped. `ui.tsx` quotes `shadow-2xl` in the paragraph
    explaining why dialogs no longer use it, and matching that would make the
    test fail against correct code for the third time on this project -- the
    same mistake as asserting against compose comments that quote the probe they
    replaced.
    """
    found: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.tsx")):
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("*"):
                continue
            for key in re.findall(r"\bshadow-([a-z0-9-]+)\b", line):
                found.setdefault(key, []).append(
                    f"{path.relative_to(SRC)}:{lineno}"
                )
    return found


def test_the_config_defines_a_shadow_scale() -> None:
    """A missing scale would make every other check vacuously true."""
    assert CONFIG.exists(), "tailwind.config.js is missing"
    assert _declared_shadow_keys(), "the boxShadow scale is empty or unparsable"


def test_every_shadow_utility_in_use_is_defined_by_the_config() -> None:
    """The invariant: an undefined name is Tailwind's opinion, not the theme's.

    This is the check that fails on the original defect. `shadow-2xl` was in
    use on two drawers and absent from the scale, so the name resolved to a
    published default that neither theme asked for.
    """
    declared = _declared_shadow_keys()
    used = _shadow_utilities_in_source()

    assert used, (
        "no shadow utilities found in the source -- the scan is not looking "
        "at the right files, so this test would pass without checking anything"
    )

    undefined = {
        key: sites
        for key, sites in used.items()
        if key not in declared
    }
    assert not undefined, (
        "these shadow names are used in components but not defined in the "
        "boxShadow scale, so Tailwind's built-in value ships instead:\n"
        + "\n".join(
            f"  shadow-{key} at {', '.join(sites)}"
            for key, sites in sorted(undefined.items())
        )
        + "\n  Either add it to boxShadow as var(--...), or stop using it."
    )


def test_the_scale_defines_the_levels_the_depth_system_uses() -> None:
    """The tokens that exist must be reachable through the utility names.

    A token declared in index.css but absent from the config is unusable from
    markup, which reads as an unused token rather than a broken wiring. The
    converse -- a config entry pointing at a token nothing declares -- would
    make every use of that utility an empty shadow, silently.

    Each half is matched in the file that owns it. `var(--shadow-sm)` appears in
    the config; index.css contains only the declaration `--shadow-sm: ...`.
    Searching for the `var(...)` form in the CSS, as an earlier version of this
    test did, finds nothing and fails against a file that is entirely correct.
    """
    declared = _declared_shadow_keys()
    config = CONFIG.read_text(encoding="utf-8")
    css = (SRC / "index.css").read_text(encoding="utf-8")

    for key, token in (
        ("sm", "--shadow-sm"),
        ("md", "--shadow-md"),
        ("lg", "--shadow-lg"),
        ("glow", "--shadow-glow"),
        ("elevate-1", "--elevate-1"),
        ("elevate", "--elevate-2"),
        ("elevate-3", "--elevate-3"),
        ("well", "--inset-well"),
    ):
        assert key in declared, f"boxShadow does not define '{key}'"
        assert f"var({token})" in config, (
            f"the config's '{key}' does not point at {token}"
        )
        assert re.search(rf"^\s*{re.escape(token)}\s*:", css, re.MULTILINE), (
            f"{token} is not declared in index.css"
        )


def test_the_drawers_use_the_theme_depth_not_a_bare_drop() -> None:
    """The specific surfaces the original defect affected.

    Both are edge-flush slabs. They must carry the class that gives them the
    bevel and sheen, and they must keep their single edge border -- a migration
    that swallowed `border-l`/`border-r` would leave an unbordered panel sitting
    against the page.
    """
    sites = (
        (SRC / "pages" / "targets.tsx", "border-l"),
        (SRC / "components" / "mobile-nav.tsx", "border-r"),
    )
    for path, edge in sites:
        body = path.read_text(encoding="utf-8")
        assert "surface-drawer" in body, (
            f"{path.name} no longer uses .surface-drawer"
        )
        assert edge in body, f"{path.name} lost its {edge} edge border"
        assert not re.search(r"\bshadow-2xl\b", _live_code(body)), (
            f"{path.name} still asks for the theme-blind shadow-2xl"
        )


def _live_code(body: str) -> str:
    """Strip comment lines, so prose quoting a removed class is not matched."""
    return "\n".join(
        line for line in body.splitlines()
        if not line.lstrip().startswith(("//", "*", "/*"))
    )


def test_surface_drawer_supplies_both_depth_cues() -> None:
    """A shadow alone does not register on a near-black page.

    The class exists to pair the drop with a lit top edge and a gradient, which
    is the reasoning recorded in `.surface-raised` above it. Asserting only that
    a shadow is set would allow a regression to the exact flat drop this fixes.
    """
    css = (SRC / "index.css").read_text(encoding="utf-8")
    block = re.search(r"\.surface-drawer\s*\{[^}]*\}", css)
    assert block, ".surface-drawer is not defined"
    body = block.group(0)
    assert "var(--elevate-3)" in body, "no lit top edge or layered drop"
    assert "var(--sheen)" in body, "no gradient -- a bare shadow reads as a hole"


def test_surface_drawer_paints_itself() -> None:
    """The class must be usable without a companion utility.

    An earlier version set only the shadow and the gradient, so it rendered
    transparent unless the call site happened to carry `bg-surface` too. The
    app happened to: both drawers already did. But a depth class that paints
    nothing unless the caller remembers is half-wired, and the style-guide
    specimen written against it came out blank -- which is how the omission was
    found, not by anything in the app.

    `.surface-raised` sets `bg-surface` in its own `@apply`; a surface that
    floats defines what it is made of, not only how it is lit. Matching it here
    also means the specimen in the guide exercises the class on its own rather
    than being propped up by a utility that hides the gap.
    """
    css = (SRC / "index.css").read_text(encoding="utf-8")
    block = re.search(r"\.surface-drawer\s*\{[^}]*\}", css)
    assert block, ".surface-drawer is not defined"
    assert "bg-surface" in block.group(0), (
        ".surface-drawer does not set a background colour, so it renders "
        "transparent unless the call site supplies bg-surface"
    )


def test_the_guide_shows_the_drawer_as_it_ships() -> None:
    """A class absent from the guide is a class the next person reimplements.

    That is how the two drawers came to use `shadow-2xl` while the dialog
    beside them used the correct surface: the vocabulary was documented in one
    place and not the other.

    The specimen must render the class rather than name it, keep the `border-l`
    that distinguishes it from `.surface-raised`, and must *not* carry
    `bg-surface` -- a specimen that props the class up hides exactly the
    half-wiring the previous test forbids.
    """
    guide = (SRC / "pages" / "style-guide.tsx").read_text(encoding="utf-8")
    assert "surface-drawer" in guide, "the style guide does not document it"

    specimen = re.search(r'className="(surface-drawer[^"]*)"', guide)
    assert specimen, "the guide names surface-drawer but does not render it"
    classes = specimen.group(1)
    assert "border-l" in classes, "the specimen lost its edge border"
    assert "bg-surface" not in classes, (
        "the specimen props the class up with a utility it should supply"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
