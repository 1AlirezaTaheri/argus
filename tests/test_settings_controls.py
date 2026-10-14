#!/usr/bin/env python3
"""A setting with no vocabulary must render a field, not an empty Select.

## The bug this pins

On the Settings page, `bot_language` and `default_profile` showed their current
value while `retention_days`, `export_max_scans`, `allowed_cidrs` and
`retention_max_scans_per_target` showed a dark box with a chevron and nothing in
it. Found on a screenshot; invisible to every source-level check, because each
rule involved is correct in isolation.

    const choices = CHOICES[name] ?? parseChoiceKind(info.kind)
    ...
    {choices ? ( <Select .../> ) : ( <Input .../> )}

`parseChoiceKind` returns `[]` for any kind that is not `choice:...`, and `[]` is
truthy in JavaScript. So a setting declared `int:1:3650` or `str` took the Select
branch with an empty item list. Radix's `SelectValue` looks for the item
matching the current value, finds none, and renders nothing.

The server's kinds, from `admin/services/settings_store.py`:

    bot_language            "choice:fa,en"   -> 2 items  -> worked
    default_profile         PROFILE_CHOICES   -> n items  -> worked
    retention_days          "int:1:3650"      -> []       -> empty Select
    export_max_scans        "int:1:500"       -> []       -> empty Select
    allowed_cidrs           "str"             -> []       -> empty Select
    retention_max_scans_per_target "int:1:10000" -> []    -> empty Select

The two that worked are exactly the two with a vocabulary; the four that did not
are exactly the four without one. That correlation is the bug.

## Why the test reads the server's kinds

Asserting only "the condition uses `.length`" would pass against a future
rewrite that moved these settings to `choice:` kinds and reintroduced the same
empty-Select path elsewhere. So the test also reads the kinds from the store and
checks the split still holds: the settings that need the Input branch have no
vocabulary, and the ones that keep the Select have one.
"""
from __future__ import annotations

import pathlib
import re
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
SETTINGS = REPO / "admin" / "frontend" / "src" / "pages" / "settings.tsx"
STORE = REPO / "admin" / "services" / "settings_store.py"

# Settings the screenshot showed rendering an empty box.
NO_VOCABULARY = [
    "retention_days",
    "export_max_scans",
    "allowed_cidrs",
    "retention_max_scans_per_target",
]
# Settings the screenshot showed working.
HAS_VOCABULARY = ["bot_language", "default_profile"]


def _kind_of(name: str) -> str:
    src = STORE.read_text(encoding="utf-8")
    m = re.search(rf'"{re.escape(name)}":\s*\([^,]+,\s*("[^"]*"|[A-Z_]+),', src)
    assert m, f"could not read the kind for {name} from settings_store.py"
    return m.group(1)


def _has_vocabulary(kind: str) -> bool:
    """Whether `parseChoiceKind` would return items for this kind."""
    return kind.startswith('"choice:') or kind == "PROFILE_CHOICES"


def test_the_branch_tests_the_length_not_the_array() -> None:
    """`[]` is truthy, so a truthiness test renders a Select with no items.

    This is the whole bug. `parseChoiceKind` returns `[]` for a kind with no
    vocabulary, and an empty array is truthy, so `choices ?` takes the Select
    branch and Radix renders nothing for a value with no matching item.
    """
    body = SETTINGS.read_text(encoding="utf-8")
    assert re.search(r"\{choices\.length \? \(", body), (
        "the setting control must test choices.length; testing `choices` "
        "renders an empty Select for any kind that parses to []"
    )
    assert not re.search(r"\{choices \? \(", body), (
        "a bare `choices ?` is the bug: [] is truthy and yields an empty Select"
    )


def test_parse_choice_kind_returns_empty_for_a_kind_with_no_vocabulary() -> None:
    """The premise the fix rests on, stated as a fact about the code.

    If `parseChoiceKind` ever returns something for a non-`choice:` kind, the
    length test stops being load-bearing and this test says so rather than
    letting the branch silently become correct for the wrong reason.
    """
    body = SETTINGS.read_text(encoding="utf-8")
    assert "if (!kind.startsWith('choice:')) return []" in body, (
        "parseChoiceKind no longer returns [] for a non-choice kind -- "
        "re-check whether the length test is still what prevents the empty Select"
    )


@pytest.mark.parametrize("name", NO_VOCABULARY)
def test_a_setting_without_a_vocabulary_is_not_a_choice_kind(name: str) -> None:
    """Each empty-looking setting must genuinely have nothing to select from.

    If one of these ever gains a `choice:` kind, it should keep its Select and
    this test should be updated -- not silently start passing.
    """
    assert not _has_vocabulary(_kind_of(name)), (
        f"{name} now has a vocabulary ({_kind_of(name)}); it should render a "
        "Select, so move it to the HAS_VOCABULARY list"
    )


@pytest.mark.parametrize("name", HAS_VOCABULARY)
def test_a_setting_with_a_vocabulary_keeps_its_select(name: str) -> None:
    """The fix must not have turned working Selects into text fields."""
    assert _has_vocabulary(_kind_of(name)), (
        f"{name} no longer has a vocabulary ({_kind_of(name)}); it would lose "
        "its Select, so move it to the NO_VOCABULARY list"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
