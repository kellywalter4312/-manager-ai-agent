"""Persistent memory across heartbeat runs.

Without this the agent has no past: every wake would re-analyse the same inbox
and re-report the same roles, which is noise rather than help.

Two levels of deduplication, because one digest email advertises many roles:

    message level      - a Gmail message id, so a processed email is never
                         analysed twice
    opportunity level  - a fingerprint of role + company + source, so the same
                         posting arriving in next week's digest, or forwarded
                         from a second platform, is recognised as old news

Storage is a single JSON file. Chosen over SQLite deliberately: it is the
smallest thing that works, it can be read and repaired by hand, and it adds no
dependency. If the volume ever outgrows it, SQLite is the upgrade path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

from src.core.versioning import MigrationChain, MigrationUnavailable
from src.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1
DEFAULT_STATE_PATH = "data/state/processed.json"
DEFAULT_RETENTION_DAYS = 90

# Every Migration this store currently knows how to run, keyed by the
# version it starts from - see src/core/versioning.py. Empty today, for the
# same reason src/services/memory/persistent.py's MEMORY_MIGRATIONS is empty:
# schema 1 is still the only schema that has ever existed. A version this
# store has no registered Migration for still falls back to exactly the
# behavior it had before this seam existed (see StateStore.load()).
STATE_MIGRATIONS: MigrationChain = MigrationChain()

_PUNCT = re.compile(r"[^a-z0-9]+")


def _normalise(value: str | None) -> str:
    """Lower-case and strip punctuation so trivial differences do not matter."""
    return _PUNCT.sub(" ", (value or "").strip().lower()).strip()


def fingerprint(opportunity: Any) -> str:
    """A stable identity for one opportunity.

    Built from role, company and source - the things that make a posting *that*
    posting, and which stay constant when a board reformats its emails.

    When those are missing (a vague posting where nothing could be extracted)
    the fallback is the message id plus subject. That still deduplicates the
    same email seen twice, without pretending two unrelated vague postings are
    the same thing.
    """
    role = _normalise(getattr(opportunity, "role", None))
    company = _normalise(getattr(opportunity, "company", None))
    source = _normalise(getattr(opportunity, "source", None))

    if role or company:
        basis = f"{role}|{company}|{source}"
    else:
        basis = (
            f"msg:{getattr(opportunity, 'message_id', '')}"
            f"|{_normalise(getattr(opportunity, 'subject', None))}"
        )

    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:16]


@dataclass
class ProcessedState:
    """What previous runs have already seen."""

    version: int = SCHEMA_VERSION
    last_run_at: str | None = None
    messages: dict[str, str] = field(default_factory=dict)
    opportunities: dict[str, dict[str, str]] = field(default_factory=dict)

    def has_message(self, message_id: str) -> bool:
        return message_id in self.messages

    def has_opportunity(self, key: str) -> bool:
        return key in self.opportunities

    def remember_message(self, message_id: str, when: datetime) -> None:
        self.messages[message_id] = when.isoformat()

    def remember_opportunity(self, key: str, headline: str, when: datetime) -> None:
        existing = self.opportunities.get(key)
        stamp = when.isoformat()
        self.opportunities[key] = {
            "first_seen": existing["first_seen"] if existing else stamp,
            "last_reported": stamp,
            "headline": headline[:120],
        }

    def known_message_ids(self, limit: int = 500) -> tuple[str, ...]:
        """Recent message ids, newest first, for excluding from a fetch.

        Bounded so the exclusion list cannot grow without limit as history
        accumulates.
        """
        ordered = sorted(self.messages.items(), key=lambda kv: kv[1], reverse=True)
        return tuple(message_id for message_id, _ in ordered[:limit])

    def prune(self, before: datetime) -> int:
        """Drop entries older than ``before``. Returns how many were removed."""
        cutoff = before.isoformat()
        removed = 0

        for message_id in [k for k, v in self.messages.items() if v < cutoff]:
            del self.messages[message_id]
            removed += 1

        for key in [
            k for k, v in self.opportunities.items()
            if v.get("last_reported", "") < cutoff
        ]:
            del self.opportunities[key]
            removed += 1

        return removed

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "last_run_at": self.last_run_at,
            "messages": self.messages,
            "opportunities": self.opportunities,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ProcessedState":
        return cls(
            version=int(raw.get("version", SCHEMA_VERSION)),
            last_run_at=raw.get("last_run_at"),
            messages=dict(raw.get("messages") or {}),
            opportunities=dict(raw.get("opportunities") or {}),
        )


class StateStore:
    """Reads and writes ProcessedState, durably."""

    def __init__(
        self,
        path: str | Path = DEFAULT_STATE_PATH,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._path = Path(path)
        self._retention = timedelta(days=retention_days)

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> ProcessedState:
        """Load state, recovering rather than crashing on damage.

        A corrupt state file must never stop the agent from working: the cost
        of losing history is one duplicated report, while the cost of refusing
        to start is every future run. The damaged file is kept for inspection
        instead of being silently overwritten.
        """
        if not self._path.exists():
            return ProcessedState()

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            backup = self._path.with_suffix(f".corrupt-{int(datetime.now().timestamp())}")
            try:
                self._path.replace(backup)
                logger.warning(
                    "state file at '%s' was unreadable (%s); moved to '%s' and "
                    "starting fresh", self._path, exc, backup,
                )
            except OSError:
                logger.warning("state file at '%s' was unreadable: %s", self._path, exc)
            return ProcessedState()

        if not isinstance(raw, dict):
            logger.warning("state file at '%s' is not an object; starting fresh", self._path)
            return ProcessedState()

        # The exact expression ProcessedState.from_dict() uses for a missing
        # key, computed here too so a file with no "version" at all is still
        # treated as current - unchanged from before this method could migrate
        # anything.
        version = int(raw.get("version", SCHEMA_VERSION))
        if version != SCHEMA_VERSION:
            migrated = self._migrate_or_reset(raw, version)
            if migrated is None:
                logger.warning(
                    "state schema %s differs from %s; starting fresh",
                    version, SCHEMA_VERSION,
                )
                return ProcessedState()
            raw = migrated

        return ProcessedState.from_dict(raw)

    def _migrate_or_reset(self, raw: dict[str, Any], version: int) -> dict[str, Any] | None:
        """Bring a recognized older schema forward, or return None so the
        caller falls back to exactly today's behavior: a warning and a
        fresh, empty state.

        Unlike PersistentMemory, this store has never quarantined (moved
        aside) a version it couldn't use - a mismatched file is simply left
        where it was, and the next save() overwrites it. That is existing
        behavior this method does not change for the "no migration
        available" case; only the new migrate path below gets the stronger,
        checkpoint-before-write protection, because that path is new code
        this milestone is adding, not existing behavior being preserved.
        """
        if not STATE_MIGRATIONS.can_migrate(version, SCHEMA_VERSION):
            return None

        try:
            migrated = STATE_MIGRATIONS.migrate(raw, from_version=version, to_version=SCHEMA_VERSION)
        except MigrationUnavailable:
            return None

        if not isinstance(migrated, dict):
            logger.warning(
                "migration from schema %s produced an unusable state for '%s'; starting fresh",
                version, self._path,
            )
            return None

        checkpoint = self._path.with_suffix(f".v{version}-checkpoint")
        try:
            self._path.replace(checkpoint)
        except OSError as exc:
            logger.warning(
                "state file at '%s' could not be checkpointed before migrating from "
                "schema %s; leaving it untouched: %s",
                self._path, version, exc,
            )
            return None

        try:
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(migrated, indent=2, sort_keys=True), encoding="utf-8"
            )
            os.replace(temporary, self._path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "migrated state for '%s' could not be written; the pre-migration file "
                "is preserved at '%s': %s",
                self._path, checkpoint, exc,
            )
            return None

        logger.info(
            "migrated state store at '%s' from schema %s to %s; original preserved at '%s'",
            self._path, version, SCHEMA_VERSION, checkpoint,
        )
        return migrated

    def save(self, state: ProcessedState, now: datetime) -> None:
        """Persist state atomically, pruning old entries first.

        Written to a temporary file and moved into place, so an interrupted
        write can never leave a half-file behind - the reason the load path can
        trust what it finds.
        """
        removed = state.prune(now - self._retention)
        if removed:
            logger.debug("pruned %d expired state entry(ies)", removed)

        state.version = SCHEMA_VERSION
        state.last_run_at = now.isoformat()

        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(state.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        os.replace(temporary, self._path)
        logger.debug(
            "saved state: %d message(s), %d opportunity(ies)",
            len(state.messages), len(state.opportunities),
        )


def utc_now() -> datetime:
    """The default clock. Injected everywhere so tests can control time."""
    return datetime.now(timezone.utc)


def select_unseen(
    opportunities: Iterable[Any], state: ProcessedState
) -> tuple[list[Any], list[Any]]:
    """Split opportunities into (new, already reported)."""
    fresh: list[Any] = []
    seen: list[Any] = []
    for opportunity in opportunities:
        if state.has_opportunity(fingerprint(opportunity)):
            seen.append(opportunity)
        else:
            fresh.append(opportunity)
    return fresh, seen
