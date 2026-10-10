"""Scan profiles — the Strategy pattern applied to nmap argument sets.

A profile is a *named, fixed* argument list. Users choose a profile by
name and can never contribute arguments themselves, which is what keeps
the CLI surface injection-proof while still exposing 2-3 scan depths.
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownProfileError(ValueError):
    pass


@dataclass(frozen=True)
class ScanProfile:
    name: str
    description: str
    args: tuple[str, ...]
    # Ports this profile will probe, when they can be enumerated.
    #
    # None means "nmap decides": the -F and --top-ports profiles resolve to
    # nmap's own port tables at run time. Hardcoding a copy of those tables
    # here would go stale the moment nmap updated them, so the honest answer
    # is that the port list is unknown — and an unknown port list means port
    # rules cannot be evaluated (see core.rules), because RuleContext.ports is
    # empty and a port rule with nothing to match is skipped.
    #
    # A profile that declares `ports` has its -p flag *derived* from it below,
    # so the list the engine enforces against and the list nmap probes are
    # always the same list.
    ports: tuple[int, ...] | None = None
    # Marks -p as deliberately replacing nmap's own table rather than adding to
    # it. Kept separate from `args` so a reader can see which profiles make the
    # engine's port set knowable.
    explicit_ports: bool = False

    def __post_init__(self) -> None:
        """Derive -p from `ports`, so the two can never disagree.

        `args` is a plain field so that the -F / --top-ports profiles can carry
        their flags directly. For a profile with a port list the flag is built
        here instead, because keeping two hand-written copies of the same list
        would eventually drift -- and the drift would be silent in the worst
        possible direction: the engine enforcing rules against ports nmap never
        probes, or skipping rules for ports it does.

        The value is built from a tuple of ints with no string interpolation of
        operator input, so this stays as injection-proof as a hand-written arg.
        """
        if self.explicit_ports:
            # Checked *before* the no-ports early return below. Declaring
            # explicit_ports without a list would let describes_ports() report
            # True for a profile the engine has no ports for, which is the one
            # confusion this change exists to remove.
            raise ValueError(
                f"profile {self.name!r} sets explicit_ports but has no ports"
            )
        if not self.ports:
            return
        if any(not isinstance(port, int) or not 1 <= port <= 65535
               for port in self.ports):
            raise ValueError(
                f"profile {self.name!r} has a port outside 1-65535"
            )
        ports = ",".join(str(port) for port in self.ports)
        object.__setattr__(self, "args", self.args + ("-p", ports))
        object.__setattr__(self, "explicit_ports", True)

    def port_list(self) -> tuple[int, ...]:
        """Ports the rule engine can be told about."""
        return self.ports or ()

    def describes_ports(self) -> bool:
        """Whether port rules can be evaluated against this profile.

        Surfaces in the admin API so the panel can say *why* a port rule is
        inert instead of leaving the operator to guess.
        """
        return bool(self.ports)


# Fixed port tables. Module constants rather than inline literals so the
# families are readable on their own and the overlap between them is visible --
# `common` is deliberately the union of the four specific lists plus the
# handful that belong to none of them.
#
# Every value is an int in a tuple. Nothing here is parsed from, formatted
# from, or otherwise influenced by user input; the -p string is derived from
# these tuples in ScanProfile.__post_init__ and nowhere else.
COMMON_ONLY_PORTS = (21, 53, 111, 135, 139, 445, 636, 1883, 2049, 5672)

WEB_PORTS = (80, 81, 443, 591, 593, 2082, 2083, 2086, 2087, 2095, 2096,
             3000, 3128, 4443, 5000, 7001, 8000, 8001, 8008, 8009, 8080,
             8081, 8088, 8090, 8118, 8123, 8172, 8181, 8222, 8333, 8443,
             8500, 8834, 8888, 9000, 9043, 9060, 9080, 9090, 9091, 9200,
             9443, 9810, 9981, 12443, 16080, 18091, 18092, 20720, 28017)

MAIL_PORTS = (25, 110, 143, 465, 587, 993, 995, 2525)

DATABASE_PORTS = (1433, 1521, 3306, 5432, 5984, 5986, 6379, 7000, 9042,
                  9200, 9300, 11211, 15672, 27017, 27018)

REMOTE_PORTS = (22, 23, 3389, 5900, 5901, 5902, 5903, 5985, 5986)

# The union, computed rather than retyped. `common` is the profile to reach for
# when a port rule has to apply and the family is not known in advance; keeping
# it derived means adding a port to a family puts it here too, instead of
# creating a fourth hand-maintained list that quietly omits it.
COMMON_PORTS = tuple(sorted(
    set(WEB_PORTS) | set(MAIL_PORTS) | set(DATABASE_PORTS)
    | set(REMOTE_PORTS) | set(COMMON_ONLY_PORTS)
))

# The names of the port-scoped profiles, derived. A test and the admin API both
# want this as a collection rather than recomputing the predicate, and having
# one definition stops the two from disagreeing.
PORT_SCOPED = (
    "common", "database", "mail", "remote", "web",
)


# Fixed catalogs — no user input ever reaches the argument list.
PROFILES: dict[str, ScanProfile] = {
    "quick": ScanProfile(
        name="quick",
        description="Fast scan of the top 100 ports",
        args=("-F", "-T4"),
    ),
    "service": ScanProfile(
        name="service",
        description="Top 100 ports with service/version detection",
        args=("-F", "-T4", "-sV", "--version-intensity", "2"),
    ),
    "deep": ScanProfile(
        name="deep",
        description="Top 1000 ports with service detection",
        args=("-T4", "-sV", "--top-ports", "1000"),
    ),

    # --- profiles with a knowable port set ---------------------------------
    #
    # Each of these replaces nmap's own port table with an explicit -p list,
    # which is what makes `allow_port` / `deny_port` rules applicable. They
    # exist because the three above cannot be scoped by rules: their port set
    # is whatever the installed nmap decides, and the engine is not told it.
    #
    # The lists are the well-known assignments for each service family, kept
    # short on purpose -- a long list is a slow scan, and the operator who
    # wants a particular unusual port can ask for it with a `deny_port` rule
    # against the broader `common` profile rather than paying for it here.
    "web": ScanProfile(
        name="web",
        description="Web-facing ports only",
        ports=WEB_PORTS,
        args=("-T4", "-sV", "--version-intensity", "2"),
    ),
    "mail": ScanProfile(
        name="mail",
        description="SMTP, IMAP and POP, with submission and webmail",
        ports=MAIL_PORTS,
        args=("-T4", "-sV", "--version-intensity", "2"),
    ),
    "database": ScanProfile(
        name="database",
        description="Relational, document and cache databases",
        ports=DATABASE_PORTS,
        args=("-T4", "-sV", "--version-intensity", "2"),
    ),
    "remote": ScanProfile(
        name="remote",
        description="Remote access: SSH, Telnet, RDP, VNC",
        ports=REMOTE_PORTS,
        args=("-T4", "-sV", "--version-intensity", "2"),
    ),
    "common": ScanProfile(
        name="common",
        description=(
            "An explicit list of the most commonly scanned service ports. "
            "Slow, but the one to use when a port rule has to apply."
        ),
        ports=COMMON_PORTS,
        args=("-T4", "-sV", "--version-intensity", "2"),
    ),
}

DEFAULT_PROFILE = "service"


def get_profile(name: str | None) -> ScanProfile:
    """Resolve a profile by name, falling back to the default."""
    key = (name or DEFAULT_PROFILE).strip().lower()
    if key not in PROFILES:
        known = ", ".join(sorted(PROFILES))
        raise UnknownProfileError(
            f"Unknown profile '{name}'. Available: {known}"
        )
    return PROFILES[key]


def profile_names() -> list[str]:
    return sorted(PROFILES)


def port_scoped_profiles() -> list[str]:
    """Profiles whose port set the rule engine can enforce against.

    The panel shows this beside the port-rule editor, because the difference
    between "my rule is wrong" and "my rule cannot apply here" is otherwise
    invisible: both produce no denial.
    """
    return sorted(PORT_SCOPED)