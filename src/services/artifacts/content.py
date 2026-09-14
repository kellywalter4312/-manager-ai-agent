"""Content -> usable units: the one generalized way an artifact's real text
becomes something a capability can read aloud, quote, reason over, edit or
convert - whatever kind of content it is and wherever it came from.

    supplied PDF / document / spreadsheet / presentation  (supplied_file)
    Alfred's own structured document                       (document)
                        |
                        v
            ContentUnit(locator, text), in document order
                        |
          +-------------+--------------+----------------+
          |             |              |                |
       read aloud    reasoning      personal       edit / convert
      (voice path)  (knowledge)     context        (derived artifact)

**PDF is one source, never the architecture.** A supplied file is read by
the identical per-format reader the user's knowledge folder already uses
(``DocumentKnowledgeProvider.passages``) - a PDF's units are its pages, a
spreadsheet's its rows, a presentation's its slides. A document Alfred
produced is read from its own canonical structured content, one unit per
section. A future content type adds one branch in ``read_artifact_units``;
nothing that *consumes* units changes.

**Honest about what has no text.** A recording or a video has no text units
here - its speech needs a transcription capability, and this module never
pretends otherwise. An image's units are the printed text actually recognised
in it (``src/providers/media/local_ocr.py``, Pre-Stage-9 Campaign 7) - an image
with no recognisable text, or a machine with no recogniser, honestly yields
nothing; what the picture *shows* is still a vision question. A scanned PDF
with no text layer honestly yields nothing.

**Deterministic, no model.** Everything here - reading, locating "page 3",
choosing which units answer a question - is literal and repeatable, so it
behaves identically in Half Brain. Summarising or explaining the units is
the Brain's job, one layer up.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from src.core.contracts import tokenize
from src.services.artifacts.ingestion import SUPPLIED_FILE_TYPE
from src.services.artifacts.models import DocumentContent
from src.services.artifacts.store import Artifact
from src.services.memory.query import STOPWORDS

# Words that shape a *request about* content rather than naming what it is
# about - never counted as relevance overlap. Request vocabulary only.
_REQUEST_WORDS = frozenset({
    "this", "that", "these", "those", "other", "both", "document", "documents", "pdf", "pdfs",
    "file", "files", "page", "pages", "section", "sections", "compare", "summarize", "summarise",
    "read", "say", "says", "what", "does", "tell", "about", "explain", "attachment", "handout",
})

DOCUMENT_TYPE = "document"

# The artifact types whose content is text a person could have read to
# them. Kinds of artifact, never subjects.
TEXT_CONTENT_TYPES: tuple[str, ...] = (SUPPLIED_FILE_TYPE, DOCUMENT_TYPE)

# How a file shared in a conversation is recorded in the room (written by the
# upload route, read by the shared-files accessor) - one constant, two sides.
SHARED_FILE_PREFIX = "[shared a file]"

ARTIFACT_ID_PATTERN = re.compile(r"\bartifact[:\s]+([0-9a-f]{6,32})\b", re.IGNORECASE)

# "page 4", "section 2", "slide 3", "row 10", "paragraph 5" - the unit
# names the readers above actually produce, nothing else.
LOCATOR_PATTERN = re.compile(r"\b(page|section|slide|row|paragraph)\s+(\d{1,4})\b", re.IGNORECASE)

# Phrases that refer to a piece of content already in play rather than to a
# topic. Format nouns and pointing words only - "this pdf", "the other
# document" - never a subject a document might be about.
CONTENT_REFERENCE_MARKERS: tuple[str, ...] = (
    "this pdf", "the pdf", "that pdf", "this document", "that document", "the document",
    "this file", "that file", "the file", "this attachment", "the attachment",
    "this spreadsheet", "this presentation", "this handout", "the handout",
    "the other document", "the other pdf", "the other file", "both documents",
    "these documents", "summarize this", "summarise this",
)

_TWO_SOURCE_MARKERS: tuple[str, ...] = (
    "other document", "other pdf", "other file", "both documents", "these documents", "compare",
)

# Bounds - mechanics of reading, never anything that could express a topic.
DEFAULT_EVIDENCE_CHARS = 6000
READ_ALOUD_CHARS = 2500


@dataclass(frozen=True)
class ContentUnit:
    locator: str
    text: str

    def to_dict(self) -> dict:
        return {"locator": self.locator, "text": self.text}


def _marker_present(text: str, markers: Sequence[str]) -> bool:
    lowered = " ".join(str(text or "").lower().split())
    return any(marker in lowered for marker in markers)


def read_artifact_units(artifact: Artifact) -> tuple[ContentUnit, ...]:
    """Every real text unit of ``artifact``'s latest version, in order.
    Empty for any artifact with no readable text - never a fabricated
    description standing in for content that is not there."""
    latest = artifact.latest
    if artifact.type == DOCUMENT_TYPE:
        try:
            doc = DocumentContent.from_json(Path(latest.content_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return ()
        return tuple(
            ContentUnit(locator=f"section {index}", text=f"{section.heading}: {section.body}")
            for index, section in enumerate(doc.sections, start=1)
        )
    if artifact.type == SUPPLIED_FILE_TYPE:
        from src.providers.document_knowledge_provider import DEFAULT_SUFFIXES, DocumentKnowledgeProvider

        from src.providers.media.local_ocr import DEFAULT_OCR_READER, OCRError

        for rendition_path in latest.renditions.values():
            path = Path(rendition_path)
            if DEFAULT_OCR_READER.reads(path):
                # Pre-Stage-9 Campaign 7 (RD-04): an image's units are the
                # lines of printed text recognised in it, top to bottom.
                if not DEFAULT_OCR_READER.available():
                    continue
                try:
                    lines = DEFAULT_OCR_READER.lines(path)
                except OCRError:
                    continue
                if lines:
                    return tuple(
                        ContentUnit(locator=f"paragraph {index}", text=line)
                        for index, line in enumerate(lines, start=1)
                    )
                continue
            # Only a format the reader genuinely understands is read. The
            # reader treats anything it does not recognise as plain text,
            # so an image, a recording or a .docx must never reach it - it
            # would come back as binary noise presented as "text".
            if path.suffix.lower() not in DEFAULT_SUFFIXES:
                continue
            passages = DocumentKnowledgeProvider(path.parent).passages(path)
            if passages:
                return tuple(ContentUnit(locator=loc, text=text) for loc, text in passages)
        return ()
    return ()


def artifact_ids_in(lines: Iterable[str]) -> tuple[str, ...]:
    """Every ``artifact <id>`` mentioned in ``lines``, most recent line
    first, each id once - how a capability resolves "this PDF" to the file
    that was actually shared in this conversation."""
    seen: list[str] = []
    for line in reversed(tuple(lines)):
        for match in reversed(ARTIFACT_ID_PATTERN.findall(str(line))):
            key = match.lower()
            if key not in seen:
                seen.append(key)
    return tuple(seen)


def content_reference_intent(text: str) -> bool:
    """Whether ``text`` points at content already in play - a format noun
    with a pointing word, an explicit artifact id, or a unit locator."""
    return bool(
        _marker_present(text, CONTENT_REFERENCE_MARKERS)
        or ARTIFACT_ID_PATTERN.search(str(text or ""))
    )


def wants_two_sources(text: str) -> bool:
    return _marker_present(text, _TWO_SOURCE_MARKERS)


def locator_in(text: str) -> "str | None":
    """The unit locator a request names ("page 4"), normalised, or None."""
    match = LOCATOR_PATTERN.search(str(text or ""))
    if match is None:
        return None
    return f"{match.group(1).lower()} {int(match.group(2))}"


def units_at(units: Sequence[ContentUnit], locator: str) -> tuple[ContentUnit, ...]:
    """The units a normalised locator ("page 4") names - empty when the
    content has no such unit, so a caller can say so honestly."""
    return tuple(u for u in units if _unit_matches_locator(u, locator))


def _unit_matches_locator(unit: ContentUnit, locator: str) -> bool:
    kind, _, number = locator.partition(" ")
    unit_kind, _, unit_number = unit.locator.partition(" ")
    if unit_number.split(":")[0].strip() != number:
        return False
    # "page 2" of a document whose units are sections still means the
    # second unit - a person asking for "page 2" of a handout means the
    # second part, never "nothing".
    return unit_kind == kind or kind in ("page", "section")


def select_units(
    units: Sequence[ContentUnit], query: str, *, max_chars: int = DEFAULT_EVIDENCE_CHARS
) -> tuple[ContentUnit, ...]:
    """The units that best serve ``query``, returned in document order and
    bounded by ``max_chars``:

    - a named locator ("page 4") selects exactly that unit;
    - otherwise units sharing words with the query are preferred;
    - with no overlap at all (a "summarise this"), the document is taken
      from its beginning, so a broad request is answered from real content
      rather than an empty selection.
    """
    if not units:
        return ()
    locator = locator_in(query)
    if locator is not None:
        named = tuple(u for u in units if _unit_matches_locator(u, locator))
        if named:
            return named

    # Only words that could say what the question is *about* count as
    # overlap: "the", "this", "document", "compare" appear in almost every
    # content request and in almost every page, and letting them score made
    # "compare this with the other document" keep one page that happened to
    # contain "The" and silently drop the rest (found by this repair's own
    # test, not hypothetically).
    words = tokenize(query) - STOPWORDS - _REQUEST_WORDS
    scored = [(index, len(words & tokenize(unit.text))) for index, unit in enumerate(units)]
    # Most relevant first, then everything else in document order, all
    # bounded by the budget: relevance decides what survives when the
    # document is too long, never what a short document loses.
    relevant = [index for index, score in sorted(scored, key=lambda p: (-p[1], p[0])) if score > 0]
    order = relevant + [index for index, score in scored if score == 0]

    chosen: list[int] = []
    used = 0
    for index in order:
        size = len(units[index].text)
        if chosen and used + size > max_chars:
            continue
        chosen.append(index)
        used += size
        if used >= max_chars:
            break
    return tuple(units[index] for index in sorted(chosen))


def reading_selection(
    units: Sequence[ContentUnit], query: str, *, max_chars: int = READ_ALOUD_CHARS
) -> "tuple[tuple[ContentUnit, ...], str | None]":
    """What to read aloud for ``query``, and the locator to continue from
    when the reading had to stop before the end (None when nothing is
    left). A named unit is read in full; otherwise reading starts at the
    beginning and stops at a unit boundary once ``max_chars`` is reached."""
    if not units:
        return (), None
    locator = locator_in(query)
    if locator is not None:
        named = tuple(u for u in units if _unit_matches_locator(u, locator))
        if named:
            last = units.index(named[-1])
            following = units[last + 1].locator if last + 1 < len(units) else None
            return named, following

    chosen: list[ContentUnit] = []
    used = 0
    for index, unit in enumerate(units):
        if chosen and used + len(unit.text) > max_chars:
            return tuple(chosen), unit.locator
        chosen.append(unit)
        used += len(unit.text)
    return tuple(chosen), None


def units_to_json(artifact: Artifact, units: Sequence[ContentUnit]) -> str:
    return json.dumps(
        {
            "artifact_id": artifact.artifact_id,
            "type": artifact.type,
            "title": artifact.latest.title,
            "units": [u.to_dict() for u in units],
        }
    )


def units_from_json(data: str) -> "tuple[dict, tuple[ContentUnit, ...]]":
    payload = json.loads(data)
    units = tuple(ContentUnit(locator=str(u["locator"]), text=str(u["text"])) for u in payload.get("units", []))
    return payload, units
