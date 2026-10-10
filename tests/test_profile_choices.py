"""The profile choices must come from the registry, not a second hardcoded list.

`admin/services/settings_store.py` declared `choice:quick,service,deep` as a
literal. Nothing tied that string to `core/profiles.py`, so a profile added to
the registry could not be selected: the validator refused the name that the
Settings page's select did not offer, while `get_profile()` -- which accepts
anything in `PROFILES` -- would have run it happily for a scheduled scan set
elsewhere. Three names for one vocabulary, two of them stale.

The choices are now derived. These cases pin that, and pin the
`value:label|description` form that carries each profile's description and,
importantly, whether its port set is knowable -- because that is what decides
if a port rule can apply, and the name alone does not say.
"""
from __future__ import annotations

import re

import pytest

from admin.services.settings_store import (
    PROFILE_CHOICES,
    SETTING_SPECS,
    SettingError,
    _parse_choice,
    coerce,
)
from core.profiles import PROFILES, profile_names


class TestChoicesComeFromTheRegistry:
    def test_the_spec_no_longer_hardcodes_a_list(self) -> None:
        """A literal would rot again the next time a profile is added."""
        for key in ("default_profile", "schedule_profile"):
            _, kind, _ = SETTING_SPECS[key]
            assert kind == PROFILE_CHOICES, (
                f"{key} does not use the derived choices; "
                f"it carries {kind!r}"
            )

    def test_every_profile_is_selectable(self) -> None:
        values = [value for value, _, _ in _parse_choice(PROFILE_CHOICES)]
        missing = set(profile_names()) - set(values)
        assert not missing, (
            f"profiles that exist but cannot be chosen: {sorted(missing)}"
        )

    def test_no_choice_that_is_not_a_profile(self) -> None:
        values = {value for value, _, _ in _parse_choice(PROFILE_CHOICES)}
        unknown = values - set(profile_names())
        assert not unknown, f"choices with no profile behind them: {sorted(unknown)}"


class TestValidatorAcceptsThem:
    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_a_real_profile_validates(self, name: str) -> None:
        """The panel sends the bare name, so the bare name must be accepted.

        A `name:label` entry that was not also registered under `name` would
        make every profile setting fail validation, which shows up as the
        Settings page refusing to save.
        """
        assert coerce("default_profile", name) == name
        assert coerce("schedule_profile", name) == name

    def test_a_nonexistent_profile_is_still_refused(self) -> None:
        with pytest.raises(SettingError, match="must be one of"):
            coerce("default_profile", "insane")

    def test_the_error_lists_names_not_labels(self) -> None:
        """The message is shown to an operator, so it must be readable."""
        with pytest.raises(SettingError) as excinfo:
            coerce("default_profile", "insane")
        message = str(excinfo.value)
        for name in profile_names():
            assert name in message
        # A label leaking into the list makes a 200-character sentence of it.
        assert "Fast scan" not in message


class TestBareChoiceFormStillWorks:
    """`choice:fa,en` and `choice:allow,deny` must be unaffected."""

    @pytest.mark.parametrize(
        ("kind", "good", "bad"),
        [
            ("choice:fa,en", "fa", "de"),
            ("choice:allow,deny", "allow", "maybe"),
        ],
    )
    def test_a_bare_list_behaves_as_before(self, kind: str, good: str,
                                           bad: str) -> None:
        values = [value for value, _, _ in _parse_choice(kind)]
        assert values == good.split(",")[0:1] + good.split(",")[1:] or values
        assert good in values
        assert bad not in values
        # And the label falls back to the value, so the panel has something to
        # render for every setting regardless of whether it was spelled out.
        parsed = dict((value, label) for value, label, _ in _parse_choice(kind))
        assert parsed[good] == good

    def test_coerce_still_works_through_the_real_specs(self) -> None:
        assert coerce("bot_language", "en") == "en"
        assert coerce("rules_default_action", "deny") == "deny"


class TestLabelsCarryThePortFact:
    """The label has to answer "can a port rule apply to this?"."""

    @pytest.mark.parametrize("name", sorted(PROFILES))
    def test_the_port_flag_matches_the_profile(self, name: str) -> None:
        labels = {
            value: label
            for value, label, _ in _parse_choice(PROFILE_CHOICES)
        }
        scoped = PROFILES[name].describes_ports()
        flagged = "port rules cannot apply" in labels[name]
        assert flagged is (not scoped), (
            f"{name}: describes_ports()={scoped} but the label says "
            f"flagged={flagged} -- {labels[name]!r}"
        )

    def test_a_label_never_contains_a_separator(self) -> None:
        """A comma or colon in a label would break the kind for everyone.

        This is why the derivation drops commas rather than trusting profile
        descriptions: operator-facing text must not be able to invalidate a
        validator.
        """
        for value, label, _ in _parse_choice(PROFILE_CHOICES):
            assert "," not in label, f"{value}: label has a comma: {label!r}"
            assert ":" not in label, f"{value}: label has a colon: {label!r}"
            assert label.strip(), f"{value}: empty label"


class TestThePanelCanParseIt:
    """The frontend reads the same string, so its parser has to understand it.

    `settings.tsx` used to carry its own hardcoded list of profile options --
    the third copy of this vocabulary, alongside the settings store's literal
    `choice:quick,service,deep` and the registry itself. Adding a profile
    reached none of them.

    Without this the select would offer a label where the server expects a
    value, and saving would write `quick:Fast scan of the top 100 ports` into
    `default_profile`. The only evidence would be the next scan failing.
    """

    @staticmethod
    def _source() -> str:
        import pathlib

        return (
            pathlib.Path(__file__).resolve().parent.parent
            / "admin/frontend/src/pages/settings.tsx"
        ).read_text(encoding="utf-8")

    def test_the_panel_no_longer_hardcodes_the_profiles(self) -> None:
        source = self._source()
        # The old literal listed these three; deriving them from info.kind is
        # what stops the list rotting again.
        body = source.split("const CHOICES", 1)[1].split("}", 1)[0]
        for name in ("quick", "service", "deep"):
            assert f"value: '{name}'" not in body, (
                f"{name} is still hand-listed in the panel; the options should "
                "come from the server's choice kind"
            )

    def test_the_panel_derives_options_from_the_kind(self) -> None:
        source = self._source()
        assert "parseChoiceKind" in source, (
            "settings.tsx no longer has a kind parser"
        )
        assert "CHOICES[name] ?? parseChoiceKind(info.kind)" in source, (
            "the panel is not falling back to the server's choices, so a new "
            "profile still would not appear"
        )

    def test_the_parser_splits_a_value_from_its_label(self) -> None:
        source = self._source()
        segment = source.split("function parseChoiceKind", 1)[1]
        segment = segment[: segment.index("\n}\n") + 3]
        assert "indexOf(':')" in segment, (
            "the parser never separates a value from its label, so every "
            "option would be submitted as the whole 'value:label' string"
        )
        assert "split('|')" in segment, (
            "the parser does not strip the optional description"
        )