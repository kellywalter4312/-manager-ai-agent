"""The one concrete artifact *content* schema this checkpoint's vertical
slice actually uses: a real, multi-page-capable document ("handout") -
a title and one or more genuinely structured sections, never one
undifferentiated blob of text.

Deliberately specific to documents. ``ArtifactStore`` (see ``store.py``)
holds no opinion about what any artifact's content actually contains -
a future artifact type (image, video, diagram, ...) gets its own content
schema, read and written by its own tool/renderer, without touching this
module or the store at all. That separation is what keeps the artifact
*system* reusable rather than shaped around documents alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass


@dataclass(frozen=True)
class DocumentSection:
    """One real section of a document - a heading and real body text.
    Never empty: a section with nothing to say is not a section, it is
    an omission, and a renderer given one would have to either fabricate
    filler or silently skip it - this type makes that state
    unrepresentable instead."""

    heading: str
    body: str

    def __post_init__(self) -> None:
        if not self.heading.strip():
            raise ValueError("a document section needs a real heading")
        if not self.body.strip():
            raise ValueError("a document section needs real body text")


@dataclass(frozen=True)
class DocumentContent:
    """A real, structured, multi-page-capable document: a title and an
    ordered sequence of real sections. This is the canonical content a
    "document"/"handout" artifact's ``content_path`` (see
    ``store.py::ArtifactVersion``) actually points at - genuinely
    structured, so a renderer (``src/providers/documents/pdf.py``) can
    lay it out as an actual multi-page document rather than guessing
    where one section ends and the next begins.
    """

    title: str
    sections: tuple[DocumentSection, ...]

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValueError("a document needs a real title")
        if not self.sections:
            raise ValueError("a document needs at least one section")

    def to_json(self) -> str:
        return json.dumps(
            {
                "title": self.title,
                "sections": [{"heading": s.heading, "body": s.body} for s in self.sections],
            }
        )

    @classmethod
    def from_json(cls, data: str) -> "DocumentContent":
        try:
            payload = json.loads(data)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"not valid document content JSON: {exc}") from exc
        if not isinstance(payload, dict):
            raise ValueError("document content JSON must be an object")
        raw_sections = payload.get("sections") or []
        try:
            sections = tuple(
                DocumentSection(heading=str(s["heading"]), body=str(s["body"])) for s in raw_sections
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"malformed document section: {exc}") from exc
        return cls(title=str(payload.get("title", "")), sections=sections)

    def as_plain_text(self) -> str:
        """A flat, readable rendering - used to feed an existing
        version's real content back to the Brain as evidence for a
        revision, and as the honest fallback description when no PDF
        renderer is configured."""
        parts = [self.title, ""]
        for section in self.sections:
            parts.append(section.heading)
            parts.append(section.body)
            parts.append("")
        return "\n".join(parts).strip()
