"""A durable, append-only record of consequential capability actions.

Foundation Repair #4 (reconciliation audit, 2026-09-05): the audit found a
real, complete, persisted history for *governed evolution* transitions
(``src.services.evolution.ledger_store``), and nothing equivalent for an
*ordinary* consequential action - a message actually sent, a document
actually generated, a connection actually acted through. Those were
observable only in the Control Center's own in-memory ``history`` command
(``src.control_center.observer.ControlCenter.history``), which is real but
does not survive a restart, because the object that holds it is rebuilt
fresh every process the same way the Manager itself is.

**What counts as "consequential" here, precisely.** Not "any capability that
happened to touch a tool" - ``ConversationTurn.tool_used`` names a need
declared by the small, separate ``GENERAL_CAPABILITIES`` table (today:
research/web_search and coding/code_assistant), which has nothing to do with
whether ``connected_action``, ``communication`` or ``media_production`` ever
ran. Consequential here means ``ConversationTurn.capability`` is one of
``CONSEQUENTIAL_CAPABILITIES`` (see below) - the small, named set of
capabilities whose own code in capabilities.py only ever returns
``TaskResult.success`` *after* a real ``request.tools.invoke()`` call
reported ``ok``. An ordinary conversational turn, or one of this project's
read-only lookups (``knowledge``, ``live_information``, ``web_search``),
writes nothing here, the same way ``retain_what_was_asked_for`` writes
nothing for an ordinary remark.

**Privacy discipline, unchanged from the rest of this project.**
``MemoryRecord.describe()`` excludes a record's own content from logs on
purpose (see its own docstring); this store follows the identical rule.
An entry names *what kind* of action was attempted, by whom, through which
capability, and whether it succeeded - never the request's words, never a
tool's arguments, never its output. "What did Alfred do" must be
answerable without also reconstructing a private conversation.

**Schema-versioned and append-only**, the same "read fresh, write whole"
discipline ``ledger_store.py`` already uses for exactly the same reason:
a partial write must never leave the file half old and half new.

**What "succeeded" precisely means, and does not.** ``succeeded=True`` is
exact: the capability's own code path proves the tool ran and returned
success. ``succeeded=False`` is honest but coarser: the action did not
complete, which covers both "the tool ran and failed" and "a permission or
authorization check upstream of the tool refused it first, and the tool
was never reached." This log answers "was a consequential action
attempted, and did it complete" - exactly which gate stopped it is what
the ordinary failure message already tells the person in the moment, and
is not duplicated here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src.core.versioning import MigrationChain, MigrationUnavailable
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_ACTION_LOG_PATH = Path("data") / "audit" / "action_log.json"

# This store's own on-disk shape - distinct from, and never derived from,
# any other store's SCHEMA_VERSION (src/core/versioning.py's own point).
SCHEMA_VERSION = 1
MIGRATIONS = MigrationChain(())

# The ManagerCapability names whose own code in src/manager/capabilities.py
# only ever returns TaskResult.success() after a real request.tools.invoke()
# call reported ok - see this module's own docstring for exactly why this,
# and not ConversationTurn.tool_used, is the accurate signal. Deliberately
# excludes this project's read-only lookups (knowledge, live_information,
# web_search): real and governed, but not consequential in the sense Phase 8
# means by the word - nothing external changes because of them.
CONSEQUENTIAL_CAPABILITIES: frozenset[str] = frozenset(
    {"connected_action", "communication", "media_production", "generate_document"}
)

# A hard cap on how many entries a single load keeps in memory / rewrites on
# each append. Bounded for the same reason Memory.recall takes a limit: an
# ever-growing single JSON file rewritten in full on every consequential
# action would make using the system slower the more it was used. Oldest
# entries are dropped first - "the last N consequential actions" is what
# the Control Center actually needs to answer "what did Alfred just do",
# not an unbounded ledger.
MAX_ENTRIES = 500


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ActionLogEntry:
    """One consequential action, attempted - never its content.

    ``capability`` is which ``ManagerCapability`` handled the request (one
    of ``CONSEQUENTIAL_CAPABILITIES`` above, e.g. "connected_action" or
    "communication") - never a specific tool name, because which
    concrete tool answered can vary per connection (see
    ``connected_action``'s own docstring on this) and is not this log's
    concern: the capability is what a person or a governance review would
    ask "did Alfred do X", not which internal tool happened to carry it
    out.
    """

    timestamp: str
    actor: str
    capability: str
    succeeded: bool

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "actor": self.actor,
            "capability": self.capability,
            "succeeded": self.succeeded,
        }

    @classmethod
    def from_dict(cls, payload: dict) -> "ActionLogEntry":
        return cls(
            timestamp=payload["timestamp"],
            actor=payload.get("actor", ""),
            capability=payload.get("capability", ""),
            succeeded=bool(payload.get("succeeded", False)),
        )

    def describe(self) -> str:
        verdict = "ok" if self.succeeded else "failed"
        return f"{self.timestamp}  {self.actor:<12} {self.capability:<24} {verdict}"


def load_action_log(path: "Path | str" = DEFAULT_ACTION_LOG_PATH) -> tuple[ActionLogEntry, ...]:
    """Every entry on disk, oldest first - or an honestly empty tuple for
    a brand-new installation that has never taken a consequential action."""
    path = Path(path)
    if not path.exists():
        return ()

    payload = json.loads(path.read_text(encoding="utf-8"))
    on_disk_version = int(payload.get("schema_version", SCHEMA_VERSION))
    entries = payload.get("entries", ())
    if on_disk_version != SCHEMA_VERSION:
        try:
            migrated = MIGRATIONS.migrate(
                {"entries": entries}, from_version=on_disk_version, to_version=SCHEMA_VERSION
            )
        except MigrationUnavailable as exc:
            raise MigrationUnavailable(
                f"action log at '{path}' is schema version {on_disk_version}, "
                f"this code expects {SCHEMA_VERSION}, and no migration path exists: {exc}"
            ) from exc
        entries = migrated.get("entries", ())
    return tuple(ActionLogEntry.from_dict(entry) for entry in entries)


def record_action(
    entry: ActionLogEntry, path: "Path | str" = DEFAULT_ACTION_LOG_PATH
) -> tuple[ActionLogEntry, ...]:
    """Append one entry and persist the result, bounded to MAX_ENTRIES.

    Read-modify-write, like every other small persisted store in this
    project - the volume here (one entry per consequential action, not per
    conversational turn) is far too low to need anything else.
    """
    existing = list(load_action_log(path))
    existing.append(entry)
    if len(existing) > MAX_ENTRIES:
        existing = existing[-MAX_ENTRIES:]

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "entries": [item.to_dict() for item in existing],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return tuple(existing)
