"""Port rules must be able to apply, which needs a profile that declares ports.

The bug this pins: `ScanProfile.ports` was `None` for all three shipped
profiles, because they use nmap's own `-F` / `--top-ports`. `port_list()`
therefore returned `()` for every scan, the scan pipeline passed that empty
tuple into `RuleContext.ports`, and `allow_port` / `deny_port` rules were
skipped -- silently, because "no ports in the context" is a documented code path
rather than an error.

The user-visible symptom was twofold: there was no way to scan a port they
chose, and a port rule they had written appeared to do nothing.

Three things are asserted, because all three could break independently:

  * the profile's `-p` flag and its `ports` tuple agree -- if they drift, the
    engine enforces rules against ports nmap never probes;
  * the port-scoped profiles hand a non-empty list to the engine;
  * the `-F` profiles still hand an empty one, because their port set really is
    not knowable and pretending otherwise would be a lie the engine acts on.
"""
from __future__ import annotations

import pytest

from core import profiles as P
from core.rules import RuleContext


class TestPortArgsAreDerived:
    """-p must be built from `ports`, never written beside it."""

    @pytest.mark.parametrize("name", sorted(P.PORT_SCOPED))
    def test_the_flag_matches_the_tuple(self, name: str) -> None:
        profile = P.PROFILES[name]
        want = ",".join(str(port) for port in profile.ports or ())
        assert profile.args[-2:] == ("-p", want), (
            f"{name}: args carry {profile.args[-2:]!r} but ports says {want!r}"
        )

    @pytest.mark.parametrize("name", sorted(P.PORT_SCOPED))
    def test_ports_are_unique_and_sorted(self, name: str) -> None:
        """A duplicate or unsorted list is not harmful, but it is a mistake.

        `common` is a union of four tables, so a repeat would mean one of them
        was edited carelessly and the same port is being named twice.
        """
        ports = P.PROFILES[name].ports or ()
        assert len(ports) == len(set(ports)), f"{name} repeats a port"
        assert list(ports) == sorted(ports), f"{name} is not sorted"

    def test_common_covers_every_specific_family(self) -> None:
        """`common` is meant to be the union, so it must actually be one.

        It is derived with a set union rather than retyped. This pins the
        property the derivation is there to provide: adding a port to `web`
        must put it in `common` without a second edit.
        """
        common = set(P.COMMON_PORTS)
        for family in (P.WEB_PORTS, P.MAIL_PORTS, P.DATABASE_PORTS,
                       P.REMOTE_PORTS, P.COMMON_ONLY_PORTS):
            missing = set(family) - common
            assert not missing, f"common is missing {sorted(missing)}"


class TestProfilesCarryUsablePorts:
    def test_the_shipped_profiles_without_a_port_list_are_unchanged(self) -> None:
        """The three original profiles must keep deferring to nmap.

        Giving them a copied port table would go stale against the installed
        nmap, and their whole reason for existing is that they track it.
        """
        for name in ("quick", "service", "deep"):
            profile = P.PROFILES[name]
            assert profile.ports is None, f"{name} gained a hardcoded port list"
            assert profile.port_list() == ()
            assert "-p" not in profile.args
            assert profile.describes_ports() is False

    @pytest.mark.parametrize("name", sorted(P.PORT_SCOPED))
    def test_a_port_scoped_profile_reports_its_ports(self, name: str) -> None:
        profile = P.PROFILES[name]
        assert profile.port_list(), f"{name} claims to be port-scoped but is not"
        assert profile.describes_ports() is True

    def test_port_scoped_profiles_matches_the_flag(self) -> None:
        declared = set(P.port_scoped_profiles())
        actual = {
            name for name, profile in P.PROFILES.items()
            if profile.describes_ports()
        }
        assert declared == actual

    def test_the_widely_wanted_ports_are_reachable(self) -> None:
        """The complaint was that *certain* ports could not be scanned.

        Named here so a later edit to the tables cannot quietly drop the ports
        people actually reach for: SSH, Telnet, RDP, VNC and the web tier.
        """
        common = set(P.PROFILES["common"].port_list())
        for port in (22, 23, 80, 443, 3389, 5900):
            assert port in common, f"port {port} is not scannable"

        remote = set(P.PROFILES["remote"].port_list())
        for port in (22, 23, 3389, 5900):
            assert port in remote, f"remote-access port {port} is missing"


class TestPortsReachTheEngine:
    def test_a_port_scoped_profile_hands_ports_to_the_context(self) -> None:
        profile = P.PROFILES["common"]
        ctx = RuleContext(
            target="192.0.2.10",
            target_key="probe",
            ports=profile.port_list(),
        )
        assert ctx.ports, (
            "the engine received no ports, so every port rule is skipped -- "
            "this is the defect being fixed"
        )
        assert 23 in ctx.ports

    def test_an_f_profile_hands_an_empty_context(self) -> None:
        """Honest emptiness, not a silent gap.

        The engine treats an empty port list as "cannot evaluate", which is the
        correct answer for a profile whose ports are nmap's business.
        """
        ctx = RuleContext(
            target="192.0.2.10",
            target_key="probe",
            ports=P.PROFILES["service"].port_list(),
        )
        assert ctx.ports == ()


class TestConstructionIsGuarded:
    """The derivation rejects nonsense rather than emitting a broken -p."""

    def test_a_port_outside_range_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            P.ScanProfile(name="bad", description="d", args=("-T4",),
                          ports=(80, 70000))

    def test_a_zero_port_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1-65535"):
            P.ScanProfile(name="bad", description="d", args=("-T4",), ports=(0,))

    def test_explicit_ports_without_a_list_is_refused(self) -> None:
        """The flag cannot be claimed for a profile that has no ports."""
        with pytest.raises(ValueError, match="explicit_ports"):
            P.ScanProfile(name="bad", description="d", args=("-T4",),
                          explicit_ports=True)

    def test_a_profile_without_ports_keeps_its_own_args(self) -> None:
        """__post_init__ must not rewrite a profile that declares no ports."""
        profile = P.ScanProfile(name="x", description="d", args=("-F", "-T4"))
        assert profile.args == ("-F", "-T4")
        assert profile.explicit_ports is False

    def test_explicit_ports_is_derived_not_declarable(self) -> None:
        """A caller cannot claim a port list that was never given.

        Otherwise `describes_ports()` could report True for a profile the engine
        has no ports for, and the panel would advertise a rule that cannot
        apply -- the exact confusion this change exists to remove.

        The guard has to sit *before* the no-ports early return in
        __post_init__, or this is unreachable: the first version of that method
        returned first, so the flag was silently dropped and describes_ports()
        would have lied.
        """
        with pytest.raises(ValueError, match="explicit_ports"):
            P.ScanProfile(name="x", description="d", args=("-F",),
                          explicit_ports=True)