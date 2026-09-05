"""Can Alfred OS move safely from what is installed to a proposed target?

This is the question Stage 6's Upgrade Manifest cannot answer by itself.
The manifest says what *is* installed; this module says whether a
*different*, requested installation is reachable from here - without ever
making it so.

    Current System (an UpgradeManifest, Stage 6)
          |
          v
    UpgradeRequest  (a proposed target)
          |
          v
    plan_upgrade()
          |
          v
    UpgradePlan { status, reason, required_migrations }

**This is a planner, not an executor.** Nothing in this module writes a
file, renames a file, calls a Migration's ``apply``, changes an agent's
memory owner, switches a provider, or rebuilds a visual environment.
``required_migrations`` *names* the Migration steps a real upgrade would
need to run - src/core/versioning.py's own ``MigrationChain.path()``,
read-only - it never runs one. A future, separately-governed Upgrade
Executor is what would act on a plan this module returns; it does not
exist yet, and this module does not pretend it does.

**Five states, chosen to mean something, not to fill in a checklist:**

    INVALID              the request itself is malformed - a blank version
                          string, a non-positive schema number, or a
                          visual environment surface this installation
                          does not have. Not a judgement about safety; the
                          question could not even be asked.
    CURRENT               the requested target matches what is already
                          installed in every dimension asked about.
                          Nothing to do.
    UPGRADE_AVAILABLE     the target differs, but nothing about the
                          difference requires a data migration - a
                          SYSTEM_VERSION change and/or a visual environment
                          version change, neither of which any
                          MigrationChain in this codebase governs, because
                          neither transforms persisted data.
    MIGRATION_REQUIRED    the target differs, and a complete, known
                          migration path exists for every schema that
                          changed. Safe, and precisely nameable.
    UNSUPPORTED            the target differs, and at least one requested
                          schema change has no complete known migration
                          path. Fails closed - the exact "1->2 known, 2->3
                          missing" case must land here, never be attempted.

**System version and visual environment compatibility, deliberately
minimal.** Neither SYSTEM_VERSION nor a Visual Environment Registry
version has a migration contract anywhere in this codebase - no
semantic-version resolver exists, and this module does not invent one.
Absence of a contract is not, by itself, treated as a safety risk: fail-
closed (UNSUPPORTED) exists specifically to stop an *incomplete data
migration* from running, and neither of these two fields ever asks a
store to transform anything. A request that changes only these two
fields is therefore UPGRADE_AVAILABLE, not UNSUPPORTED - reported
honestly as "no contract, and no risk", never silently assumed safe by
omission the way a real schema gap must never be.

**Reuses, never re-derives.** Schema compatibility is answered by asking
the exact MigrationChain instances PersistentMemory and StateStore already
maintain (MEMORY_MIGRATIONS, STATE_MIGRATIONS) - the same registries
Stages 1-5's real migrations already run through - not a second copy of
"which versions are compatible" logic that could drift from what actually
happens when a migration runs. Visual environment versions are compared
against Stage 6's own UpgradeManifest.visual_environments, never a second
registry.

**Agent identity and provider independence.** This module imports nothing
from src.registry, src.services.identity, src.composition, or src.providers,
and takes no agent, name, or provider anywhere in its inputs. A plan can
never be, or become, a statement about which agent exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

from src.control_center.manifest import UpgradeManifest
from src.core.versioning import MigrationChain
from src.heartbeat.state import STATE_MIGRATIONS
from src.services.memory.persistent import MEMORY_MIGRATIONS


class UpgradeStatus(str, Enum):
    """Inherits str for the same reason src.core.contracts.TaskStatus
    does: a plain, JSON-serialisable value and a closed set at once."""

    INVALID = "invalid"
    CURRENT = "current"
    UPGRADE_AVAILABLE = "upgrade_available"
    MIGRATION_REQUIRED = "migration_required"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class UpgradeRequest:
    """A proposed target, never an instruction to act.

    Every field is optional. ``None`` (or an empty mapping, for the
    visual-environment field) means "no change requested for this
    dimension" - not "invalid". A request with everything omitted is
    well-formed and resolves to CURRENT: "is what's already installed
    still what's installed?" is a legitimate question to ask.

    ``target_visual_environment_versions`` is keyed by the same
    ``surface`` string UpgradeManifest.visual_environments already uses
    ("agent:core", "agent:polygon", "brain", "memory", "room") - not an
    agent name, and not a second vocabulary for naming a surface.
    """

    target_system_version: str | None = None
    target_memory_schema_version: int | None = None
    target_state_schema_version: int | None = None
    target_visual_environment_versions: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class RequiredMigration:
    """One step a real upgrade would need to run - named, not performed.

    ``store`` is "memory" or "state" today, the same two owners
    MEMORY_MIGRATIONS/STATE_MIGRATIONS already govern; a future third
    versioned store extends this the same way it extends those two
    registries, not by widening this module's own vocabulary.
    """

    store: str
    from_version: int
    to_version: int


@dataclass(frozen=True)
class UpgradePlan:
    """The planner's whole answer: a status, why, and - only when
    MIGRATION_REQUIRED - exactly which steps would run, in order."""

    status: UpgradeStatus
    reason: str
    required_migrations: tuple[RequiredMigration, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "reason": self.reason,
            "required_migrations": [
                {"store": m.store, "from_version": m.from_version, "to_version": m.to_version}
                for m in self.required_migrations
            ],
        }


def _malformed(current: UpgradeManifest, request: UpgradeRequest) -> str | None:
    """The one place a request is checked for well-formedness, before any
    compatibility question is asked of it."""
    if request.target_system_version is not None and not request.target_system_version.strip():
        return "target_system_version, if given, must not be blank"

    for label, value in (
        ("target_memory_schema_version", request.target_memory_schema_version),
        ("target_state_schema_version", request.target_state_schema_version),
    ):
        if value is not None and value < 1:
            return f"{label} must be a positive integer, got {value!r}"

    known_surfaces = {entry.surface for entry in current.visual_environments}
    for surface, version in request.target_visual_environment_versions.items():
        if surface not in known_surfaces:
            return f"'{surface}' is not a visual environment surface this installation has"
        if version < 1:
            return f"target version for visual environment '{surface}' must be a positive integer, got {version!r}"

    return None


def _schema_plan(
    store: str,
    chain: MigrationChain,
    current_version: int,
    target_version: int | None,
) -> tuple[bool, tuple[RequiredMigration, ...], str | None]:
    """One persisted store's contribution to the overall plan.

    Returns (changed, required_migrations, blocked_reason). ``changed`` is
    False only when no target was requested or the target already matches
    what is installed.
    """
    if target_version is None or target_version == current_version:
        return False, (), None

    if not chain.can_migrate(current_version, target_version):
        return True, (), (
            f"{store} schema {current_version} -> {target_version} has no complete "
            f"registered migration path"
        )

    steps = tuple(
        RequiredMigration(store=store, from_version=m.from_version, to_version=m.to_version)
        for m in chain.path(current_version, target_version)
    )
    return True, steps, None


def plan_upgrade(current: UpgradeManifest, request: UpgradeRequest) -> UpgradePlan:
    """Decide whether ``request`` is reachable from ``current``, without
    performing anything.

    Deterministic and read-only: the same manifest and the same request
    always produce the same plan, and no file, store, agent, provider or
    visual environment is touched, written, renamed or rebuilt by calling
    this function - not even the migration steps it names.
    """
    problem = _malformed(current, request)
    if problem:
        return UpgradePlan(status=UpgradeStatus.INVALID, reason=problem)

    blocked: list[str] = []
    required: list[RequiredMigration] = []
    # Differences with no migration/data-transformation concept at all -
    # never blocking, because there is nothing here that could leave data
    # half-transformed. Named, so UPGRADE_AVAILABLE's reason can say what
    # actually differs rather than a generic "something changed".
    unmigrated_differences: list[str] = []

    if request.target_system_version is not None and (
        request.target_system_version != current.system_version
    ):
        unmigrated_differences.append(
            f"system version {current.system_version!r} -> {request.target_system_version!r}"
        )

    installed_visuals = {entry.surface: entry.version for entry in current.visual_environments}
    for surface, target_version in request.target_visual_environment_versions.items():
        installed_version = installed_visuals[surface]  # validated present by _malformed
        if target_version != installed_version:
            unmigrated_differences.append(
                f"visual environment '{surface}' {installed_version} -> {target_version}"
            )

    for store, chain, current_version, target_version in (
        ("memory", MEMORY_MIGRATIONS, current.memory_schema_version, request.target_memory_schema_version),
        ("state", STATE_MIGRATIONS, current.state_schema_version, request.target_state_schema_version),
    ):
        _changed, steps, blocked_reason = _schema_plan(store, chain, current_version, target_version)
        required.extend(steps)
        if blocked_reason:
            blocked.append(blocked_reason)

    if blocked:
        return UpgradePlan(status=UpgradeStatus.UNSUPPORTED, reason="; ".join(blocked))
    if required:
        return UpgradePlan(
            status=UpgradeStatus.MIGRATION_REQUIRED,
            reason=f"{len(required)} migration(s) must run to reach the requested target",
            required_migrations=tuple(required),
        )
    if unmigrated_differences:
        return UpgradePlan(
            status=UpgradeStatus.UPGRADE_AVAILABLE,
            reason=(
                "differs with no data migration required, and none is assumed safe by "
                "guessing where none is defined: " + "; ".join(unmigrated_differences)
            ),
        )
    return UpgradePlan(
        status=UpgradeStatus.CURRENT,
        reason="the requested target matches what is already installed",
    )
