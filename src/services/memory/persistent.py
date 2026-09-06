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
- **A recognized older schema is carried forward, not discarded.** Governed
  evolution (src/core/versioning.py) lets a future SCHEMA_VERSION bump ship
  with a registered Migration; an older file is then checkpointed and
  upgraded in place rather than quarantined. A version nobody has written a
  Migration for is still refused exactly as before - carrying data forward is
  additive, and never replaces the refusal of a version this store cannot
  trust.
- **A store can also be adopted under a new, stable owner name.**
  `adopt_legacy_store()` is the file-identity counterpart to a schema
  Migration: not the JSON *shape* changing, but *which file* is
  authoritative - the case a caller's own stable technical key was always
  the intended owner, but an owner derived from something mutable (a
  display name) was used instead. Checkpointed, atomic and idempotent by
  the same rules as everything above.

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

from src.core.versioning import Migration, MigrationChain, MigrationUnavailable
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.memory import Memory, MemoryRecord, MemoryStoreError
from src.utils.logging import get_logger

logger = get_logger(__name__)

SCHEMA_VERSION = 2


def _migrate_v1_to_v2(raw: dict[str, Any]) -> dict[str, Any]:
    """Schema 1 -> 2: give every record an explicit ``source`` field.

    Foundation Repair #2 (reconciliation audit, 2026-09-05): provenance had
    always lived informally, by convention, inside whatever a caller chose to
    put in ``context["source"]`` - readable if you knew to look, but nothing
    the schema itself promised. ``MemoryRecord.source`` (see memory.py) makes
    it a first-class field instead. A schema-1 record never had this field at
    all, so the honest migration is the smallest one possible: default it to
    the empty string, exactly what a freshly-constructed record with no
    ``source`` argument already gets. Nothing is inferred from ``context`` -
    a real caller can still read ``context["source"]`` there unchanged, and
    guessing which of an old record's context keys "was" its source would be
    exactly the kind of invention this repair pass was told not to do.

    The first real Migration this project has ever shipped in
    MEMORY_MIGRATIONS, rather than one constructed only inside a test - see
    tests/test_persistent_memory.py::TestRealSchemaMigrationV1ToV2 for the
    proof this actually runs end to end, against this exact function, not a
    monkeypatched stand-in.
    """
    migrated = dict(raw)
    migrated["records"] = [
        {**entry, "source": entry.get("source", "")} if isinstance(entry, dict) else entry
        for entry in raw.get("records", [])
    ]
    migrated["version"] = 2
    return migrated


# Every Migration this store currently knows how to run, keyed by the
# version it starts from - see src/core/versioning.py. Schema 1 -> 2 is the
# first real one (see _migrate_v1_to_v2 above); an older, unrecognised
# version still hits exactly the same quarantine path it always has (see
# _migrate_or_quarantine below) - this constant is where the next schema
# bump registers its own Migration alongside this one.
MEMORY_MIGRATIONS: MigrationChain = MigrationChain(
    [Migration(from_version=1, to_version=2, apply=_migrate_v1_to_v2)]
)

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
            migrated = self._migrate_or_quarantine(raw, version)
            if migrated is None:
                return []
            raw = migrated

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
                source=entry.get("source", ""),
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
            "source": record.source,
        }

    def _migrate_or_quarantine(self, raw: dict[str, Any], version: Any) -> dict[str, Any] | None:
        """Bring a recognized older schema forward, or fall back to exactly
        today's quarantine behavior for anything this store cannot trust.

        Governed evolution, not automatic recovery: a version this store has
        no registered Migration for is refused in precisely the same way,
        with precisely the same message, as an unknown version always has
        been - test_an_unknown_schema_version_is_quarantined covers that and
        is unchanged by this method existing. Only a version this store
        explicitly knows how to carry forward (MEMORY_MIGRATIONS.can_migrate)
        takes the new path below.

        The new path never overwrites the original bytes before they are
        safely elsewhere: the file is renamed aside first (the same
        technique _quarantine() already uses, so a checkpoint is provably as
        safe as quarantine already was), and only then does the migrated
        result get written - atomically, the same tmp-then-replace _save()
        already uses - to the now-free original path. A failure at any point
        in between leaves either the untouched original (under its
        checkpoint name) or the complete migrated result on disk; it can
        never leave a half-written mixture of the two.
        """
        if not isinstance(version, int) or not MEMORY_MIGRATIONS.can_migrate(version, SCHEMA_VERSION):
            self._quarantine(f"schema {version!r} is not {SCHEMA_VERSION}")
            return None

        try:
            migrated = MEMORY_MIGRATIONS.migrate(raw, from_version=version, to_version=SCHEMA_VERSION)
        except MigrationUnavailable as exc:
            self._quarantine(f"schema {version!r} could not be migrated to {SCHEMA_VERSION}: {exc}")
            return None

        if not isinstance(migrated, dict) or not isinstance(migrated.get("records"), list):
            self._quarantine(f"migration from schema {version!r} produced an unusable store")
            return None

        checkpoint = self._path.with_suffix(f".v{version}-checkpoint")
        try:
            self._path.replace(checkpoint)
        except OSError as exc:
            logger.warning(
                "memory store at '%s' could not be checkpointed before migrating from "
                "schema %s; leaving it untouched: %s",
                self._path, version, exc,
            )
            return None

        try:
            body = json.dumps(migrated, indent=2, sort_keys=True)
            temporary = self._path.with_suffix(".tmp")
            temporary.write_text(body, encoding="utf-8")
            os.replace(temporary, self._path)
        except (OSError, TypeError, ValueError) as exc:
            logger.warning(
                "migrated memory for '%s' could not be written; the pre-migration file "
                "is preserved at '%s': %s",
                self._path, checkpoint, exc,
            )
            return None

        logger.info(
            "migrated memory store at '%s' from schema %s to %s; original preserved at '%s'",
            self._path, version, SCHEMA_VERSION, checkpoint,
        )
        return migrated

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


def adopt_legacy_store(
    memory_dir: str | Path, *, stable_owner: str, legacy_owner: str
) -> bool:
    """Bring a store forward from an old owner-derived filename to a new,
    stable one.

    The file-identity counterpart to `_migrate_or_quarantine` above: that
    method carries a store's JSON *shape* forward when the schema inside one
    fixed path changes; this carries a store forward when *which path* is
    authoritative changes, because whatever decided the owner name changed
    underneath it (see ARCHITECTURE.md's entry on stable identity vs.
    display name). Agent-agnostic on purpose - this module has no notion of
    "the Manager" or "IDENTITY.md"; a caller who does supplies both names.

    Deliberately conservative:

    - Does nothing if a file already exists under ``stable_owner``. A real
      store there, however it got there, is never read, merged into or
      overwritten.
    - Does nothing if ``legacy_owner`` and ``stable_owner`` are the same
      string - there is nothing to adopt from.
    - Does nothing if no file exists under ``legacy_owner``.
    - A damaged legacy file is quarantined by PersistentMemory's own,
      already-tested load path (constructing one over the legacy path
      triggers it in full) - this function then has nothing left to adopt,
      and the quarantined file is left exactly where that path already
      leaves it.

    Idempotent by construction, with no separate "already done" flag that
    could fall out of sync with reality: the very next call (the next
    heartbeat tick, the next live-server request) finds the stable file
    already there, and the first check alone makes it a no-op. Safe to call
    unconditionally, every time.

    Returns True only if an adoption actually happened.
    """
    memory_dir = Path(memory_dir)
    stable_path = memory_dir / f"{stable_owner}.json"
    if stable_path.exists():
        return False
    if legacy_owner == stable_owner:
        return False

    legacy_path = memory_dir / f"{legacy_owner}.json"
    if not legacy_path.exists():
        return False

    # Reuses PersistentMemory's own load path in full, including its
    # existing quarantine behavior for a damaged file - not a second,
    # parallel validation of the same JSON shape.
    legacy_store = PersistentMemory(legacy_path)
    if not legacy_path.exists():
        return False  # quarantined during the load above; nothing to adopt

    # Oldest-first, matching the original insertion order: recall() with no
    # query returns newest-first (its documented behaviour), and _save()
    # reverses it for exactly the same reason this does.
    ordered = tuple(reversed(legacy_store.recall(limit=2**31 - 1)))
    payload = {
        "version": SCHEMA_VERSION,
        "records": [PersistentMemory._serialise(record) for record in ordered],
    }

    checkpoint = legacy_path.with_suffix(".pre-stable-owner-checkpoint")
    try:
        legacy_path.replace(checkpoint)
    except OSError as exc:
        logger.warning(
            "legacy store at '%s' could not be checkpointed before adopting it as "
            "'%s'; leaving it untouched: %s",
            legacy_path, stable_path, exc,
        )
        return False

    try:
        body = json.dumps(payload, indent=2, sort_keys=True)
        temporary = stable_path.with_suffix(".tmp")
        temporary.write_text(body, encoding="utf-8")
        os.replace(temporary, stable_path)
    except (OSError, TypeError, ValueError) as exc:
        logger.warning(
            "adopted store for '%s' could not be written; the pre-adoption file is "
            "preserved at '%s': %s",
            stable_path, checkpoint, exc,
        )
        return False

    logger.info(
        "adopted legacy store '%s' as '%s' (%d record(s)); the pre-adoption file is "
        "preserved at '%s'",
        legacy_path, stable_path, len(ordered), checkpoint,
    )
    return True
