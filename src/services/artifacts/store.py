"""The one authoritative, persistent record of every real artifact
Alfred's work has produced - agent-independent, type-independent, and
shared: the same store a capability's tool writes through and the
Control Center reads from, the identical "one shared instance" role
``Runtime.presence_registry`` already plays for presence.

**Identity, versioning and location - nothing about content.** This
module has no idea what a "document" or a future "image"/"video"
artifact actually contains; it only ever manages a stable
``artifact_id``, an ordered chain of real ``ArtifactVersion`` records,
and where each version's real content and renditions live on disk. A
revision is a new version under the same identity, never a new,
unrelated artifact - the version chain a caller needs to answer "is
this the same handout, revised" honestly.

**File-backed, loaded once, written through on every mutation** - the
identical discipline ``PersistentMemory`` already holds itself to
(``src/services/memory/persistent.py``), for the identical reason: a
fresh instance (a new process, or a fresh per-run rebuild) must see
exactly what the last one wrote, and a crash right after a write must
never lose it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


class ArtifactError(RuntimeError):
    """Raised for a genuine artifact-store misuse - a duplicate id, or a
    revision to an artifact that does not exist."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class ArtifactVersion:
    """One real, immutable version of an artifact's content - never
    edited in place. A revision always produces a new one; nothing here
    is ever overwritten."""

    version: int
    title: str
    status: str  # "ready" | "failed" - honest, never fabricated
    created_at: datetime
    content_path: str
    renditions: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, str] = field(default_factory=dict)
    error: str = ""

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ArtifactError("an ArtifactVersion's version must be at least 1")
        if self.status not in ("ready", "failed"):
            raise ArtifactError(f"unknown artifact status {self.status!r}")

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "title": self.title,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "content_path": self.content_path,
            "renditions": dict(self.renditions),
            "metadata": dict(self.metadata),
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "ArtifactVersion":
        return cls(
            version=int(data["version"]),
            title=str(data["title"]),
            status=str(data["status"]),
            created_at=datetime.fromisoformat(str(data["created_at"])),
            content_path=str(data["content_path"]),
            renditions=dict(data.get("renditions") or {}),
            metadata=dict(data.get("metadata") or {}),
            error=str(data.get("error", "")),
        )


@dataclass(frozen=True)
class Artifact:
    """One real artifact's stable identity plus its complete, ordered
    version history (oldest first) - never collapsed to "the latest
    version", the same "keep every step, not just a summary" discipline
    ``ObjectiveOutcome`` already holds itself to for a pursued
    objective's steps."""

    artifact_id: str
    type: str
    owner_agent: str
    versions: tuple[ArtifactVersion, ...]

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ArtifactError("an Artifact must have a real artifact_id")
        if not self.versions:
            raise ArtifactError("an Artifact must have at least one version")

    @property
    def latest(self) -> ArtifactVersion:
        return self.versions[-1]

    def to_dict(self) -> dict:
        return {
            "artifact_id": self.artifact_id,
            "type": self.type,
            "owner_agent": self.owner_agent,
            "versions": [v.to_dict() for v in self.versions],
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "Artifact":
        return cls(
            artifact_id=str(data["artifact_id"]),
            type=str(data["type"]),
            owner_agent=str(data["owner_agent"]),
            versions=tuple(ArtifactVersion.from_dict(v) for v in data["versions"]),
        )


class ArtifactStore:
    """Loads whatever is already on disk at construction, then accepts
    real ``create``/``revise`` calls - each one persisted immediately,
    never batched, so a crash the instant after a write can lose at most
    nothing.

    ``root`` holds both the small index file (``artifacts.json``,
    metadata and paths only - never the real content or rendered bytes)
    and, per artifact, the real content/rendition files a caller (an
    ``ArtifactProductionTool``) writes directly under
    ``artifact_dir(artifact_id)``. Splitting it this way keeps the index
    small and fast to rewrite whole even as real, possibly large,
    rendered files accumulate - the identical reason
    ``MediaProductionTool`` writes real asset bytes to their own files
    rather than inlining them into any index.
    """

    def __init__(self, root: "Path | str") -> None:
        self._root = Path(root)
        self._index_path = self._root / "artifacts.json"
        self._artifacts: dict[str, Artifact] = {}
        self._load()

    def artifact_dir(self, artifact_id: str) -> Path:
        """Where this artifact's real content/rendition files live -
        never interpreted by this class, only located."""
        return self._root / artifact_id

    def create(
        self,
        *,
        artifact_id: str,
        type: str,
        owner_agent: str,
        title: str,
        content_path: str,
        renditions: Mapping[str, str] = (),
        metadata: Mapping[str, str] = (),
        status: str = "ready",
        error: str = "",
    ) -> Artifact:
        """Register a brand-new artifact at version 1. ``artifact_id`` is
        caller-supplied, never invented here - the identical "names come
        from the caller" discipline ``ProviderAccount.account_id``
        already holds itself to."""
        artifact_id = artifact_id.strip()
        if not artifact_id:
            raise ArtifactError("artifact_id must not be empty")
        if artifact_id in self._artifacts:
            raise ArtifactError(f"an artifact with id {artifact_id!r} already exists")
        if not type.strip():
            raise ArtifactError("type must not be empty")
        if not owner_agent.strip():
            raise ArtifactError("owner_agent must not be empty")

        version = ArtifactVersion(
            version=1,
            title=title,
            status=status,
            created_at=_now(),
            content_path=content_path,
            renditions=dict(renditions),
            metadata=dict(metadata),
            error=error,
        )
        artifact = Artifact(
            artifact_id=artifact_id, type=type.strip(), owner_agent=owner_agent, versions=(version,)
        )
        self._artifacts[artifact_id] = artifact
        self._save()
        return artifact

    def revise(
        self,
        artifact_id: str,
        *,
        title: "str | None" = None,
        content_path: str,
        renditions: Mapping[str, str] = (),
        metadata: Mapping[str, str] = (),
        status: str = "ready",
        error: str = "",
    ) -> Artifact:
        """Add a new version to an artifact that already exists - the
        same identity, one version further along. Raises ``ArtifactError``
        honestly when the id names nothing, rather than silently
        creating a new, disconnected artifact under a caller's typo."""
        key = artifact_id.strip()
        existing = self._artifacts.get(key)
        if existing is None:
            raise ArtifactError(f"no artifact found with id {artifact_id!r}")

        new_version = ArtifactVersion(
            version=existing.latest.version + 1,
            title=title if title is not None else existing.latest.title,
            status=status,
            created_at=_now(),
            content_path=content_path,
            renditions=dict(renditions),
            metadata=dict(metadata),
            error=error,
        )
        revised = Artifact(
            artifact_id=existing.artifact_id,
            type=existing.type,
            owner_agent=existing.owner_agent,
            versions=existing.versions + (new_version,),
        )
        self._artifacts[existing.artifact_id] = revised
        self._save()
        return revised

    def get(self, artifact_id: str) -> "Artifact | None":
        return self._artifacts.get(str(artifact_id).strip())

    def all(self) -> tuple[Artifact, ...]:
        """Every artifact, in no particular guaranteed order - a caller
        that wants a specific ordering (newest first, for a listing)
        sorts it, the same "this store makes no presentation choices"
        boundary ``CredentialStore`` already holds for accounts."""
        return tuple(self._artifacts.values())

    def find_latest(
        self, *, owner_agent: "str | None" = None, type: "str | None" = None
    ) -> "Artifact | None":
        """The most recently updated artifact matching the given
        filters, or None - real, deterministic evidence a capability can
        use to resolve "revise the handout" with no explicit id named,
        never a guess among several equally-plausible candidates hidden
        behind an arbitrary tie-break."""
        candidates = [
            a
            for a in self._artifacts.values()
            if (owner_agent is None or a.owner_agent == owner_agent)
            and (type is None or a.type == type)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda a: a.latest.created_at)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._index_path.exists():
            return
        try:
            payload = json.loads(self._index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A damaged index recovers to an honestly empty store rather
            # than taking the whole agent down - the identical
            # "the cost of a bad file is lost history; the cost of
            # refusing to run is every future artifact as well" reasoning
            # PersistentMemory._load already documents.
            return
        for entry in payload.get("artifacts", []):
            try:
                artifact = Artifact.from_dict(entry)
            except (KeyError, ValueError, TypeError, ArtifactError):
                continue
            self._artifacts[artifact.artifact_id] = artifact

    def _save(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        payload = {"artifacts": [a.to_dict() for a in self._artifacts.values()]}
        self._index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
