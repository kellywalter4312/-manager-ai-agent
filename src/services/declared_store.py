"""One schema-versioned JSON document of declared items - the storage shape
every owner-declared ecosystem store in the GM campaign shares (what is
watched, conditions, groups, zones, bindings, destinations, capture policy,
input bindings, anchors, supervised tasks, sensitivity rules).

Each of those is "a list of things the owner (or Alfred, on the owner's
request) declared, each with its own id", and each used to be the kind of
store this project writes by hand every time: read tolerantly, write
atomically, never lose the whole file to one bad entry. Written once here so
eleven stores do not each carry their own copy - the same reason
``src/services/memory/secrets.py`` exists beside Memory rather than inside a
policy.

**Atomic.** A temporary file, then ``os.replace`` - the identical discipline
``governed_config.py``, ``ledger_store.py`` and ``PersistentMemory`` use, so a
failure part-way through leaves the previous document intact.

**Tolerant, never silent.** A missing file is an empty document (absence is
not an error). A file this code cannot read is reported through ``problem``
and never overwritten by a later save - the Phase 11 rule (P11-CMP-01) that
unknown or damaged persisted state fails safely and is never clobbered.

**Serialised.** Every read-modify-write for one path runs under one
in-process lock, so the live server's request threads and its background
tick never interleave two updates to the same document.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Callable

_LOCKS: "dict[str, threading.RLock]" = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path.resolve()) if path.exists() or path.parent.exists() else str(path)
    with _LOCKS_GUARD:
        lock = _LOCKS.get(key)
        if lock is None:
            lock = _LOCKS[key] = threading.RLock()
        return lock


class DeclaredStoreError(RuntimeError):
    """The document exists but cannot be read, so it is not written over."""


class DeclaredDocument:
    """One ``{"schema_version": N, "<key>": [...]}`` JSON file."""

    def __init__(self, path: "Path | str", *, key: str = "items", schema_version: int = 1) -> None:
        self.path = Path(path)
        self.key = key
        self.schema_version = int(schema_version)
        self.problem = ""

    # -- reading ---------------------------------------------------------

    def load(self) -> "list[dict[str, Any]]":
        """Every item, as stored. Empty when the file does not exist."""
        with _lock_for(self.path):
            return self._load_unlocked()

    def _load_unlocked(self) -> "list[dict[str, Any]]":
        self.problem = ""
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.problem = f"{self.path.name} could not be read ({type(exc).__name__}) - left exactly as it is"
            return []
        if not isinstance(payload, dict):
            self.problem = f"{self.path.name} is not a document this version understands - left as it is"
            return []
        version = payload.get("schema_version", 1)
        if not isinstance(version, int) or version > self.schema_version:
            self.problem = (
                f"{self.path.name} was written by a newer version (schema {version}, "
                f"this reads {self.schema_version}) - left exactly as it is"
            )
            return []
        items = payload.get(self.key, [])
        if not isinstance(items, list):
            self.problem = f"{self.path.name} has no readable '{self.key}' list - left as it is"
            return []
        return [item for item in items if isinstance(item, dict)]

    # -- writing ---------------------------------------------------------

    def save(self, items: "list[dict[str, Any]]") -> None:
        with _lock_for(self.path):
            self._save_unlocked(items)

    def _save_unlocked(self, items: "list[dict[str, Any]]") -> None:
        if self.problem:
            raise DeclaredStoreError(self.problem)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": self.schema_version, self.key: list(items)}
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.path)

    def update(self, change: "Callable[[list[dict[str, Any]]], Any]") -> Any:
        """Read, let ``change`` edit the list in place (returning whatever it
        wants reported), and write - as one step under the lock."""
        with _lock_for(self.path):
            items = self._load_unlocked()
            if self.problem:
                raise DeclaredStoreError(self.problem)
            result = change(items)
            self._save_unlocked(items)
            return result


def slug(text: str, *, limit: int = 48) -> str:
    """A stable, filesystem- and command-safe id from free text."""
    cleaned = "".join(ch if ch.isalnum() else "_" for ch in str(text).strip().lower())
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return cleaned[:limit].strip("_") or "item"
