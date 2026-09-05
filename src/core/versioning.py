"""The vocabulary of governed evolution: what "version" means, and how one
version becomes the next one on purpose.

Three different things in this codebase are already called a "version", and
keeping them apart is the whole point of this module existing:

    SYSTEM_VERSION       the assembled product - "what is Alfred OS today".
                          A human-chosen string, bumped when the system as a
                          whole changes in a way worth naming. Lives here.

    SNAPSHOT_VERSION      the *shape* of one interface's read-only export
                          (src/control_center/snapshot.py). It describes a
                          format contract for something regenerated fresh on
                          every read; nothing is ever loaded-and-migrated
                          against it, so it needs no migration machinery.

    SCHEMA_VERSION         each persisted store's own on-disk shape
                          (src/services/memory/persistent.py,
                          src/heartbeat/state.py, one constant per store).
                          This is what a Migration below moves between.

A Migration is deliberately small: one deterministic function from an older
raw dict to the next raw dict, nothing else. It does not open a file, does
not know a Path, and does not decide whether to run - a store decides that,
using the checkpoint-before-write discipline documented where each store
wires this in. This module only answers "is there a way from A to B, and
what is it", the same way src/core/contracts.py only answers "what shape does
a Task have" without deciding when one is created.

**What this module is not.** It is not an upgrade runner, not a rollback
executor, and not a compatibility validator - those are governed operations
with their own safety story, layered on top of this in a later milestone.
This is the seam they will be built against, kept intentionally small: two
classes and one exception, both pure, both fully deterministic, both provably
free of every kind of I/O.

**Implementation upgrade vs identity change.** A Migration exists to carry
one agent's *persisted state* forward when the code that reads it changes -
a Brain implementation, a Memory backend, a 3D visual environment version.
None of that is this module's business either: nothing here reads an agent's
name, key, or registry entry, and nothing here can rename anything. That is
a deliberate absence, not an oversight - see ARCHITECTURE.md's entry on this
milestone for why an implementation upgrade and a deliberate rename must stay
two different operations, only one of which this module ever touches.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

# The assembled product's own version - not a subsystem's SCHEMA_VERSION, not
# the interface's SNAPSHOT_VERSION. A human-chosen string (semantic-versioned
# by convention, not by any code here) bumped when the system as a whole
# changes in a way worth naming to a person looking at a status screen.
# Nothing in this codebase derives it from anything else, and nothing should:
# deriving it from, say, the memory schema version would conflate "one store
# changed shape" with "the product moved forward", which are different facts.
SYSTEM_VERSION = "0.1.0"


class MigrationUnavailable(Exception):
    """Raised when no ordered path of registered Migrations reaches the
    target version from the version on hand.

    This is the exact case a caller must treat exactly like an unknown or
    corrupt version today - refuse and preserve, never guess. It is not a
    bug in the chain; most of the time it correctly means "nobody has
    written a migration for this yet."
    """


@dataclass(frozen=True)
class Migration:
    """One deterministic step: a raw dict at ``from_version`` becomes a raw
    dict at ``to_version``.

    Single-step and gapless on purpose (``to_version`` must be exactly
    ``from_version + 1``): a migration that jumped several versions at once
    would be doing several people's undocumented work in one function, and
    the day version 2 needs its own fix, someone would have to split a
    2-to-4 migration apart under pressure instead of adding a 3-to-4 one
    beside an existing 2-to-3. ``apply`` receives one raw dict and returns
    one raw dict - no file, no Path, no owner name, so the exact same
    function can be unit-tested with a plain literal and reused by any
    store that happens to share a version number, without either store's
    on-disk format being exposed to the other.
    """

    from_version: int
    to_version: int
    apply: Callable[[dict], dict]

    def __post_init__(self) -> None:
        if self.to_version != self.from_version + 1:
            raise ValueError(
                f"Migration must be a single, gapless step: "
                f"from_version={self.from_version!r}, to_version={self.to_version!r}"
            )


class MigrationChain:
    """An ordered set of Migrations for one store's schema, keyed by the
    version each one starts from.

    Holds nothing but the migrations themselves - no store, no path, no
    version currently on disk anywhere. A chain with nothing registered
    (the state every existing store is wired with today) can migrate only
    from a version to itself; every other request correctly raises
    MigrationUnavailable, which is what makes "wire the seam in with zero
    real migrations yet" a behavior-preserving change rather than a
    speculative one.
    """

    def __init__(self, migrations: "tuple[Migration, ...] | list[Migration]" = ()) -> None:
        by_from: dict[int, Migration] = {}
        for migration in migrations:
            if migration.from_version in by_from:
                raise ValueError(
                    f"duplicate migration registered from version {migration.from_version}"
                )
            by_from[migration.from_version] = migration
        self._by_from = by_from

    def _steps(self, from_version: int, to_version: int) -> list[Migration]:
        """The ordered Migrations from ``from_version`` to ``to_version``,
        or raise MigrationUnavailable - the one place both public methods
        agree on what "a path exists" means."""
        if to_version < from_version:
            raise MigrationUnavailable(
                f"cannot migrate backward from version {from_version} to {to_version}"
            )
        steps: list[Migration] = []
        version = from_version
        while version != to_version:
            step = self._by_from.get(version)
            if step is None:
                raise MigrationUnavailable(
                    f"no migration registered from version {version} "
                    f"(on the way from {from_version} to {to_version})"
                )
            steps.append(step)
            version = step.to_version
        return steps

    def can_migrate(self, from_version: int, to_version: int) -> bool:
        """Whether an ordered path exists - the check a caller makes before
        deciding to attempt a migration at all, so a store can log an honest
        "no migration available" without touching a single byte on disk."""
        if from_version == to_version:
            return True
        try:
            self._steps(from_version, to_version)
        except MigrationUnavailable:
            return False
        return True

    def path(self, from_version: int, to_version: int) -> tuple[Migration, ...]:
        """The ordered Migrations migrate() would run for this exact
        (from_version, to_version) pair - without running any of them.

        The read-only counterpart to migrate(), for a caller that needs to
        *name* what would happen rather than make it happen (a planner, not
        an executor - see src/control_center/upgrade_planner.py). Raises
        MigrationUnavailable under exactly the same conditions migrate()
        would; call can_migrate() first to check without a try/except.
        """
        return tuple(self._steps(from_version, to_version))

    def migrate(self, raw: dict, *, from_version: int, to_version: int) -> dict:
        """Walk ``raw`` forward from ``from_version`` to ``to_version``.

        Shallow-copies before every step, so a migration that does the
        ordinary thing - ``current["records"] = [...]``, replacing a
        top-level key - can never affect the caller's original dict or an
        earlier step's result. That matters because a caller's original is
        very likely also the exact dict about to be preserved as an
        on-disk checkpoint; this is real safety for the common case, not a
        formality. It is not a deep copy: a migration that reaches inside a
        nested list or dict and mutates it in place, instead of building a
        replacement, is responsible for its own purity, same as anywhere
        else in this codebase a caller receives a mutable value.

        Raises only ``MigrationUnavailable`` - never whatever a
        ``Migration.apply`` happened to raise, and never lets one return
        something that is not a dict. That is a deliberate, narrow
        contract: every store that calls this catches exactly one
        exception type and falls back to its own existing quarantine
        behavior, so a bug in a future migration function degrades to "this
        version could not be trusted" - the same safe, tested outcome as an
        unrecognized version - rather than an uncaught exception taking the
        whole load path (and the agent constructing it) down.
        """
        if from_version == to_version:
            return dict(raw)

        current = dict(raw)
        for step in self._steps(from_version, to_version):
            try:
                current = step.apply(dict(current))
            except Exception as exc:
                raise MigrationUnavailable(
                    f"migration from version {step.from_version} to {step.to_version} "
                    f"raised {type(exc).__name__}: {exc}"
                ) from exc
            if not isinstance(current, dict):
                raise MigrationUnavailable(
                    f"migration from version {step.from_version} to {step.to_version} "
                    f"did not return a dict (got {type(current).__name__})"
                )
        return current
