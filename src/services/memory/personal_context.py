"""Generalized, topic-keyed personal context - the mechanism, never a
catalog of the things it can hold.

**What this closes.** ``user_facts.py`` recognises a small, fixed set of
self-introduction grammars (name, date of birth, school, completion
date) - each its own regex, each its own ``fact_type``, one feature per
category. ``learning.py`` recognises exactly one shape (present-
continuous study) for exactly one purpose (pitching an explanation).
Neither generalizes to "the user just taught me a skill they have
learned", "here is my new schedule", or a future kind of personal fact
nobody has named yet - and building a third fixed-category module for
each new kind a person happens to mention is exactly the "one feature
per possible fact" this module exists to avoid.

**The generalization.** A statement is read into a ``PersonalContextCandidate``:
a free-text ``topic`` (never matched against a list - the same discipline
``learning.py``'s own subject capture already holds itself to), a
``content`` string (what was actually said), a ``category`` (an open
label like "skill"/"schedule"/"context" - carried, never enumerated
here), and a ``status`` ("current" or "discontinued"). Nothing about the
extraction functions below cares what the topic *is*; they recognise the
grammatical *shape* of a statement, the identical principle every other
deterministic extractor in this package already follows.

**Lifecycle without mutation.** ``MemoryRecord`` is frozen - "correcting
a memory means storing a new record, which leaves the correction
visible" (that module's own docstring). So a topic's current state is
never edited in place; a new record is written for every statement, each
tagged with a stable, normalized key for its topic
(``personal_context_topic:<key>``), and ``recall(tags={that_key},
limit=1)`` - "with no filters at all [beyond tags/kind], returns the
most recent records" (``Memory.recall``'s own contract) - always answers
"what is currently true about this topic" as the single latest record
for it, honestly, with the full history still sitting right behind it
in the same store, never deleted. A "no longer current" statement is not
a deletion; it is one more record, with ``status="discontinued"``, that
becomes the new latest word on that topic.

**Deterministic floor, Brain-assisted ceiling - the same shape this
project's own memory-extraction modules already establish** (see
``session.py``'s own docstring: "Model-judged extraction is a Full Brain
upgrade layered on top of this floor, never a replacement for it").
Everything in this module is Half-Brain-safe: no model is consulted.
The genuinely open-ended cases language alone cannot classify (the
capability's own Tier 2) live in ``src/manager/capabilities.py``, which
asks a Brain only when nothing here matched, and validates its answer
before ever writing it - this module holds no opinion about that.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from src.services.memory.memory import MemoryRecord

PERSONAL_CONTEXT_KIND = "personal_context"
PERSONAL_CONTEXT_FACT_TYPE = "personal_context"
PERSONAL_CONTEXT_TAG = "personal_context"

STATUS_CURRENT = "current"
STATUS_DISCONTINUED = "discontinued"

_Recall = Callable[..., "Iterable[MemoryRecord]"]


def _topic_tag(topic_key: str) -> str:
    return "personal_context_topic:" + "_".join(str(topic_key).lower().split())


def normalize_topic_key(topic: str) -> str:
    """A stable grouping key for ``topic`` - lower-cased, whitespace-
    collapsed, and stripped of the small set of leading qualifiers a
    person's own restatement of the same topic commonly carries ("my
    NEW IT schedule" and "my IT schedule" must group together, or
    supersession could never find what it is replacing). Never a lookup
    against known topics - there is no such list."""
    words = " ".join(str(topic or "").lower().split())
    qualifiers = ("new ", "updated ", "current ", "latest ", "my ")
    changed = True
    while changed:
        changed = False
        for qualifier in qualifiers:
            if words.startswith(qualifier):
                words = words[len(qualifier):]
                changed = True
    return words.strip(" .,;:-")


@dataclass(frozen=True)
class PersonalContextCandidate:
    """One recognised statement, not yet written - the neutral shape
    every extractor below returns and the capability offers to governed
    memory, the same "candidates, not memories" stance every sibling
    module in this package already holds."""

    topic: str
    content: str
    category: str
    status: str = STATUS_CURRENT

    def __post_init__(self) -> None:
        if not str(self.topic).strip():
            raise ValueError("PersonalContextCandidate.topic must not be blank")
        if not str(self.content).strip():
            raise ValueError("PersonalContextCandidate.content must not be blank")


@dataclass(frozen=True)
class PersonalContextRecord:
    """One resolved, current-or-discontinued fact, read back from
    Memory - the shape a caller (a capability, Control Center, the
    snapshot) actually wants, rather than a raw ``MemoryRecord`` whose
    ``context`` dict it would otherwise have to unpack itself."""

    topic: str
    content: str
    category: str
    status: str
    noted_by: str
    created_at: "object"
    record_id: str


# ---------------------------------------------------------------------------
# Deterministic extraction - recognised by shape, never by subject.
# ---------------------------------------------------------------------------

# A captured topic that is one of these is almost never a genuine topic -
# it is what is left over when a marker phrase matched something that was
# not really about a skill/schedule at all ("I know that's true"). Small,
# and only ever used to *reject* an otherwise-matched capture, never to
# recognise one - the identical role _NON_ACCENT_PRECEDING_WORDS plays
# for voice parsing.
_TOPIC_STOPWORDS = {
    "that", "it", "this", "so", "you", "him", "her", "them", "there",
    "here", "what", "why", "how", "which", "who",
}


def _clean_topic(text: str) -> str:
    return " ".join(str(text or "").split()).strip(" ,.:;!?")


def _reject_if_stopword(topic: "str | None") -> "str | None":
    if topic is None:
        return None
    cleaned = _clean_topic(topic)
    if not cleaned:
        return None

    def _is_acronym_shaped(word: str) -> bool:
        # An all-uppercase word ("IT", "AI", "UX") reads as a genuine
        # acronym, never the pronoun it happens to share letters with -
        # "I know IT" (the subject) and "I know it" (the pronoun) are
        # not the same capture, and only the second should ever be
        # rejected here.
        return word.isupper() and len(word) <= 6

    first_word = cleaned.split(None, 1)[0]
    whole_is_stopword = not _is_acronym_shaped(cleaned) and cleaned.lower() in _TOPIC_STOPWORDS
    opens_with_stopword = not _is_acronym_shaped(first_word) and first_word.lower() in _TOPIC_STOPWORDS
    if whole_is_stopword or opens_with_stopword:
        # Rejects both a bare pronoun capture ("I know it") and one
        # that merely *opens* with one ("I know that is true") - a
        # genuine topic is a noun phrase, never a clause that happens
        # to start with a pronoun this table already treats as filler.
        return None
    return cleaned


_MAX_TOPIC_CHARS = 120


def _bounded(text: str) -> str:
    if len(text) > _MAX_TOPIC_CHARS:
        return text[:_MAX_TOPIC_CHARS].rstrip() + " ..."
    return text


# "I've learned Python", "I have learned Python", "I learned Python",
# "I've mastered X", "I know X now" - a completed, past-tense competency
# statement. Deliberately distinct from learning.py's own present-
# continuous "I am studying X" (a different capability, a different
# category - see this module's own docstring) - "learn(ed)"/"master(ed)"
# in the past tense is a claim of having acquired something, not of
# currently working on it.
_SKILL_LEARNED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'ve| have)\s+(?:just\s+)?(?:learned|learnt|mastered|picked up)\s+(?P<topic>[^.!?]+)",
        r"\bi\s+(?:just\s+)?(?:learned|learnt|mastered)\s+(?P<topic>[^.!?]+)",
        r"\bi\s+(?:now\s+)?know\s+(?P<topic>[^.!?]+?)\s+now\b",
        r"\bi\s+know\s+(?P<topic>[^.!?]+)",
    )
)

# "This is my new IT schedule", "here's my updated timetable", "this is
# my schedule" - a general declaration/update shape, naming no category
# of thing being declared. Whatever follows "my (new/updated/current)?"
# *is* the topic and the content both - the same "whatever fills the
# slot is the value, never matched against a list" rule every extractor
# in this package already follows.
_UPDATE_DECLARATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # The topic stops at an optional ":"/"-" that introduces the
        # actual value ("this is my IT schedule: Mon-Fri 9-3") - without
        # this split, two successive statements about the same topic
        # with different values ("... schedule: Mon-Fri 9-3" then "...
        # new schedule: Tue-Thu 10-4") would each capture their own value
        # into the topic itself and be tracked as two unrelated topics,
        # never superseding one another. A real gap, found live, this
        # checkpoint.
        r"\b(?:this is|here is|here'?s)\s+my\s+(?:new\s+|updated\s+|current\s+|latest\s+)?"
        r"(?P<topic>[^.!?:\-]+?)(?:\s*[:\-]\s*(?P<content>[^.!?]+))?(?:[.!?]|$)",
        r"\bmy\s+(?:new\s+|updated\s+)(?P<topic>[^.!?]+?)\s+is\s*[:\-]?\s*(?P<content>[^.!?]*)",
    )
)

# "I'm starting IT", "I am starting a new job", "I've started my new
# course" - a declaration that a new context has begun. Read the same
# way an update-declaration is: the topic is genuinely new (no prior
# state assumed), so this shares the update path rather than needing
# its own status.
_STARTING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m| am)\s+(?:now\s+)?starting\s+(?P<topic>[^.!?]+)",
        r"\bi(?:'ve| have)\s+(?:just\s+)?started\s+(?P<topic>[^.!?]+)",
    )
)

# "My schedule has changed", "my timetable is different now" - names a
# topic as stale without stating the new value. Handled as its own
# shape: marks the topic discontinued/stale honestly (see
# extract_discontinued's own docstring) rather than inventing content
# that was never actually given.
_CHANGED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bmy\s+(?P<topic>[^.!?]+?)\s+(?:has|have)\s+changed\b",
        r"\bmy\s+(?P<topic>[^.!?]+?)\s+is\s+different\s+now\b",
    )
)

# "I'm no longer learning French", "I've stopped studying for finals",
# "I'm not doing IT anymore" - a topic explicitly closed out. Subject-
# agnostic: the topic is whatever the negated activity names, never
# matched against a list of known activities.
_DISCONTINUED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m| am)\s+no\s+longer\s+(?P<topic>[^.!?]+)",
        r"\bi(?:'ve| have)\s+stopped\s+(?P<topic>[^.!?]+)",
        # The optional leading gerund ("doing"/"taking"/"studying"/
        # "working on") is stripped from the capture so "I'm not doing
        # IT anymore" resolves to the same topic key as "I'm starting
        # IT" - a grammatical linking verb, not a personal fact, the
        # same distinction learning.py already draws for its own verb
        # list.
        r"\bi(?:'m| am)\s+not\s+(?:doing|taking|studying|working\s+on)?\s*(?P<topic>[^.!?]+?)\s+anymore\b",
    )
)


def extract_skill_learned(text: str) -> "PersonalContextCandidate | None":
    stripped = str(text or "")
    for pattern in _SKILL_LEARNED_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(match.group("topic"))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user has learned {topic}.",
            category="skill",
            status=STATUS_CURRENT,
        )
    return None


def extract_update_declaration(text: str) -> "PersonalContextCandidate | None":
    stripped = str(text or "")
    for pattern in _UPDATE_DECLARATION_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        groups = match.groupdict()
        topic = _reject_if_stopword(groups.get("topic"))
        if topic is None:
            continue
        content_part = groups.get("content")
        topic = _bounded(topic)
        if content_part and content_part.strip():
            content_part = _clean_topic(content_part)
            statement = f"The user's {topic} is: {content_part}"
        else:
            statement = f"The user stated (regarding {topic}): {_clean_topic(stripped)}"
        return PersonalContextCandidate(
            topic=topic, content=_bounded(statement), category="context", status=STATUS_CURRENT,
        )

    for pattern in _STARTING_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(match.group("topic"))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user has started {topic}.",
            category="context",
            status=STATUS_CURRENT,
        )
    return None


def extract_discontinued(text: str) -> "PersonalContextCandidate | None":
    """A topic the person just said is no longer current - "no longer
    X", "stopped X", "not X anymore", or a bare "my X has changed" (a
    real, honest signal that X is stale even with no new value given
    yet - never inventing content that was not actually stated)."""
    stripped = str(text or "")
    for pattern in _DISCONTINUED_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(match.group("topic"))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user is no longer {topic}.",
            category="context",
            status=STATUS_DISCONTINUED,
        )
    for pattern in _CHANGED_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(match.group("topic"))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user's {topic} has changed (new value not yet stated).",
            category="context",
            status=STATUS_DISCONTINUED,
        )
    return None


def extract_personal_context_candidate(text: str) -> "PersonalContextCandidate | None":
    """Every deterministic shape this module recognises, tried in order:
    discontinuation first (a "no longer/changed" statement should never
    be mistaken for a fresh skill/update), then skill-acquired, then
    update-declaration/starting. The first genuine match wins; nothing
    here ever combines two shapes from one sentence."""
    for extractor in (extract_discontinued, extract_skill_learned, extract_update_declaration):
        candidate = extractor(text)
        if candidate is not None:
            return candidate
    return None


def personal_context_intent(text: str) -> bool:
    """The ``claims=`` predicate a capability recognises its own
    deterministic request shape with - true only when at least one of
    this module's own grammars actually matched."""
    return extract_personal_context_candidate(text) is not None


def build_personal_context_record(
    candidate: PersonalContextCandidate, *, agent_name: str
) -> MemoryRecord:
    """The MemoryRecord a governed write path retains for one candidate -
    constructed here, never written here (this module holds no Memory
    reference of its own, the same boundary every sibling extractor in
    this package already crosses only through its caller)."""
    topic_key = normalize_topic_key(candidate.topic)
    return MemoryRecord(
        content=candidate.content,
        kind=PERSONAL_CONTEXT_KIND,
        tags={PERSONAL_CONTEXT_TAG, _topic_tag(topic_key)},
        context={
            "fact_type": PERSONAL_CONTEXT_FACT_TYPE,
            "topic": candidate.topic,
            "topic_key": topic_key,
            "category": candidate.category,
            "status": candidate.status,
            "source": "conversation",
            "noted_by": agent_name,
        },
    )


# ---------------------------------------------------------------------------
# Retrieval - "latest record for this topic wins", never a second store.
# ---------------------------------------------------------------------------


def _to_record(raw: MemoryRecord) -> "PersonalContextRecord | None":
    ctx = raw.context or {}
    topic = ctx.get("topic")
    if not topic:
        return None
    return PersonalContextRecord(
        topic=str(topic),
        content=raw.content,
        category=str(ctx.get("category") or "context"),
        status=str(ctx.get("status") or STATUS_CURRENT),
        noted_by=str(ctx.get("noted_by") or ""),
        created_at=raw.created_at,
        record_id=raw.id,
    )


def current_personal_context_for(topic: str, *, recall: "_Recall | None") -> "PersonalContextRecord | None":
    """The single, latest statement about ``topic`` - honestly ``None``
    when nothing has ever been said about it, or no ``recall`` reaches
    this call at all. Never a guess, never the first match: the most
    recent record genuinely is the current word on this topic, because
    every statement about the same topic shares the same tag and
    ``recall`` (with no query text) returns most-recent-first."""
    if recall is None:
        return None
    for raw in recall(kind=PERSONAL_CONTEXT_KIND, tags={_topic_tag(normalize_topic_key(topic))}, limit=1):
        return _to_record(raw)
    return None


def all_current_personal_context(*, recall: "_Recall | None", limit: int = 50) -> "tuple[PersonalContextRecord, ...]":
    """Every topic's own latest statement - real Memory content, grouped
    by the same topic key every write already carries, never a second
    index. ``limit`` bounds how many of the most recent raw records are
    considered; a topic whose only mention has aged out of that window
    is honestly absent, the same bounded-recall trade-off every other
    "what do I know" query in this project already accepts.
    """
    if recall is None:
        return ()
    seen_topics: set[str] = set()
    resolved: list[PersonalContextRecord] = []
    for raw in recall(kind=PERSONAL_CONTEXT_KIND, tags={PERSONAL_CONTEXT_TAG}, limit=limit):
        ctx = raw.context or {}
        topic_key = str(ctx.get("topic_key") or normalize_topic_key(str(ctx.get("topic") or "")))
        if not topic_key or topic_key in seen_topics:
            continue
        seen_topics.add(topic_key)
        record = _to_record(raw)
        if record is not None:
            resolved.append(record)
    return tuple(resolved)
