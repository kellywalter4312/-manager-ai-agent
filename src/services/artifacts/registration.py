"""A small, shared, reusable way to persist a brand-new artifact's real
content and one real rendition in a single step - used by any tool that
produces a finished, real, user-consumable result
(``ArtifactProductionTool``'s own document/image creation,
``MediaProductionTool``'s own real narration/image/video production),
so each does not reimplement the identical "write content.json, write
the rendition, register it in the store" sequence its own way.

**Why this exists separately from either tool.** Both tools produce
real files through entirely different real providers
(``DocumentRenderer``/``ImageProvider`` for one,
``NarrationProvider``/``ImageProvider``/``VideoComposer``/
``VideoGenerationProvider``/``VideoClipper`` for the other) and have
genuinely different reasons to exist - this module is neither, and
knows about neither. It only ever does the one mechanical thing every
brand-new artifact needs done once its real content and real rendition
bytes already exist.

**Deliberately narrow: creation only.** Revision (a new version of an
*existing* identity) stays each capability's own concern, since what
"revising" means genuinely differs per type (structured content for a
document, a whole new generation call for an image/video) - forcing one
shared ``revise()`` here would blur that real difference rather than
respect it.
"""

from __future__ import annotations

import uuid

from src.services.artifacts.store import Artifact, ArtifactStore


def register_new_artifact(
    store: ArtifactStore,
    *,
    type: str,
    owner_agent: str,
    title: str,
    content_bytes: bytes,
    rendition_format: str,
    rendition_bytes: "bytes | None" = None,
    rendition_path: "str | None" = None,
    related_artifact_ids: "tuple[str, ...]" = (),
) -> Artifact:
    """Write ``content_bytes`` to a brand-new artifact's own directory
    and register it with one real rendition, then register it - the
    identical file layout (``<root>/<artifact_id>/v1/...``) every
    artifact type already uses, so the Control Center/snapshot/frontend
    need no new code to find either file.

    Exactly one of ``rendition_bytes``/``rendition_path`` must be given.
    ``rendition_bytes`` writes a fresh copy under this artifact's own
    directory (the shape ``ArtifactProductionTool`` already uses, where
    no other real copy of the rendition exists anywhere).
    ``rendition_path`` instead *references* an already-real file a
    caller wrote somewhere else (``MediaProductionTool``'s own
    content-addressed ``asset_dir`` - real evidence a later
    objective-loop step may still need at that exact path) - never
    duplicating potentially large media bytes on disk merely to satisfy
    this module's own directory convention.
    """
    if (rendition_bytes is None) == (rendition_path is None):
        raise ValueError("exactly one of rendition_bytes or rendition_path must be given")

    artifact_id = uuid.uuid4().hex[:12]
    version_dir = store.artifact_dir(artifact_id) / "v1"
    version_dir.mkdir(parents=True, exist_ok=True)

    content_path = version_dir / "content.json"
    content_path.write_bytes(content_bytes)

    if rendition_bytes is not None:
        written_path = version_dir / f"rendition.{rendition_format}"
        written_path.write_bytes(rendition_bytes)
        real_rendition_path = written_path.as_posix()
    else:
        real_rendition_path = rendition_path

    return store.create(
        artifact_id=artifact_id,
        type=type,
        owner_agent=owner_agent,
        title=title,
        content_path=content_path.as_posix(),
        renditions={rendition_format: real_rendition_path},
        related_artifact_ids=related_artifact_ids,
    )
