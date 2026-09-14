"""The one governed way a file the *user* supplies becomes a canonical
artifact - a PDF, a document, a spreadsheet, an image, a recording, a
video - whichever door it came through (a conversation, the Control
Center, a terminal).

**One mechanism, never one upload system per type.** Every supplied file
becomes a ``supplied_file`` artifact in the identical, authoritative
``ArtifactStore`` every produced artifact already lives in, written by the
identical shared ``register_new_artifact`` helper. What a capability can
*do* with it (read its text, analyse its image, clip its video) is that
capability's own decision - see ``src/services/artifacts/content.py`` for
the generalized "content -> usable units" reading - never a branch here.

**The received bytes are the source of truth, and stay untouched.** The
file is stored byte-for-byte as supplied, as the artifact's one rendition.
Editing or converting it later always produces a *new*, derived artifact
linked back to it (``related_artifact_ids``), never an in-place rewrite -
the same "source of truth stays read-only" rule Memory already lives under.

**Identity is the content hash.** Supplying the same bytes to the same
agent twice returns the artifact already registered rather than a
duplicate, so a person re-attaching a file never ends up with two copies
that could drift apart in a later answer.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import PurePosixPath, PureWindowsPath

from src.services.artifacts.models import SuppliedFileContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import Artifact, ArtifactError, ArtifactStore

SUPPLIED_FILE_TYPE = "supplied_file"

# A generous but real bound: a person's own timetable, notes or a short
# video must fit; an accidental multi-gigabyte upload must be refused
# honestly rather than exhaust memory in the one server process.
MAX_SUPPLIED_FILE_BYTES = 25 * 1024 * 1024

_MAX_FILENAME_CHARS = 120
_SAFE_EXTENSION = re.compile(r"^[a-z0-9]{1,8}$")
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")

# Kinds of content, keyed by format - how a capability decides whether a
# supplied file is something it can work with. Kinds, never subjects: there
# is no "timetable" or "lecture" here and there must never be one. A format
# this does not name is still accepted, and is honestly "other".
_KIND_BY_EXTENSION: dict[str, str] = {
    **{ext: "document" for ext in ("pdf", "txt", "text", "md", "markdown", "csv", "xlsx", "pptx", "docx", "rtf")},
    **{ext: "image" for ext in ("png", "jpg", "jpeg", "gif", "webp", "bmp", "heic")},
    # Pre-Stage-9 Campaign 7: a saved web page and an e-book are documents.
    **{ext: "document" for ext in ("html", "htm", "xhtml", "epub")},
    **{ext: "audio" for ext in ("wav", "mp3", "m4a", "ogg", "webm", "aac", "flac")},
    **{ext: "video" for ext in ("mp4", "mov", "mkv", "avi")},
}


class IngestionError(ValueError):
    """A supplied file that cannot honestly be accepted - empty, too large,
    or unnamed. Raised before anything is written."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_filename(name: object) -> str:
    """Only the final name component, with control characters removed and
    a bounded length - never a path a caller could use to point anywhere.
    Both separator styles are stripped, because a browser on one operating
    system may report a name shaped by another."""
    raw = str(name or "")
    base = PureWindowsPath(PurePosixPath(raw).name).name
    cleaned = _CONTROL_CHARS.sub("", base).strip().strip(".")
    if len(cleaned) > _MAX_FILENAME_CHARS:
        stem, dot, ext = cleaned.rpartition(".")
        cleaned = (stem[: _MAX_FILENAME_CHARS - len(ext) - 1] + dot + ext) if dot else cleaned[:_MAX_FILENAME_CHARS]
    return cleaned


def extension_of(filename: str) -> str:
    _, dot, ext = filename.rpartition(".")
    ext = ext.lower() if dot else ""
    return ext if _SAFE_EXTENSION.match(ext) else "bin"


def content_kind_for(extension: str) -> str:
    return _KIND_BY_EXTENSION.get(extension.lower(), "other")


def ingest_supplied_file(
    store: ArtifactStore,
    *,
    owner_agent: str,
    filename: object,
    data: bytes,
    source: str,
    media_type: str = "",
) -> "tuple[Artifact, bool]":
    """Register ``data`` as a supplied-file artifact owned by
    ``owner_agent``. Returns ``(artifact, created)`` - ``created`` is False
    when the identical bytes were already supplied to this same agent, in
    which case the existing artifact is returned unchanged.

    Raises ``IngestionError`` for an empty, oversized or nameless file,
    before anything touches disk.
    """
    if not str(owner_agent or "").strip():
        raise IngestionError("a supplied file must be owned by a real agent")
    if not isinstance(data, (bytes, bytearray)) or not data:
        raise IngestionError("the file is empty - nothing was received")
    if len(data) > MAX_SUPPLIED_FILE_BYTES:
        raise IngestionError(
            f"the file is {len(data)} bytes, larger than the {MAX_SUPPLIED_FILE_BYTES}-byte "
            "limit a supplied file may have"
        )
    name = safe_filename(filename)
    if not name:
        raise IngestionError("a supplied file needs a real filename")
    origin = " ".join(str(source or "").split())[:40]
    if not origin:
        raise IngestionError("a supplied file must say where it came from")

    digest = hashlib.sha256(bytes(data)).hexdigest()
    for existing in store.all():
        if (
            existing.type == SUPPLIED_FILE_TYPE
            and existing.owner_agent == owner_agent
            and existing.latest.metadata.get("sha256") == digest
        ):
            return existing, False

    extension = extension_of(name)
    kind = content_kind_for(extension)
    media = " ".join(str(media_type or "").split())[:100]
    content = SuppliedFileContent(
        filename=name,
        media_type=media,
        sha256=digest,
        size_bytes=len(data),
        source=origin,
        ingested_at=_utc_now(),
    )
    try:
        artifact = register_new_artifact(
            store,
            type=SUPPLIED_FILE_TYPE,
            owner_agent=owner_agent,
            title=name,
            content_bytes=content.to_json().encode("utf-8"),
            rendition_format=extension,
            rendition_bytes=bytes(data),
            metadata={
                "filename": name,
                "media_type": media,
                "sha256": digest,
                "size_bytes": str(len(data)),
                "source": origin,
                "content_kind": kind,
            },
        )
    except ArtifactError as exc:
        raise IngestionError(str(exc)) from exc
    return artifact, True
