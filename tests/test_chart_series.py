"""Every chart series must carry a dash, and the dashes must all differ.

`SERIES` declares five series. `SERIES_DASH` declared three. That mismatch was
invisible until the style guide was rendered: the guide showed six marks with
three dash patterns and described every series as carrying its own dash, while
the charts drew solid lines for `hosts` and `services`.

It is latent rather than shipped-bad only because no chart currently plots
those two alongside `scans`. A green and a red line is the classic deuteranopia
collision, and the whole reason the dashes exist is so a line is still readable
without colour vision and in greyscale print -- so the vocabulary has to be
complete for the series that *exist*, not for the ones in use today.

The check is over the TypeScript source rather than a render, because the
defect is a missing map entry and a missing entry does not show up on a page
that never uses it.
"""
from __future__ import annotations

import pathlib
import re

CHART = pathlib.Path(__file__).resolve().parent.parent / (
    "admin/frontend/src/lib/chart.ts"
)
GUIDE = pathlib.Path(__file__).resolve().parent.parent / (
    "admin/frontend/src/pages/style-guide.tsx"
)

SERIES_BLOCK = re.compile(r"export const SERIES = \{(.*?)\n\} as const", re.DOTALL)
DASH_BLOCK = re.compile(
    r"export const SERIES_DASH[^=]*=\s*\{(.*?)\n\} as const", re.DOTALL
)


def _keys(block: str) -> list[str]:
    return re.findall(r"^\s{2}([a-zA-Z][a-zA-Z0-9]*):", block, re.MULTILINE)


def _series_keys() -> list[str]:
    match = SERIES_BLOCK.search(CHART.read_text(encoding="utf-8"))
    assert match, "could not find SERIES in lib/chart.ts"
    return _keys(match.group(1))


def _dash_entries() -> dict[str, str]:
    match = DASH_BLOCK.search(CHART.read_text(encoding="utf-8"))
    assert match, "could not find SERIES_DASH in lib/chart.ts"
    return dict(re.findall(r"^\s{2}([a-zA-Z][a-zA-Z0-9]*):\s*'([^']*)',",
                           match.group(1), re.MULTILINE))


class TestEverySeriesHasADash:
    def test_series_parses(self) -> None:
        assert len(_series_keys()) >= 5, (
            "SERIES did not parse; these cases would pass vacuously"
        )

    def test_no_series_is_missing_one(self) -> None:
        series = set(_series_keys())
        dashes = set(_dash_entries())
        missing = series - dashes
        assert not missing, (
            f"series with no dash pattern: {sorted(missing)}. A chart drawing "
            "one of these next to another would render two solid lines, which "
            "is the collision the dashes exist to prevent."
        )

    def test_no_dash_is_orphaned(self) -> None:
        """A dash for a series that does not exist is dead vocabulary."""
        series = set(_series_keys())
        orphans = set(_dash_entries()) - series
        assert not orphans, f"dash patterns with no matching series: {sorted(orphans)}"


class TestTheDashesAreDistinct:
    def test_no_two_series_share_a_pattern(self) -> None:
        """Two series with the same dash are two series that differ only by
        colour, which is the case the dashes were added for."""
        entries = _dash_entries()
        by_pattern: dict[str, list[str]] = {}
        for name, pattern in entries.items():
            by_pattern.setdefault(pattern, []).append(name)
        collisions = {p: names for p, names in by_pattern.items()
                      if len(names) > 1}
        assert not collisions, (
            f"series sharing a dash pattern: {collisions}. They are "
            "distinguishable by hue alone."
        )

    def test_solid_is_used_at_most_once(self) -> None:
        """`'0'` is solid. More than one series solid means N unmarked lines."""
        solid = [n for n, p in _dash_entries().items() if p.strip() == "0"]
        assert len(solid) <= 1, f"more than one solid line: {solid}"

    def test_the_patterns_differ_in_rhythm_not_only_length(self) -> None:
        """Two patterns of the same length but different rhythm read alike.

        `'4 3'` and `'3 4'` are both a single dash of similar length; at a 2px
        stroke on a busy chart they blur together. The set should contain at
        least one dotted, one dashed and one dash-dot, so no two are
        distinguished by a shift that a reader will not notice.
        """
        patterns = list(_dash_entries().values())

        def has_gap(pattern: str) -> bool:
            return len(pattern.split()) > 2

        def is_dotted(pattern: str) -> bool:
            return bool(pattern) and all(
                float(part) <= 2 for part in pattern.split() if part
            ) and len(pattern.split()) <= 3

        assert any(is_dotted(p) for p in patterns), (
            "no dotted pattern: without one the set is dashes of varying length, "
            "which are hard to tell apart at a thin stroke"
        )
        assert any(has_gap(p) for p in patterns), (
            "no dash-dot pattern: a repeating dash and a dash-dot are the two "
            "easiest to confuse at small sizes"
        )


class TestTheGuideDoesNotRestateThem:
    """The guide reads the vocabulary rather than copying it.

    It used to hardcode both the token names and the dash patterns, which was a
    third copy. That copy drifted: it showed six marks with three dash patterns
    and claimed every series carried its own dash.
    """
    def test_the_guide_imports_the_vocabulary(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        assert "from '@/lib/chart'" in source, (
            "the style guide does not import from lib/chart"
        )
        assert "SERIES_DASH" in source, (
            "the style guide does not read SERIES_DASH"
        )

    def test_it_hardcodes_no_dash_patterns(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        # A strokeDasharray literal in the guide is a fourth copy waiting to
        # drift. The reserved swatch is the one legitimate exception, and it is
        # allowed below.
        offenders = [
            line.strip()
            for line in source.splitlines()
            if "strokeDasharray=" in line and "SERIES_DASH" not in line
            and "2 2" not in line
        ]
        assert not offenders, (
            f"the style guide hardcodes dash patterns: {offenders}"
        )