"""Assembling the evidence Alfred has for a knowledge question - and being
exact about which kind of knowing each piece of it is.

This module is the seam that keeps four things apart that a single
"knowledge engine" would quietly merge. It answers one question, for one
query - *what do I actually have to reason from, and where did each piece
come from* - and it answers it without reasoning, without storing
anything, and without knowing a single subject.

    source                     where it comes from            what it means
    ------------------------------------------------------------------------
    USER_DOCUMENTS             the knowledge_lookup tool      quotable, the
                               (a KnowledgeSourceProvider)    person's own
    PERSONAL_CONTEXT           the agent's governed Memory    the resolved,
                               (personal_context.py)          current-per-
                                                              topic state of
                                                              whatever the
                                                              user has taught
                                                              Alfred about
                                                              themselves
    MODEL_KNOWLEDGE            the Brain, one layer up        settled, broad
    (live information)         deliberately NOT here          the present

**PERSONAL_CONTEXT is resolved evidence, never raw history.** Every topic
the user has ever taught Alfred may have several records behind it (see
personal_context.py's own "lifecycle without mutation"); this module reads
only ``all_current_personal_context`` - one record per topic, the latest -
so a Brain reasoning over this evidence never sees a superseded fact beside
its replacement and has to guess which one is true. What the user is
studying is part of it (Pre-Stage-9 Campaign 1): there is no second,
separate learning store to consult.

**Live information is deliberately absent, and its absence is the point.**
``LIVE_INFORMATION`` in ``capabilities.py`` already owns "what is true
right now", is claimed by its own request shape, and is the only path in
this project permitted to imply currency. A knowledge answer that quietly
folded in a live lookup would make "settled" and "current" one
indistinguishable thing, which is the exact confusion
``src/providers/live_info.py`` was written to prevent. So this module
gathers only settled evidence, and says so in the note it appends.

**It never invents a source.** Every note returned here came from a tool
that actually ran or a record that was actually stored. When nothing is
installed and nothing is remembered, the result is honestly empty and
``unavailable`` names what was missing, by name - so a caller can report
the gap rather than fill it in.

**It receives no CapabilityRequest.** The two narrow collaborators it
needs are passed in directly, so this module imports nothing from
``capabilities.py`` and can be exercised with a bare ToolSet and a plain
function. A handler passes what it has; there is nothing else in scope to
reach.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Sequence

import json
import re

from src.agents.tools.artifact_production import TRANSCRIPT_TITLE_PREFIX
from src.agents.tools.toolset import ToolSet
from src.services.artifacts.models import DocumentContent, DocumentSection
from src.core.contracts import tokenize
from src.services.artifacts.content import (
    ContentUnit,
    artifact_ids_in,
    content_reference_intent,
    select_units,
    units_from_json,
    wants_two_sources,
)
from src.services.memory.memory import MemoryRecord
from src.services.memory.personal_context import (
    STATUS_DISCONTINUED,
    PersonalContextRecord,
    all_current_personal_context,
)
from src.services.memory.query import STOPWORDS
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The one tool name this module knows. Matching the capability that grants
# it (``KNOWLEDGE``) and the tool that implements it
# (``src/agents/tools/knowledge.py``) - one name across all three, never a
# provider's or a format's own name.
KNOWLEDGE_TOOL = "knowledge_lookup"

# What each kind of evidence is called when a result reports which sources
# it actually used. Plain strings rather than an enum on purpose: they
# travel in a TaskResult payload, which is a mapping of ordinary values.
USER_DOCUMENTS = "user_documents"
PERSONAL_CONTEXT = "personal_context"
MODEL_KNOWLEDGE = "model_knowledge"
# A specific document or file the person handed Alfred (or one Alfred
# produced) and is now pointing at - "this PDF", "the other document",
# "artifact <id>" - read through the artifact store, never the knowledge
# folder search above.
SUPPLIED_CONTENT = "supplied_content"

# The tool that reads an artifact's own text units (see
# src/agents/tools/artifact_production.py's read_content/list_content).
ARTIFACT_TOOL = "artifact_production"

# How far back in the conversation a shared file still counts as "this" one.
CONTENT_HISTORY_LINES = 12

# Bound on how much of one referenced personal-context document is offered.
PERSONAL_CONTEXT_DOCUMENT_CHARS = 4000

# How many distinct personal-context topics are worth carrying into one
# answer. Small, for the same reason: ambient context for an answer, never
# a full transcript of everything the user has ever taught Alfred about
# themselves.
PERSONAL_CONTEXT_RECALL_LIMIT = 10

# How many of the user's own current topics are even considered before
# relevance narrows them down to PERSONAL_CONTEXT_RECALL_LIMIT - the
# identical bound the Control Center's own "show me everything" view
# (ControlCenter.personal_context) already uses for "the whole current
# state", so relevance selection below genuinely chooses from
# everything currently true, not merely whatever was touched most
# recently.
_PERSONAL_CONTEXT_RESOLVE_LIMIT = 200

# The inert line that stops settled evidence being read as current. Phrased
# as a statement *about the evidence* rather than as an instruction, because
# notes are evidence and never instructions - the rule
# ``build_deliberation_prompt`` already states for its CONTEXT section.
SETTLED_KNOWLEDGE_NOTE = (
    "The evidence above is settled knowledge and the user's own documents. "
    "None of it is live or current information, and none of it was checked "
    "against the present moment."
)


@dataclass(frozen=True)
class KnowledgeEvidence:
    """What was found for one query, and what was not.

    Frozen, and carrying both halves on purpose. ``notes`` is what a Brain
    may reason over; ``sources`` is what may honestly be claimed
    afterwards; ``unavailable`` is what a refusal may name. A result that
    reported only the first would leave a caller unable to tell "the
    documents said nothing" from "there are no documents", which is the
    same conflation ``ToolStatus`` already refuses between rejected and
    failed.
    """

    notes: tuple[str, ...] = field(default_factory=tuple)
    sources: tuple[str, ...] = field(default_factory=tuple)
    unavailable: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_empty(self) -> bool:
        """True when nothing was found from any consulted source."""
        return not self.notes

    def with_settlement_note(self) -> tuple[str, ...]:
        """The notes a Brain should see, closed with the settled-knowledge
        statement. Appended only when there is evidence to qualify - a lone
        disclaimer with nothing above it would be noise."""
        if not self.notes:
            return ()
        return self.notes + (SETTLED_KNOWLEDGE_NOTE,)


def _relevant_personal_context(
    items: "tuple[PersonalContextRecord, ...]", query: str, *, limit: int
) -> "tuple[PersonalContextRecord, ...]":
    """Which of ``items`` (every topic's own current state - already
    resolved, never re-derived here) are actually worth answering
    ``query`` with, rather than merely whichever were touched most
    recently.

    **Governed personal-context intelligence checkpoint.** Scored by
    the identical literal-token-overlap relevance ``Memory.recall``'s
    own ``query`` parameter already provides - reused here rather than
    duplicated. ``all_current_personal_context`` itself
    cannot take a relevance-ranked ``query`` directly: a relevance sort
    could return an older, superseded record for a topic ahead of its
    own newer replacement, silently breaking the "always the current
    statement" guarantee ``personal_context.py``'s own docstring makes.
    Resolving *what is current* and judging *what is relevant* must
    therefore stay two separate steps - this function is only the
    second one.

    Falls back to the existing "most recently touched" order, bounded
    the same way, when nothing has any real lexical overlap with the
    query at all - a genuinely broad remark should still be answered
    with *something*, the identical honest default this function's own
    caller already had before relevance scoring existed, never a
    silently emptied result for an ordinary open-ended question.

    Generic by construction: nothing here reads a topic name, a
    category, or any fixed vocabulary - only the words the user's own
    stored content and the current query actually share.
    """
    words = tokenize(query) - STOPWORDS
    if not words:
        return items[:limit]

    scored = [
        (item, len((tokenize(item.topic) | tokenize(item.content)) & words))
        for item in items
    ]
    if not any(score > 0 for _, score in scored):
        return items[:limit]

    ranked = sorted(scored, key=lambda pair: pair[1], reverse=True)
    return tuple(item for item, score in ranked if score > 0)[:limit]


def _history_lines(history: "Callable[[], Sequence[str]] | None") -> tuple[str, ...]:
    if history is None:
        return ()
    try:
        return tuple(str(line) for line in history())[-CONTENT_HISTORY_LINES:]
    except Exception as exc:  # noqa: BLE001 - a history that cannot be read
        # must not take an answer down with it.
        logger.warning("could not read conversation history: %s", exc)
        return ()


def _shared_ids(shared_files: "Callable[[], Sequence[str]] | None") -> tuple[str, ...]:
    if shared_files is None:
        return ()
    try:
        return tuple(str(item).lower() for item in shared_files() if str(item).strip())
    except Exception as exc:  # noqa: BLE001 - an unreadable record must not
        # take an answer down with it.
        logger.warning("could not read the conversation's shared files: %s", exc)
        return ()


def describe_artifact(tools: ToolSet, artifact_id: str) -> "dict | None":
    """What one owned artifact is (title, kind, renditions, readable units),
    readable or not, or None when it is not this agent's or the tool is not
    granted - never a guess."""
    if not tools.permits(ARTIFACT_TOOL):
        return None
    outcome = tools.invoke(ARTIFACT_TOOL, {"operation": "describe", "content": artifact_id})
    if not outcome.ok:
        return None
    try:
        described = json.loads(outcome.output)
    except ValueError:
        return None
    return described if isinstance(described, dict) else None


def resolve_shared_media(
    query: str,
    *,
    tools: ToolSet,
    history: "Callable[[], Sequence[str]] | None" = None,
    shared_files: "Callable[[], Sequence[str]] | None" = None,
    kinds: "Sequence[str]" = (),
) -> "dict | None":
    """The file a request points at, described - an explicit ``artifact <id>``
    first, then the newest file shared in this conversation (whatever its
    kind, when ``kinds`` is empty; otherwise the newest of those kinds).
    Returns the description even for a file with no readable text, so the
    caller can answer honestly about *that* file."""
    found = resolve_shared_media_all(
        query, tools=tools, history=history, shared_files=shared_files, kinds=kinds, limit=1,
    )
    return found[0] if found else None


def resolve_shared_media_all(
    query: str,
    *,
    tools: ToolSet,
    history: "Callable[[], Sequence[str]] | None" = None,
    shared_files: "Callable[[], Sequence[str]] | None" = None,
    kinds: "Sequence[str]" = (),
    limit: int = 2,
) -> "list[dict]":
    """Up to ``limit`` files a request points at, described, most relevant
    first - the same order ``resolve_shared_media`` uses, for a request about
    more than one ("compare these two photos")."""
    candidates: list[str] = []
    for artifact_id in artifact_ids_in([query]) + artifact_ids_in(_history_lines(history)) + _shared_ids(shared_files):
        if artifact_id not in candidates:
            candidates.append(artifact_id)
    wanted = {str(kind).lower() for kind in kinds}
    found: "list[dict]" = []
    for artifact_id in candidates:
        if len(found) >= limit:
            break
        described = describe_artifact(tools, artifact_id)
        if described is None:
            continue
        if not wanted or str(described.get("content_kind", "")).lower() in wanted or str(described.get("type", "")).lower() in wanted:
            found.append(described)
    return found


def read_artifact_content(tools: ToolSet, artifact_id: str) -> "tuple[dict, tuple[ContentUnit, ...]] | None":
    """One artifact's real text units through the governed artifact tool,
    or None when it is not this agent's, has no readable text, or the tool
    is not granted - never a guess at what it might contain."""
    if not tools.permits(ARTIFACT_TOOL):
        return None
    outcome = tools.invoke(ARTIFACT_TOOL, {"operation": "read_content", "content": artifact_id})
    if not outcome.ok:
        return None
    try:
        payload, units = units_from_json(outcome.output)
    except (ValueError, KeyError, TypeError):
        return None
    return (payload, units) if units else None


MEDIA_TOOL = "media_production"
_NAMES_A_RECORDING = re.compile(r"\b(?:recording|recordings|voice\s+note|voice\s+memo)\b", re.IGNORECASE)
_TRANSCRIPT_LINE = re.compile(r"^\s*(\d+:\d{2}(?::\d{2})?\s*-\s*\d+:\d{2}(?::\d{2})?)\s*:\s*(.+?)\s*$")


def names_a_recording(text: str) -> bool:
    """Whether a request points at a recording ("my recording", "the interview
    recording", "my latest voice note")."""
    return bool(_NAMES_A_RECORDING.search(str(text or "")))


def transcript_document(title: str, transcript: str) -> DocumentContent:
    """A transcript as an ordinary document: one section per timed passage,
    headed by its time range, so it is read, quoted and searched like any
    other document. Untimed lines become one section each."""
    sections = []
    for index, line in enumerate(line for line in str(transcript).splitlines() if line.strip()):
        timed = _TRANSCRIPT_LINE.match(line)
        heading, body = (timed.group(1), timed.group(2)) if timed else (f"passage {index + 1}", line.strip())
        sections.append(DocumentSection(heading=heading, body=body))
    if not sections:
        raise ValueError("an empty transcript is not a document")
    return DocumentContent(title=f"{TRANSCRIPT_TITLE_PREFIX}{title}", sections=tuple(sections))


def save_transcript(tools: ToolSet, recording_id: str, title: str, transcript: str) -> "str | None":
    """Keep a transcript as a document derived from its recording - the
    recording itself untouched - and return the new document's id, or None
    when it could not be saved."""
    if not tools.permits(ARTIFACT_TOOL) or not recording_id:
        return None
    try:
        doc = transcript_document(title, transcript)
    except ValueError:
        return None
    outcome = tools.invoke(ARTIFACT_TOOL, {"operation": "derive_document", "content": f"{recording_id}\n{doc.to_json()}"})
    found = re.search(r"\bid=([0-9a-f]{12})\b", outcome.output or "") if outcome.ok else None
    return found.group(1) if found else None


def recording_transcript_source(
    query: str, tools: ToolSet,
) -> "tuple[tuple[dict, tuple[ContentUnit, ...]] | None, str]":
    """The readable transcript of the recording ``query`` means, and - when
    there is none - the plain reason why.

    A transcript already saved is read; otherwise the recording is transcribed
    through the configured transcription provider (the same governed
    ``media_production`` tool ``transcribe_media`` uses) and kept as a derived
    document, so the next question about it needs no second transcription.
    Nothing is invented: no provider, no transcript."""
    if not tools.permits(ARTIFACT_TOOL):
        return None, ""
    located = tools.invoke(ARTIFACT_TOOL, {"operation": "find_recording", "content": str(query or "")})
    if not located.ok:
        return None, "there is no recording yet"
    try:
        recording = json.loads(located.output)
    except ValueError:
        return None, ""
    title = str(recording.get("title") or "your recording")
    existing = str(recording.get("transcript_artifact_id") or "")
    if existing:
        read = read_artifact_content(tools, existing)
        if read is not None:
            return read, ""
    renditions = recording.get("renditions") or {}
    path = next(iter(renditions.values()), None)
    if path is None or not tools.permits(MEDIA_TOOL):
        return None, f"'{title}' has no transcript, and I have no transcription tool to make one"
    heard = tools.invoke(MEDIA_TOOL, {"operation": "transcribe", "content": str(path)})
    if not heard.ok:
        return None, f"I couldn't transcribe '{title}': {heard.error}"
    saved = save_transcript(tools, str(recording.get("artifact_id") or ""), title, heard.output)
    read = read_artifact_content(tools, saved) if saved else None
    if read is None:
        return None, f"I transcribed '{title}' but couldn't keep the transcript"
    return read, ""


def resolve_content_sources(
    query: str,
    *,
    tools: ToolSet,
    history: "Callable[[], Sequence[str]] | None" = None,
    limit: int = 1,
    fallback_latest: bool = True,
    shared_files: "Callable[[], Sequence[str]] | None" = None,
) -> "list[tuple[dict, tuple[ContentUnit, ...]]]":
    """Which real documents a request is about, most relevant first:

    1. an explicit ``artifact <id>`` in the request itself;
    2. files shared (or produced) recently in this conversation, newest first;
    3. every file shared in this conversation (``shared_files``), newest first -
       a share that has left the recent-history window is still "this file";
    4. only when ``fallback_latest`` *and nothing was shared in this
       conversation at all* - this agent's own most recent documents and
       supplied files. A conversation that shared something never silently
       reads a file it did not share (Pre-Stage-9 Campaign 3).

    Each candidate is actually read before it counts, so an id that names
    nothing readable is skipped rather than reported as a source.
    """
    if not tools.permits(ARTIFACT_TOOL) or limit <= 0:
        return []
    # A request naming a recording ("what was decided in my recording?") is
    # about that recording's words - its transcript, read like any document.
    if names_a_recording(query) and not artifact_ids_in([query]):
        transcript, _reason = recording_transcript_source(query, tools)
        if transcript is not None:
            return [transcript]
    candidates: list[str] = []
    shared = _shared_ids(shared_files)
    for artifact_id in artifact_ids_in([query]) + artifact_ids_in(_history_lines(history)) + shared:
        if artifact_id not in candidates:
            candidates.append(artifact_id)

    found: list[tuple[dict, tuple[ContentUnit, ...]]] = []
    for artifact_id in candidates:
        if len(found) >= limit:
            break
        read = read_artifact_content(tools, artifact_id)
        if read is not None:
            found.append(read)

    if len(found) < limit and fallback_latest and not shared:
        listing = tools.invoke(ARTIFACT_TOOL, {"operation": "list_content", "content": str(limit + len(candidates))})
        if listing.ok:
            try:
                entries = json.loads(listing.output)
            except ValueError:
                entries = []
            taken = {str(payload.get("artifact_id")) for payload, _ in found}
            for entry in entries:
                if len(found) >= limit:
                    break
                artifact_id = str(entry.get("artifact_id", ""))
                if not artifact_id or artifact_id in taken:
                    continue
                read = read_artifact_content(tools, artifact_id)
                if read is not None:
                    found.append(read)
                    taken.add(artifact_id)
    return found


def gather_knowledge_evidence(
    query: str,
    *,
    tools: ToolSet | None = None,
    recall: Callable[..., Sequence[MemoryRecord]] | None = None,
    history: "Callable[[], Sequence[str]] | None" = None,
) -> KnowledgeEvidence:
    """Everything Alfred genuinely holds for ``query``, with provenance.

    Both collaborators are optional and independently so, because their
    availability is genuinely independent: an installation may have the
    user's documents and no memory, memory and no documents, both or
    neither - and all four states must be describable rather than guessed
    at. Nothing here raises; a tool that fails is recorded as unavailable
    and the rest of the evidence still stands, the same fault isolation
    ``ToolSet.invoke`` already provides one layer down.
    """
    text = str(query or "").strip()
    if not text:
        return KnowledgeEvidence()

    notes: list[str] = []
    sources: list[str] = []
    unavailable: list[str] = []

    granted = tools if tools is not None else ToolSet()
    if not granted.permits(KNOWLEDGE_TOOL):
        unavailable.append(
            f"{USER_DOCUMENTS}: no '{KNOWLEDGE_TOOL}' capability is installed, "
            "so no user-supplied document was consulted"
        )
    else:
        outcome = granted.invoke(KNOWLEDGE_TOOL, {"query": text})
        if outcome.ok and outcome.output.strip():
            # The tool's own output already carries each document name and
            # locator (see KnowledgeExcerpt.cite), so the citation travels
            # with the text rather than being reattached here.
            for line in outcome.output.splitlines():
                if line.strip():
                    notes.append(f"From your own documents: {line.strip()}")
            sources.append(USER_DOCUMENTS)
        else:
            unavailable.append(f"{USER_DOCUMENTS}: {outcome.error or 'nothing was found'}")

    # A specific document the person is pointing at ("this PDF", "the
    # other document", "artifact <id>") - read in full through the artifact
    # store and narrowed to what serves the question, never the folder
    # search above. Only when the request actually points at content, so
    # every ordinary knowledge question gathers exactly what it always did.
    if content_reference_intent(text) and granted.permits(ARTIFACT_TOOL):
        referenced = resolve_content_sources(
            text, tools=granted, history=history, limit=2 if wants_two_sources(text) else 1,
        )
        for payload, units in referenced:
            for unit in select_units(units, text):
                notes.append(
                    f"From the document '{payload.get('title')}' (artifact {payload.get('artifact_id')}), "
                    f"{unit.locator}: {unit.text}"
                )
        if referenced:
            sources.append(SUPPLIED_CONTENT)
        elif names_a_recording(text):
            _transcript, reason = recording_transcript_source(text, granted)
            unavailable.append(f"your recording: {reason or 'it has no transcript'}")

    if recall is None:
        unavailable.append(
            f"{PERSONAL_CONTEXT}: no memory reaches this request, so nothing is "
            "known about the user's other personal context"
        )
    else:
        try:
            personal = all_current_personal_context(recall=recall, limit=_PERSONAL_CONTEXT_RESOLVE_LIMIT)
            personal = _relevant_personal_context(personal, text, limit=PERSONAL_CONTEXT_RECALL_LIMIT)
        except Exception as exc:  # noqa: BLE001 - deliberate boundary catch
            logger.warning("could not recall personal context: %s", exc)
            unavailable.append(f"{PERSONAL_CONTEXT}: memory could not be read")
        else:
            if personal:
                for item in personal:
                    if item.status == STATUS_DISCONTINUED:
                        notes.append(f"Personal context (no longer current): {item.content}")
                    else:
                        notes.append(f"Personal context: {item.content}")
                        # A current topic that *is* a document ("this is my
                        # timetable" said about a shared file): the
                        # document's own relevant text is the context, read
                        # fresh from the artifact store - never a copy that
                        # could go stale in Memory.
                        if item.artifact_id:
                            read = read_artifact_content(granted, item.artifact_id)
                            if read is not None:
                                payload, units = read
                                for unit in select_units(units, text, max_chars=PERSONAL_CONTEXT_DOCUMENT_CHARS):
                                    notes.append(
                                        f"Personal context ({item.topic}), from the document "
                                        f"'{payload.get('title')}', {unit.locator}: {unit.text}"
                                    )
                sources.append(PERSONAL_CONTEXT)
            else:
                unavailable.append(
                    f"{PERSONAL_CONTEXT}: nothing is stored about the user's personal context"
                )

    return KnowledgeEvidence(
        notes=tuple(notes),
        sources=tuple(sources),
        unavailable=tuple(unavailable),
    )
