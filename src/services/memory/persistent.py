"""Memory that outlives the process that wrote it.

EphemeralMemory is named for its lifecycle because the lifecycle is what a
caller has to know. This one is named the same way, and for the same reason:
**records here survive a restart.** The Manager can be rebuilt, the heartbeat
can tick again tomorrow, the machine can be switched off overnight, and what
was remembered is still there.

That matters because of how the heartbeat actually runs. It builds a fresh
Manager on every tick, so an ephemeral store gave the orchestrator a memory
that lasted exactly one wake - it recorded a delegation outcome and then lost
it before it could ever inform anything. Persistence is what makes memory a
faculty rather than a scratch pad.

Design, in order of importance:

- **Recall is not reimplemented.** An EphemeralMemory holds the loaded records
  and answers every query. Composition rather than a second copy of the
  matching logic, so the two implementations cannot drift apart: whatever
  ordering, tie-breaking and filtering EphemeralMemory does, this does too, by
  construction rather than by agreement.
- **Atomic writes**, the same ``.tmp`` then ``os.replace`` StateStore uses.
  An interrupted write cannot leave a half-file behind, which is what lets the
  load path trust what it finds.
- **A damaged file is moved aside, never overwritten.** Losing history is bad;
  losing it silently, with no artefact left to inspect, is worse.

Deliberately absent, each a later decision rather than an oversight: any notion
of a day, of "current" versus "historical", of summarising or consolidating, of
importance, of expiry, and of semantic search. ``created_at`` is preserved on
every record precisely so a temporal layer can be built on top of this without
touching the store - but nothing here interprets it, and recall has no date
filter. This module is the durable foundation, not the daily memory.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord, MemoryStoreError
from src.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 1

# Where a store lives when nobody says otherwise. Under data/, which is
# gitignored - these files hold whatever an agent was told, which is user
# content and must never reach a commit.
DEFAULT_MEMORY_DIR = "data/memory"


class PersistentMemory(Memory):
    """A Memory backed by one JSON file, loaded at construction.

    One file per owner. The Manager gets its own, each specialist gets its
    own, and they cannot see each other's records for the same reason the
    ephemeral ones cannot: they are different objects over different files.
    Nothing here has any concept of a shared store.
    """

    def __init__(self, path: str | Path, records: Iterable[MemoryRecord] = ()) -> None:
        """Load whatever is already on disk, then add ``records``.

        Loading in the constructor is what makes a rebuilt Manager continue
        where the last one stopped. The heartbeat constructs one of these per
        tick against the same path; each construction re-reads the file, so a
        write from the previous wake is visible to the next one without any
        object having to survive in between.
        """
        self._path = Path(path)
        self._index = EphemeralMemory(self._load())

        for record in records or ():
            self.remember(record)

    @property
    def path(self) -> Path:
        return self._path

    def __len__(self) -> int:
        return len(self._index)

    def __repr__(self) -> str:
        return f"<PersistentMemory path={str(self._path)!r} records={len(self._index)}>"

    # ------------------------------------------------------------------
    # The Memory contract
    # ------------------------------------------------------------------

    def remember(self, record: MemoryRecord) -> MemoryRecord:
        """Store a record in memory and on disk, or neither.

        The index is updated first because it performs the validation and the
        duplicate-id check, and there is no point writing a file for a record
        that is about to be rejected. If the write then fails, the record is
        taken back out again: an index that holds something the disk does not
        would report a memory that vanishes on the next restart, which is a
        worse failure than refusing the write outright.
        """
        stored = self._index.remember(record)

        try:
            self._save()
        except MemoryStoreError:
            self._index.forget(stored.id)
            raise

        return stored

    def recall(
        self,
        query: str = "",
        *,
        kind: str | None = None,
        tags: Iterable[str] = (),
        limit: int = 10,
    ) -> tuple[MemoryRecord, ...]:
        """Delegated verbatim, so persistence changes nothing about matching."""
        return self._index.recall(query, kind=kind, tags=tags, limit=limit)

    def forget(self, record_id: str) -> bool:
        removed = self._index.forget(record_id)
        if removed:
            self._save()
        return removed

    def clear(self) -> int:
        removed = self._index.clear()
        # Written even when nothing was removed, so an empty store is a real
        # file rather than an absent one - "cleared" and "never existed" should
        # not look identical on disk.
        self._save()
        return removed

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _load(self) -> list[MemoryRecord]:
        """Read the store, recovering rather than crashing on damage.

        A store that refused to start because its file was damaged would take
        the whole agent down with it. The cost of a bad file is lost history;
        the cost of refusing to run is every future memory as well.
        """
        if not self._path.exists():
            return []

        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            self._quarantine(f"unreadable ({exc})")
            return []

        if not isinstance(raw, dict) or not isinstance(raw.get("records"), list):
            self._quarantine("not a memory store")
            return []

        version = raw.get("version")
        if version != SCHEMA_VERSION:
            self._quarantine(f"schema {version!r} is not {SCHEMA_VERSION}")
            return []

        records: list[MemoryRecord] = []
        skipped = 0
        for entry in raw["records"]:
            restored = self._restore(entry)
            if restored is None:
                skipped += 1
                continue
            records.append(restored)

        if skipped:
            # Named and counted rather than passed over. One unreadable entry
            # should not cost the other nine hundred, but nobody should have to
            # discover the loss by noticing the store got smaller.
            logger.warning(
                "%d unreadable record(s) skipped while loading '%s'; the rest were kept",
                skipped, self._path,
            )
        return records

    @staticmethod
    def _restore(entry: Any) -> MemoryRecord | None:
        """Rebuild one record, or None if this entry cannot be trusted."""
        if not isinstance(entry, dict):
            return None
        try:
            return MemoryRecord(
                content=entry["content"],
                kind=entry.get("kind", ""),
                tags=frozenset(entry.get("tags") or ()),
                context=entry.get("context"),
                created_at=datetime.fromisoformat(entry["created_at"]),
                id=entry["id"],
            )
        except (KeyError, TypeError, ValueError, MemoryStoreError):
            return None

    def _save(self) -> None:
        """Write every record atomically.

        The whole store is rewritten rather than appended to. At this size that
        is simpler and safer - one file is either the old complete store or the
        new complete store, never a mixture - and an append log would need its
        own compaction story to answer forget() and clear().
        """
        # Reversed on purpose. recall() with no query returns most-recent-first
        # (its documented behaviour), and writing that order would reverse the
        # store on every save - so a reloaded store would tie-break differently
        # from the one that wrote it. Reversing restores insertion order, which
        # is what EphemeralMemory treats as "most recent" when scores are equal.
        newest_first = self._index.recall(limit=2**31 - 1)
        payload = {
            "version": SCHEMA_VERSION,
            "records": [self._serialise(record) for record in reversed(newest_first)],
        }

        try:
            # No `default=` fallback. A context that cannot be represented in
            # JSON must fail loudly here; coercing it to str() would store
            # something that is not what the caller handed over and hand back a
            # different value on reload, which is data loss wearing a disguise.
            body = json.dumps(payload, indent=2, sort_keys=True)
        except (TypeError, ValueError) as exc:
            raise MemoryStoreError(
                f"a record could not be serialised for '{self._path}': {exc}"
            ) from exc

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, self._path)
        except OSError as exc:
            # Raised as MemoryStoreError, not the OS error, so callers depend on
            # this package rather than on whatever filesystem sits behind it -
            # the same rule Mailbox and LLMProvider follow.
            raise MemoryStoreError(f"could not write memory to '{self._path}': {exc}") from exc

    @staticmethod
    def _serialise(record: MemoryRecord) -> dict[str, Any]:
        """One record as plain JSON types.

        ``tags`` is sorted rather than left as a set so the file is stable
        between writes - an unordered dump would show spurious differences
        every time and make the store impossible to diff by eye.
        """
        return {
            "id": record.id,
            "content": record.content,
            "kind": record.kind,
            "tags": sorted(record.tags),
            "context": dict(record.context) if record.context is not None else None,
            # Preserved exactly, in full precision with its timezone. The
            # temporal stage that comes later reads this; nothing here does.
            "created_at": record.created_at.isoformat(),
        }

    def _quarantine(self, reason: str) -> None:
        """Move a damaged store aside and start empty.

        Kept, not deleted. A file that cannot be parsed still holds the only
        copy of whatever was remembered, and someone may be able to repair it
        by hand - which they cannot do if starting fresh meant overwriting it.
        """
        backup = self._path.with_suffix(f".corrupt-{int(datetime.now().timestamp())}")
        try:
            self._path.replace(backup)
            logger.warning(
                "memory store at '%s' was %s; moved to '%s' and starting empty",
                self._path, reason, backup,
            )
        except OSError:
            logger.warning("memory store at '%s' was %s and could not be moved aside",
                           self._path, reason)
