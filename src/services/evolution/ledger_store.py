"""File-backed persistence for ``src.evolution``'s governed proposal
ledger - a pure load/save pair operating *around* that module, never a
change to it, the identical relationship
``src/services/credentials/account_store_file.py`` already has to
``CredentialStore``.

``src.evolution``'s own docstring is explicit that ``EvolutionLedger`` is
"immutable and... not persisted by this module - a caller that needs this
to survive a restart owns that store and that decision." This is that
caller's store: it existed for the account layer, and for the same
reason it must exist here too, or "governed evolution" would reset to
nothing every time the Control Center restarts.

**Two things are persisted, not one.** ``EvolutionLedger`` alone (current
versions + which proposals are active) answers "what is true right now".
It does not answer "what happened, and when" - a REJECTED or ROLLED_BACK
proposal leaves no trace in the ledger at all, by ``src.evolution``'s own
design (only APPLIED/ACTIVE proposals touch it). ``EvolutionState`` below
also keeps every proposal ever seen, by id, and a real, timestamped
``HistoryEntry`` for every governed transition - what "show evolution
history" and "show failures" in the Control Center actually read from.

**Schema-versioned like every other persisted store in this project.**
``SCHEMA_VERSION`` and an (initially empty) ``MigrationChain`` are wired
in now, with the same reasoning ``src/core/versioning.py`` itself states
for doing this ahead of any real migration: "wire the seam in with zero
real migrations yet is a behavior-preserving change rather than a
speculative one." A version that does not match this module's own
``SCHEMA_VERSION`` and has no registered migration path is refused, not
guessed at.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from src.core.versioning import MigrationChain, MigrationUnavailable
from src.evolution import EvolutionLedger, EvolutionProposal, EvolutionStatus, RiskLevel

DEFAULT_LEDGER_PATH = Path("data") / "evolution" / "state.json"

# This store's own on-disk shape - distinct from, and never derived from,
# SYSTEM_VERSION or any other store's SCHEMA_VERSION (src/core/versioning.py's
# own point: each persisted store's shape is its own fact). No migration
# has ever been needed yet, so this chain is genuinely empty - the same
# honest starting point PersistentMemory/StateStore themselves began at.
SCHEMA_VERSION = 1
MIGRATIONS = MigrationChain(())


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class HistoryEntry:
    """One real, timestamped governed transition - what "evolution
    history" in the Control Center actually lists. Recorded for every
    stage this module's own ``ecosystem_evolution.py`` performs,
    including a rejection or a failure - a proposal that never reached
    ACTIVE must still be visible, or "show failures" would have nothing
    to show.
    """

    timestamp: str
    component_id: str
    proposal_id: str
    from_status: str
    to_status: str
    detail: str

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "component_id": self.component_id,
            "proposal_id": self.proposal_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "detail": self.detail,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "HistoryEntry":
        return cls(
            timestamp=payload["timestamp"],
            component_id=payload["component_id"],
            proposal_id=payload["proposal_id"],
            from_status=payload["from_status"],
            to_status=payload["to_status"],
            detail=payload.get("detail", ""),
        )

    def describe(self) -> str:
        return (
            f"{self.timestamp}  {self.component_id:<28} {self.proposal_id:<20} "
            f"{self.from_status} -> {self.to_status}  {self.detail}"
        )


@dataclass(frozen=True)
class EvolutionState:
    """Everything the Control Center's ``ecosystem`` command needs to
    survive a restart: the governed ledger itself, every proposal ever
    seen (by id, at its latest known status), and the full history log.

    Immutable, like every other contract in this project's persistence
    layer - each governed step in ``ecosystem_evolution.py`` produces a
    new ``EvolutionState`` rather than mutating this one, the same
    "read fresh, write whole" discipline ``account_store_file.py``
    already uses.
    """

    ledger: EvolutionLedger = field(default_factory=EvolutionLedger)
    proposals: "dict[str, EvolutionProposal]" = field(default_factory=dict)
    history: tuple[HistoryEntry, ...] = ()

    def with_proposal(self, proposal: EvolutionProposal) -> "EvolutionState":
        proposals = dict(self.proposals)
        proposals[proposal.proposal_id] = proposal
        return EvolutionState(ledger=self.ledger, proposals=proposals, history=self.history)

    def with_ledger(self, ledger: EvolutionLedger) -> "EvolutionState":
        return EvolutionState(ledger=ledger, proposals=dict(self.proposals), history=self.history)

    def with_history(self, entry: HistoryEntry) -> "EvolutionState":
        return EvolutionState(
            ledger=self.ledger, proposals=dict(self.proposals), history=self.history + (entry,)
        )

    def proposal(self, proposal_id: str) -> "EvolutionProposal | None":
        return self.proposals.get(proposal_id)

    def proposals_for(self, component_id: str) -> tuple[EvolutionProposal, ...]:
        return tuple(p for p in self.proposals.values() if p.target == component_id)

    def history_for(self, component_id: "str | None" = None) -> tuple[HistoryEntry, ...]:
        if component_id is None:
            return self.history
        return tuple(entry for entry in self.history if entry.component_id == component_id)


def load_evolution_state(path: "Path | str" = DEFAULT_LEDGER_PATH) -> EvolutionState:
    """A fresh state, populated from ``path`` if it exists.

    A missing file is not an error - a brand-new installation gets an
    honestly empty ledger and history, the same rule every other
    optional persisted store in this project already follows.
    """
    path = Path(path)
    if not path.exists():
        return EvolutionState()

    payload = json.loads(path.read_text(encoding="utf-8"))
    on_disk_version = int(payload.get("schema_version", SCHEMA_VERSION))
    raw = payload.get("data", payload)
    if on_disk_version != SCHEMA_VERSION:
        try:
            raw = MIGRATIONS.migrate(raw, from_version=on_disk_version, to_version=SCHEMA_VERSION)
        except MigrationUnavailable as exc:
            raise MigrationUnavailable(
                f"evolution state at '{path}' is schema version {on_disk_version}, "
                f"this code expects {SCHEMA_VERSION}, and no migration path exists: {exc}"
            ) from exc

    ledger = EvolutionLedger(
        current_versions=raw.get("current_versions", {}),
        active_proposals=tuple(raw.get("active_proposals", ())),
    )
    proposals = {
        proposal_id: EvolutionProposal(
            proposal_id=entry["proposal_id"],
            target=entry["target"],
            current_version=entry["current_version"],
            proposed_version=entry["proposed_version"],
            proposed_change=entry["proposed_change"],
            reason=entry["reason"],
            expected_benefit=entry["expected_benefit"],
            risk_level=RiskLevel(entry["risk_level"]),
            dependencies=tuple(entry.get("dependencies", ())),
            status=EvolutionStatus(entry["status"]),
        )
        for proposal_id, entry in raw.get("proposals", {}).items()
    }
    history = tuple(HistoryEntry.from_dict(entry) for entry in raw.get("history", ()))
    return EvolutionState(ledger=ledger, proposals=proposals, history=history)


def save_evolution_state(state: EvolutionState, path: "Path | str" = DEFAULT_LEDGER_PATH) -> None:
    """Write the whole state to ``path`` - overwriting whatever was
    there, the same "read fresh, write whole" simplicity every other
    persisted store in this project already uses; a partial write can
    never leave the file half-updated and half-stale.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "schema_version": SCHEMA_VERSION,
        "data": {
            "current_versions": dict(state.ledger.current_versions),
            "active_proposals": list(state.ledger.active_proposals),
            "proposals": {
                proposal_id: proposal.to_dict() for proposal_id, proposal in state.proposals.items()
            },
            "history": [entry.to_dict() for entry in state.history],
        },
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
