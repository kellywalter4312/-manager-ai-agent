"""The canonical personal-context system - the one place user knowledge is
learned, superseded and read back. The mechanism, never a catalog of the
things it can hold.

**What this closes.** ``user_facts.py`` recognises a small, fixed set of
self-introduction grammars (name, date of birth, school, completion
date). ``learning.py`` recognises the grammar of a study statement.
Neither generalizes to "the user just taught me a skill they have
learned", "here is my new schedule", "my goal this year is X", or a future
kind of personal fact nobody has named yet.

**One store (Pre-Stage-9 Campaign 1).** Every self-declared, updatable fact
- a study subject, a learned skill, a schedule, a goal, a preference, a
project - is written here, as a ``personal_context`` record keyed by its
topic. Before this campaign a study statement went to a separate
``learning`` store and a "remember that my X is Y" to plain
``user_context``, so the same knowledge lived in three places that
superseded nothing. Those older records are still *read* by
``all_current_personal_context`` (adapted into the same shape, the newest
statement per topic still winning), but nothing writes them any more.

**The generalization.** A statement is read into a ``PersonalContextCandidate``:
a free-text ``topic`` (never matched against a list), a ``content`` string,
a ``category`` (an open label like "learning"/"skill"/"context" - carried,
never enumerated here), and a ``status`` ("current" or "discontinued").
Nothing about the extraction functions below cares what the topic *is*;
they recognise the grammatical *shape* of a statement.

**Lifecycle without mutation.** ``MemoryRecord`` is frozen, so a topic's
current state is never edited in place; a new record is written for every
statement, tagged with a stable key for its topic, and the most recent
record for a key is the current word on it - the full history still in the
store behind it. A "no longer current" statement is not a deletion; it is
one more record, with ``status="discontinued"``.

**Deterministic floor, Brain-assisted ceiling.** Everything in this module
is Half-Brain-safe: no model is consulted, and every question about what is
stored (``match_personal_context``) is answered by token overlap over real
records. The genuinely open-ended cases language alone cannot classify live
in ``src/manager/capabilities.py``, which asks a Brain only when it is
available and validates its answer before writing anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable, Iterable

from src.core.contracts import tokenize
from src.services.memory.learning import (
    LEARNING_KIND,
    extract_learning_statement,
    strip_trailing_filler,
    strip_vocative_address,
)
from src.services.memory.memory import MemoryRecord
from src.services.memory.query import STOPWORDS
from src.services.memory.user_facts import (
    DECLARED_FACT_TYPES,
    extract_declared_facts,
    relational_birthday_intent,
)

PERSONAL_CONTEXT_KIND = "personal_context"
PERSONAL_CONTEXT_FACT_TYPE = "personal_context"
PERSONAL_CONTEXT_TAG = "personal_context"

# The kind a plain "remember that X" note is filed under (session.py's own
# RETAINED_KIND). Read here only to present older topic-shaped notes.
_USER_CONTEXT_KIND = "user_context"

STATUS_CURRENT = "current"
STATUS_DISCONTINUED = "discontinued"

_Recall = Callable[..., "Iterable[MemoryRecord]"]


def _topic_tag(topic_key: str) -> str:
    return "personal_context_topic:" + "_".join(str(topic_key).lower().split())


# Time phrases that qualify *when* a statement holds, never *what* it is
# about ("my goal this year" and "my goal" are one topic). Grammar only.
_TEMPORAL_TAIL = re.compile(
    r"\s+(?:(?:for\s+)?(?:this|next|the)\s+(?:year|month|week|term|semester|season|quarter)"
    r"|now|lately|these\s+days|at\s+the\s+moment|right\s+now)$",
    re.IGNORECASE,
)


def normalize_topic_key(topic: str) -> str:
    """A stable grouping key for ``topic`` - lower-cased, whitespace-
    collapsed, and stripped of the small set of leading qualifiers and
    trailing time phrases a person's own restatement of the same topic
    commonly carries ("my NEW IT schedule" and "my IT schedule", "my goal
    this year" and "my goal", must group together, or supersession could
    never find what it is replacing). Never a lookup against known topics -
    there is no such list."""
    words = " ".join(str(topic or "").lower().split())
    qualifiers = ("new ", "updated ", "current ", "latest ", "my ")
    changed = True
    while changed:
        changed = False
        for qualifier in qualifiers:
            if words.startswith(qualifier):
                words = words[len(qualifier):]
                changed = True
        stripped = _TEMPORAL_TAIL.sub("", words)
        if stripped != words and stripped.strip():
            words = stripped
            changed = True
    return words.strip(" .,;:-")


@dataclass(frozen=True)
class PersonalContextCandidate:
    """One recognised statement, not yet written - the neutral shape every
    extractor below returns and the capability offers to governed memory.

    ``value`` is the stated value when the statement had one ("Tue-Thu
    10-4"), ``activity`` the study verb for a study statement, and
    ``broad`` marks the one shape ("my X is Y") general enough that a Brain,
    when one is available, is asked to confirm it really is a personal fact
    before it is kept."""

    topic: str
    content: str
    category: str
    status: str = STATUS_CURRENT
    value: str = ""
    activity: str = ""
    broad: bool = False

    def __post_init__(self) -> None:
        if not str(self.topic).strip():
            raise ValueError("PersonalContextCandidate.topic must not be blank")
        if not str(self.content).strip():
            raise ValueError("PersonalContextCandidate.content must not be blank")


@dataclass(frozen=True)
class PersonalContextRecord:
    """One resolved, current-or-discontinued fact, read back from Memory -
    the shape every reader (a capability, Control Center, a specialist's
    profile, the knowledge evidence) actually wants."""

    topic: str
    content: str
    category: str
    status: str
    noted_by: str
    created_at: "object"
    record_id: str
    # The supplied/document artifact this statement was about ("this is my
    # timetable" said about a shared PDF), when there was one - the
    # artifact stays the source of the actual content; this record is only
    # the governed, supersedable claim that it is the current one.
    artifact_id: str = ""
    value: str = ""
    activity: str = ""
    # Which store the record was read from: "personal_context" for every
    # record written since Campaign 1, or the older kind it was adapted
    # from ("learning", "user_context").
    source_kind: str = PERSONAL_CONTEXT_KIND

    @property
    def topic_key(self) -> str:
        return normalize_topic_key(self.topic)


# ---------------------------------------------------------------------------
# Deterministic extraction - recognised by shape, never by subject.
# ---------------------------------------------------------------------------

# A captured topic that is one of these is almost never a genuine topic -
# it is what is left over when a marker phrase matched something that was
# not really about a skill/schedule at all ("I know that's true"). Small,
# and only ever used to *reject* an otherwise-matched capture, never to
# recognise one.
_TOPIC_STOPWORDS = {
    "that", "it", "this", "so", "you", "him", "her", "them", "there",
    "here", "what", "why", "how", "which", "who", "your",
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
        # acronym, never the pronoun it happens to share letters with.
        return word.isupper() and len(word) <= 6

    first_word = cleaned.split(None, 1)[0]
    whole_is_stopword = not _is_acronym_shaped(cleaned) and cleaned.lower() in _TOPIC_STOPWORDS
    opens_with_stopword = not _is_acronym_shaped(first_word) and first_word.lower() in _TOPIC_STOPWORDS
    if whole_is_stopword or opens_with_stopword:
        return None
    return cleaned


_MAX_TOPIC_CHARS = 120


def _bounded(text: str) -> str:
    if len(text) > _MAX_TOPIC_CHARS:
        return text[:_MAX_TOPIC_CHARS].rstrip() + " ..."
    return text


def _third_person(text: str) -> str:
    """"my notes" said by the user is "their notes" in a statement about
    them - pronouns only, never meaning."""
    return re.sub(r"\bmy\b", "their", re.sub(r"\bI\b", "they", str(text)))


# "I've learned Python", "I have learned Python", "I learned Python",
# "I've mastered X", "I know X now" - a completed, past-tense competency
# statement, distinct from a study still under way.
_SKILL_LEARNED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'ve| have)\s+(?:just\s+|now\s+|finally\s+|also\s+)?(?:learned|learnt|mastered|picked up)\s+(?P<topic>[^.!?]+)",
        r"\bi\s+(?:just\s+|finally\s+|also\s+)?(?:learned|learnt|mastered)\s+(?P<topic>[^.!?]+)",
        r"\bi\s+(?:now\s+)?know\s+(?P<topic>[^.!?]+?)\s+now\b",
        r"\bi\s+know\s+(?P<topic>[^.!?]+)",
    )
)

# "This is my new IT schedule", "here's my updated timetable" - a general
# declaration/update shape, naming no category of thing being declared.
_UPDATE_DECLARATION_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # The topic stops at an optional ":"/"-" that introduces the actual
        # value ("this is my IT schedule: Mon-Fri 9-3"), so two successive
        # statements about the same topic with different values supersede
        # one another instead of being tracked as two topics.
        r"\b(?:this is|here is|here'?s)\s+my\s+(?:new\s+|updated\s+|current\s+|latest\s+)?"
        r"(?P<topic>[^.!?:\-]+?)(?:\s*[:\-]\s*(?P<content>[^.!?]+))?(?:[.!?]|$)",
        r"\bmy\s+(?:new\s+|updated\s+)(?P<topic>[^.!?]+?)\s+is\s*[:\-]?\s*(?P<content>[^.!?]*)",
    )
)

# "I'm starting IT", "I've started my new course" - a declaration that a
# new context has begun.
_STARTING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m| am)\s+(?:now\s+|also\s+)?starting\s+(?P<topic>[^.!?]+)",
        r"\bi(?:'ve| have)\s+(?:just\s+|also\s+)?started\s+(?P<topic>[^.!?]+)",
    )
)

# "My schedule has changed" - names a topic as stale without stating the
# new value. "has changed TO X" is a new value, handled by the possessive
# declaration below, never by this shape.
_CHANGED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bmy\s+(?P<topic>[^.!?]+?)\s+(?:has|have)\s+changed\b(?!\s+to\b)",
        r"\bmy\s+(?P<topic>[^.!?]+?)\s+is\s+different\s+now\b",
    )
)

# "I'm no longer learning French", "I've stopped studying for finals",
# "I'm not doing IT anymore" - a topic explicitly closed out.
_DISCONTINUED_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m| am)\s+no\s+longer\s+(?P<topic>[^.!?]+)",
        r"\bi(?:'ve| have)\s+stopped\s+(?P<topic>[^.!?]+)",
        r"\bi(?:'m| am)\s+not\s+(?P<topic>[^.!?]+?)\s+any\s*more\b",
    )
)

# The activity verb a closed-out topic is often phrased with ("no longer
# LEARNING French", "not DOING IT anymore") - grammar, stripped so the
# topic resolves to the same key as the statement that opened it.
_LEADING_ACTIVITY = re.compile(
    r"^(?:learning|studying|revising|reading|practi[cs]ing|doing|taking|working\s+on|going\s+to)\s+(?:about\s+|for\s+)?",
    re.IGNORECASE,
)

# "My X is Y", "my X are Y", "my X has changed to Y", "my X is now Y" - the
# general possessive declaration of a personal value. Anchored to the start
# of the statement (after an optional short lead-in like "also," or "by the
# way,") so a possessive buried inside a longer request ("can you check
# whether my code is right") is never read as one.
_LEAD_IN = r"^(?:(?:and|so|also|well|oh|ok|okay|actually|by\s+the\s+way|btw|fyi|just\s+so\s+you\s+know)\s*,?\s*)?"
_POSSESSIVE_DECLARATION = re.compile(
    _LEAD_IN
    + r"my\s+(?P<topic>[a-z0-9][\w'\-+#]*(?:\s+[\w'\-+#]+){0,5}?)"
    r"(?:\s+(?P<verb>has\s+changed\s+to|have\s+changed\s+to|changed\s+to|is\s+now|are\s+now|is|are)|'s)\s+"
    r"(?P<value>[^.!?]+)",
    re.IGNORECASE,
)

# Opening words that make a sentence a question or a request rather than a
# statement - grammar, never a topic.
_NON_STATEMENT_OPENERS = frozenset({
    "what", "when", "where", "who", "whom", "whose", "why", "how", "which",
    "do", "does", "did", "is", "are", "was", "were", "can", "could", "would",
    "will", "should", "shall", "may", "might", "please", "tell", "show",
    "give", "help", "let", "find", "check", "read", "remind", "explain",
})


def _is_question_or_request(text: str) -> bool:
    working = strip_vocative_address(text).strip()
    if not working:
        return True
    if working.rstrip().endswith("?"):
        return True
    first = re.split(r"[\s,]+", working.lower(), maxsplit=1)[0].strip("'\"")
    return first in _NON_STATEMENT_OPENERS


def is_question_or_request(text: str) -> bool:
    """Whether ``text`` asks or requests rather than states - the check
    every statement shape here applies, offered to the capability layer so
    its Brain-assisted tier never treats a question as something to learn."""
    return _is_question_or_request(text)


# Closure campaign (2026-09-16, REQUIREMENT_LEDGER.md N-08): found live -
# "I am a Python expert." matched no extractor at all and silently fell
# through to open_conversation, not learned and not honestly refused
# either. _SKILL_LEARNED_PATTERNS above only recognises a completed
# "learned/mastered/know" verb; extract_habitual_statement deliberately
# excludes "am" (_NOT_A_LIFE_VERB) because "I am wondering"/"I am hoping"
# are mental-state hedges, not facts. Neither shape covers the single most
# ordinary way a person states a profession, role or identity: "I am a/an
# <noun>." This is precisely the founding example the whole-ecosystem
# closure brief itself names (Python -> C++ -> Java learning must not
# need new code per subject) - and "I am a Python developer" is the
# natural phrasing, not "I have learned Python developer".
#
# "a bit"/"a little"/"a lot" are excluded because they are hedge
# quantifiers, not an article introducing a profession/role noun - "I am
# a bit worried" must never be learned as an identity fact the way "I am
# a doctor" genuinely should be.
_IDENTITY_HEDGE_OPENERS = frozenset({"bit", "little", "lot"})
_IDENTITY_STATEMENT = re.compile(
    r"\bi'?m\s+an?\s+(?P<topic>[^.!?]{2,80})|\bi\s+am\s+an?\s+(?P<topic2>[^.!?]{2,80})",
    re.IGNORECASE,
)
_STARTS_WITH_VOWEL_SOUND = re.compile(r"^[aeiou]", re.IGNORECASE)


def _a_or_an(word: str) -> str:
    return "an" if _STARTS_WITH_VOWEL_SOUND.match(word) else "a"


def extract_identity_statement(text: str) -> "PersonalContextCandidate | None":
    """"I am a Python developer.", "I'm a vegetarian." - an ordinary
    profession/role/identity statement, the one common shape none of the
    other extractors in this module recognise (see the comment above)."""
    stripped = strip_vocative_address(str(text or "")).strip()
    if _is_question_or_request(stripped) or _NEGATED.search(stripped):
        return None
    match = _IDENTITY_STATEMENT.search(stripped)
    if match is None:
        return None
    topic = match.group("topic") or match.group("topic2")
    first_word = topic.strip().split(None, 1)[0].lower() if topic.strip() else ""
    if first_word in _IDENTITY_HEDGE_OPENERS:
        return None
    topic = _reject_if_stopword(_clean_topic(topic))
    if topic is None:
        return None
    topic = _bounded(topic)
    return PersonalContextCandidate(
        topic=topic,
        content=f"The user is {_a_or_an(topic)} {topic}.",
        category="identity",
        status=STATUS_CURRENT,
    )


def extract_skill_learned(text: str) -> "PersonalContextCandidate | None":
    stripped = str(text or "")
    if _is_question_or_request(stripped):
        return None
    for pattern in _SKILL_LEARNED_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(strip_trailing_filler(match.group("topic")))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user has learned {topic}.",
            category="skill",
            status=STATUS_CURRENT,
            activity="learned",
        )
    return None


def extract_learning_activity(text: str) -> "PersonalContextCandidate | None":
    """A study statement ("I'm also learning X", "I've started studying Y")
    as one ordinary personal-context candidate whose topic is the subject -
    the single writer of what used to be a separate learning store."""
    stripped = str(text or "")
    if _is_question_or_request(stripped):
        return None
    statement = extract_learning_statement(stripped)
    if statement is None:
        return None
    topic = _reject_if_stopword(statement.subject)
    if topic is None:
        return None
    topic = _bounded(topic)
    phrase = _third_person(f"{statement.preposition}{topic}")
    if statement.tense == "started":
        content = f"The user has started {statement.activity} {phrase}."
    else:
        content = f"The user is {statement.activity} {phrase}."
    return PersonalContextCandidate(
        topic=topic, content=content, category="learning", status=STATUS_CURRENT,
        activity=statement.activity,
    )


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
        value = ""
        if content_part and content_part.strip():
            value = _clean_topic(content_part)
            statement = f"The user's {topic} is: {value}"
        else:
            statement = f"The user stated (regarding {topic}): {_clean_topic(stripped)}"
        return PersonalContextCandidate(
            topic=topic, content=_bounded(statement), category="context", status=STATUS_CURRENT,
            value=value,
        )

    if _is_question_or_request(stripped):
        return None
    for pattern in _STARTING_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        topic = _reject_if_stopword(strip_trailing_filler(match.group("topic")))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user has started {_third_person(topic)}.",
            category="context",
            status=STATUS_CURRENT,
        )
    return None


def extract_possessive_declaration(text: str) -> "PersonalContextCandidate | None":
    """"My goal this year is to X", "my favourite colour is green now", "my
    schedule has changed to Tue-Thu" - a personal value, stated. The topic
    is whatever fills "my ___", the value whatever follows the verb; neither
    is ever matched against a list.

    Deferred, never duplicated: a self-introduction fact user_facts.py
    already owns ("my name is", "my birthday is") and another person's
    birthday are left to those extractors."""
    working = strip_vocative_address(str(text or "")).strip()
    if not working or _is_question_or_request(working):
        return None
    if extract_declared_facts(working) or relational_birthday_intent(working):
        return None
    match = _POSSESSIVE_DECLARATION.search(working)
    if match is None:
        return None
    topic = _reject_if_stopword(match.group("topic"))
    if topic is None:
        return None
    value = strip_trailing_filler(_clean_topic(match.group("value")))
    value = re.sub(r"^(?:now|currently|actually|really|in\s+fact)\s+", "", value, flags=re.IGNORECASE).strip()
    if not value or len(value) > 200:
        return None
    verb = (match.group("verb") or "is").lower()
    linking = "are" if verb.startswith("are") or verb.startswith("have") else "is"
    if "changed" in verb or verb.endswith("now"):
        linking = f"{linking} now"
    topic = _bounded(topic)
    return PersonalContextCandidate(
        topic=topic,
        content=f"The user's {topic} {linking} {_third_person(value)}.",
        category="context",
        status=STATUS_CURRENT,
        value=value,
        broad=True,
    )


def extract_discontinued(text: str) -> "PersonalContextCandidate | None":
    """A topic the person just said is no longer current - "no longer X",
    "stopped X", "not X anymore", or a bare "my X has changed" (a real,
    honest signal that X is stale even with no new value given yet - never
    inventing content that was not actually stated)."""
    stripped = str(text or "")
    if _is_question_or_request(stripped):
        return None
    for pattern in _DISCONTINUED_PATTERNS:
        match = pattern.search(stripped)
        if match is None:
            continue
        said = _clean_topic(match.group("topic"))
        topic = _reject_if_stopword(_LEADING_ACTIVITY.sub("", said))
        if topic is None:
            continue
        topic = _bounded(topic)
        return PersonalContextCandidate(
            topic=topic,
            content=f"The user is no longer {_third_person(said)}.",
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


# A plain, present-tense statement about the person's own life: "I go to the
# gym on Mondays", "I work at Barclays", "I live in Leeds", "I play five-a-side
# on Saturdays". Nothing above recognised these at all - they are neither a
# possessive ("my X is Y") nor a study statement - so an ordinary sentence
# about the person's own routine was answered with "no reasoning capability is
# configured" and learned nothing.
#
# The verb is *not* a fixed vocabulary of life: it is any verb that is not one
# of the auxiliaries and speech-act verbs below, which are grammar rather than
# living ("I need...", "I want you to...", "I think...", "I asked..."). The
# topic is the object of the sentence, so the question that asks for it back
# ("when do I go to the gym") shares its words.
_NOT_A_LIFE_VERB = frozenset({
    "am", "was", "were", "be", "been", "being", "have", "has", "had", "do", "did", "does",
    "will", "would", "can", "could", "shall", "should", "may", "might", "must",
    "need", "want", "wish", "hope", "think", "thought", "believe", "guess", "suppose",
    "wonder", "mean", "meant", "ask", "asked", "tell", "told", "say", "said", "know", "knew",
    "like", "love", "hate", "prefer", "remember", "forget", "forgot", "see", "saw", "feel", "felt",
    "get", "got", "let", "make", "made", "try", "tried", "use", "used", "keep", "kept",
    "learn", "learning", "learned", "learnt", "study", "studying", "start", "starting", "started",
    "stop", "stopped", "read", "reading", "write", "writing", "send", "sending",
})
# An adverb between "I" and the verb ("I still need...", "I usually go...")
# is consumed rather than mistaken for the verb - otherwise "I still need to
# finish my Java work" would be learned as "the user stills need ...".
_HABIT_ADVERBS = r"(?:still|always|usually|often|sometimes|normally|generally|also|now|just|only|really)"
# The verb carries no negation and no contraction. "I don't like that",
# "I can't believe my boss yelled at me" say what is *not* so, or express a
# feeling about something else - neither is a fact about the person's life,
# and both were being read as one ("the user can'ts believe ...").
_HABITUAL_STATEMENT = re.compile(
    _LEAD_IN + rf"i\s+(?:{_HABIT_ADVERBS}\s+)*(?P<verb>[a-z][a-z\-]{{1,20}})\s+(?P<rest>[^.!?]{{2,120}})",
    re.IGNORECASE,
)
_NEGATED = re.compile(r"\bi\s+(?:\w+\s+)?(?:do\s+not|does\s+not|did\s+not|never|no\s+longer)\b|\b\w+n't\b", re.IGNORECASE)
# "I go to the gym" is a habit; "I need to call the bank", "I have to leave",
# "I am going to start" are intentions and obligations, which are not facts
# about the person's life to be kept.
_INTENTION_SHAPE = re.compile(
    r"\bi\s+(?:\w+\s+)?(?:need|want|have|have\s+got|ought|plan|intend|hope|try|going)\s+to\b",
    re.IGNORECASE,
)
# A sentence that talks to Alfred rather than about the person's own life.
_ADDRESSES_THE_AGENT = re.compile(r"\byou\b|\byour\b|\bplease\b", re.IGNORECASE)
# The leading article/preposition of the object, dropped so "to the gym on
# Mondays" keys on "gym" the way "my dentist" keys on "dentist".
_LEADING_OBJECT_WORDS = re.compile(
    r"^(?:to|at|in|on|for|with|from|into|about)\s+(?:the\s+|a\s+|an\s+|my\s+)?"
    r"|^(?:the|a|an|my)\s+",
    re.IGNORECASE,
)
# The time clause a habit usually ends with - kept in the content, dropped
# from the topic so "the gym on Mondays" and "the gym" are the same topic.
_TRAILING_TIME_CLAUSE = re.compile(
    r"\s+(?:on|every|each|at|after|before|during)\s+[\w\s:]+$", re.IGNORECASE
)


def extract_habitual_statement(text: str) -> "PersonalContextCandidate | None":
    """"I go to the gym on Mondays" - an ordinary fact about the person's own
    life, learned as one topic like any other."""
    stripped = strip_vocative_address(str(text or "")).strip()
    if (
        _is_question_or_request(stripped)
        or _ADDRESSES_THE_AGENT.search(stripped)
        or _INTENTION_SHAPE.search(stripped)
        or _NEGATED.search(stripped)
    ):
        return None
    match = _HABITUAL_STATEMENT.match(stripped)
    if match is None:
        return None
    verb = match.group("verb").lower()
    if verb in _NOT_A_LIFE_VERB:
        return None
    rest = _clean_topic(match.group("rest"))
    if not rest:
        return None
    topic = _reject_if_stopword(_TRAILING_TIME_CLAUSE.sub("", _LEADING_OBJECT_WORDS.sub("", rest)).strip())
    if topic is None:
        return None
    return PersonalContextCandidate(
        topic=_bounded(topic),
        content=f"The user {_third_person_verb(verb)} {rest}.",
        category="routine",
        status=STATUS_CURRENT,
        activity=verb,
    )


def _third_person_verb(verb: str) -> str:
    """"go" -> "goes", "study" -> "studies", "work" -> "works" - ordinary
    English agreement, so the stored sentence reads like a sentence."""
    word = verb.lower()
    if word in _IRREGULAR_THIRD_PERSON:
        return _IRREGULAR_THIRD_PERSON[word]
    if word.endswith(("s", "x", "z", "ch", "sh", "o")):
        return word + "es"
    if word.endswith("y") and len(word) > 1 and word[-2] not in "aeiou":
        return word[:-1] + "ies"
    return word + "s"


_IRREGULAR_THIRD_PERSON = {"have": "has", "be": "is", "go": "goes", "do": "does"}


def extract_personal_context_candidate(text: str) -> "PersonalContextCandidate | None":
    """Every deterministic shape this module recognises, tried in order:
    discontinuation first (a "no longer/changed" statement should never be
    mistaken for a fresh one), then skill-acquired, then an identity
    statement ("I am a Python developer" - closure campaign, 2026-09-16),
    then a study statement, then update-declaration/starting, then the
    general possessive declaration. The first genuine match wins; nothing
    here ever combines two shapes from one sentence."""
    for extractor in (
        extract_discontinued, extract_skill_learned, extract_identity_statement,
        extract_learning_activity, extract_update_declaration, extract_possessive_declaration,
        # Last: every shape above is more specific, and a habitual statement
        # is the general case they are special cases of.
        extract_habitual_statement,
    ):
        candidate = extractor(text)
        if candidate is not None:
            return candidate
    return None


def personal_context_intent(text: str) -> bool:
    """True only when at least one of this module's own grammars matched."""
    return extract_personal_context_candidate(text) is not None


def build_personal_context_record(
    candidate: PersonalContextCandidate, *, agent_name: str, artifact_id: str = ""
) -> MemoryRecord:
    """The MemoryRecord a governed write path retains for one candidate -
    constructed here, never written here.

    ``artifact_id``, when given, names the real document the statement was
    about. Only the id travels into Memory - never the document's text."""
    topic_key = normalize_topic_key(candidate.topic)
    context = {
        "fact_type": PERSONAL_CONTEXT_FACT_TYPE,
        "topic": candidate.topic,
        "topic_key": topic_key,
        "category": candidate.category,
        "status": candidate.status,
        "source": "conversation",
        "noted_by": agent_name,
    }
    if candidate.value:
        context["value"] = candidate.value
    if candidate.activity:
        context["activity"] = candidate.activity
    if artifact_id:
        context["artifact_id"] = artifact_id
    return MemoryRecord(
        content=candidate.content,
        kind=PERSONAL_CONTEXT_KIND,
        tags={PERSONAL_CONTEXT_TAG, _topic_tag(topic_key)},
        context=context,
    )


# ---------------------------------------------------------------------------
# Retrieval - "latest record for this topic wins", across every store user
# knowledge has ever been written to.
# ---------------------------------------------------------------------------


def _to_record(raw: MemoryRecord) -> "PersonalContextRecord | None":
    ctx = raw.context or {}
    topic = ctx.get("topic")
    if not topic:
        return None
    topic = str(topic)
    content = raw.content
    # Written before study statements joined this store: "I've started
    # learning X" was kept under the topic "learning X". The topic is X.
    if str(ctx.get("category") or "") == "context" and re.match(r"^The user (?:has started|is no longer) ", content):
        stripped = _LEADING_ACTIVITY.sub("", topic).strip()
        if stripped and stripped != topic:
            topic = stripped
    return PersonalContextRecord(
        topic=topic,
        content=content,
        category=str(ctx.get("category") or "context"),
        status=str(ctx.get("status") or STATUS_CURRENT),
        noted_by=str(ctx.get("noted_by") or ""),
        created_at=raw.created_at,
        record_id=raw.id,
        artifact_id=str(ctx.get("artifact_id") or ""),
        value=str(ctx.get("value") or ""),
        activity=str(ctx.get("activity") or ""),
    )


def _legacy_learning_record(raw: MemoryRecord) -> "PersonalContextRecord | None":
    ctx = raw.context or {}
    subject = str(ctx.get("subject") or re.sub(r"^Currently studying:\s*", "", raw.content)).strip()
    subject = strip_trailing_filler(subject)
    if not subject:
        return None
    return PersonalContextRecord(
        topic=subject, content=f"The user is studying {_third_person(subject)}.", category="learning",
        status=STATUS_CURRENT, noted_by=str(ctx.get("noted_by") or ""), created_at=raw.created_at,
        record_id=raw.id, activity="studying", source_kind=LEARNING_KIND,
    )


def _legacy_user_context_record(raw: MemoryRecord) -> "PersonalContextRecord | None":
    ctx = raw.context or {}
    fact_type = str(ctx.get("fact_type") or "")
    if fact_type in DECLARED_FACT_TYPES:
        return PersonalContextRecord(
            topic=fact_type.replace("_", " "), content=raw.content, category="identity",
            status=STATUS_CURRENT, noted_by=str(ctx.get("noted_by") or ""), created_at=raw.created_at,
            record_id=raw.id, source_kind=_USER_CONTEXT_KIND,
        )
    if fact_type:
        return None
    candidate = extract_possessive_declaration(raw.content) or extract_learning_activity(raw.content)
    if candidate is None:
        return None
    return PersonalContextRecord(
        topic=candidate.topic, content=candidate.content, category=candidate.category,
        status=candidate.status, noted_by=str(ctx.get("noted_by") or ""), created_at=raw.created_at,
        record_id=raw.id, value=candidate.value, activity=candidate.activity,
        source_kind=_USER_CONTEXT_KIND,
    )


def _all_records(recall: "_Recall", limit: int, include_legacy: bool) -> "list[PersonalContextRecord]":
    records: list[PersonalContextRecord] = []
    for raw in recall(kind=PERSONAL_CONTEXT_KIND, tags={PERSONAL_CONTEXT_TAG}, limit=limit):
        record = _to_record(raw)
        if record is not None:
            records.append(record)
    if include_legacy:
        for kind, adapt in ((LEARNING_KIND, _legacy_learning_record), (_USER_CONTEXT_KIND, _legacy_user_context_record)):
            try:
                raws = tuple(recall(kind=kind, limit=limit))
            except TypeError:
                # A narrow test double that only understands the canonical
                # query shape - older records simply are not read from it.
                continue
            for raw in raws:
                record = adapt(raw)
                if record is not None:
                    records.append(record)
    # Newest first across every store, so "latest statement wins" holds
    # between a new record and an older one written somewhere else.
    records.sort(key=lambda record: record.created_at, reverse=True)
    return records


def current_personal_context_for(topic: str, *, recall: "_Recall | None") -> "PersonalContextRecord | None":
    """The single, latest statement about ``topic`` - honestly ``None`` when
    nothing has ever been said about it, or no ``recall`` reaches this call."""
    if recall is None:
        return None
    key = normalize_topic_key(topic)
    for record in all_current_personal_context(recall=recall, limit=200):
        if record.topic_key == key:
            return record
    return None


def all_current_personal_context(
    *, recall: "_Recall | None", limit: int = 50, include_legacy: bool = True,
) -> "tuple[PersonalContextRecord, ...]":
    """Every topic's own latest statement - real Memory content, grouped by
    topic key, newest first. ``limit`` bounds how many of the most recent
    raw records per store are considered; a topic whose only mention has
    aged out of that window is honestly absent."""
    if recall is None:
        return ()
    seen_topics: set[str] = set()
    resolved: list[PersonalContextRecord] = []
    for record in _all_records(recall, limit, include_legacy):
        key = record.topic_key
        if not key or key in seen_topics:
            continue
        seen_topics.add(key)
        resolved.append(record)
    return tuple(resolved)


def personal_context_history(topic: str, *, recall: "_Recall | None", limit: int = 200) -> "tuple[PersonalContextRecord, ...]":
    """Every statement ever made about ``topic``, newest first - the history
    that supersession keeps rather than deletes."""
    if recall is None:
        return ()
    key = normalize_topic_key(topic)
    return tuple(record for record in _all_records(recall, limit, True) if record.topic_key == key)


# ---------------------------------------------------------------------------
# Answering "what do you know about me" without a model.
# ---------------------------------------------------------------------------

# "What am I learning?", "what's my schedule?", "do you know my goal?" - a
# question about the user's own stored context. The focus is whatever the
# question names; nothing here knows what it could be.
_QUERY_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?what\s+(?:else\s+)?(?:am|was|have)\s+i\s+(?:been\s+)?"
        r"(?P<focus>[a-z]+(?:ing|ed|nt))\b[^?]*\??\s*$",
        r"^(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?what(?:'s|\s+is|\s+are|\s+was|\s+were)\s+my\s+(?P<focus>[^?.!]+?)\s*[?.!]*$",
        r"^(?:so\s*,?\s+|and\s+)?(?:do\s+you\s+know|can\s+you\s+tell\s+me|tell\s+me|remind\s+me\s+(?:of|what))\s+"
        r"(?:what\s+)?my\s+(?P<focus>[^?.!]+?)(?:\s+(?:is|are))?\s*[?.!]*$",
        # The same question asked with any other question word. "What is my
        # dentist" is not how anyone asks; "who is my dentist" is - and it
        # was not recognised at all, so a fact Alfred had genuinely stored
        # ("my dentist is Dr Ade", accepted and retained) could not be asked
        # back. The focus is still whatever the question names; which word
        # introduced it says nothing about what it is.
        r"^(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?"
        r"(?:who|where|when|which|whose|how\s+much|how\s+many|how\s+often)"
        r"(?:'s|\s+is|\s+are|\s+was|\s+were)\s+my\s+(?P<focus>[^?.!]+?)\s*[?.!]*$",
        # "When do I go to the gym", "where do I work" - a question about a
        # habit, whose focus is the habit itself.
        r"^(?:so\s*,?\s+|and\s+|ok(?:ay)?\s*,?\s+)?"
        r"(?:when|where|how\s+often|what\s+time)\s+do\s+i\s+(?P<focus>[^?.!]+?)\s*[?.!]*$",
    )
)

# Words a question uses to ask, never to name what it is about.
_QUERY_FILLER = frozenset({
    "s", "about", "know", "tell", "remember", "am", "currently", "right", "now",
    "else", "user", "anything", "something", "stuff", "things",
})


def extract_personal_context_query(text: str) -> "str | None":
    """The focus of a question about the user's own stored context ("what
    am I LEARNING", "what's my SCHEDULE"), or None when ``text`` is not
    such a question."""
    working = strip_vocative_address(str(text or "")).strip()
    for pattern in _QUERY_PATTERNS:
        match = pattern.search(working)
        if match is not None:
            focus = _clean_topic(match.group("focus"))
            return focus or None
    return None


def personal_context_query_intent(text: str) -> bool:
    return extract_personal_context_query(text) is not None


def _stem(word: str) -> str:
    for suffix in ("ing", "ied", "ies", "ed", "es", "s", "t"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix in ("ied", "ies"):
                return word[: -len(suffix)] + "y"
            return word[: -len(suffix)]
    return word


def _meaningful_stems(text: str) -> "frozenset[str]":
    return frozenset(_stem(word) for word in tokenize(text) - STOPWORDS - _QUERY_FILLER)


def match_personal_context(
    items: "Iterable[PersonalContextRecord]", focus: str,
) -> "tuple[PersonalContextRecord, ...]":
    """Which stored records a question's ``focus`` is about - literal overlap
    between the question's own words and each record's topic, content,
    category and activity, compared by stem ("learning"/"learned" both name
    "learn"). Best overlap first, then newest. Never a guess: a record that
    shares no word with the question is not returned."""
    wanted = _meaningful_stems(focus)
    listed = tuple(items)
    if not wanted:
        return listed
    scored = []
    for position, item in enumerate(listed):
        have = _meaningful_stems(" ".join((item.topic, item.content, item.category, item.activity)))
        score = len(wanted & have)
        if score:
            scored.append((score, position, item))
    scored.sort(key=lambda entry: (-entry[0], entry[1]))
    return tuple(item for _score, _position, item in scored)


_SECOND_PERSON_EXCEPTIONS = {"has": "have", "is": "are", "goes": "go", "does": "do", "was": "were"}


def _second_person_verb(verb: str) -> str:
    """"goes" -> "go", "works" -> "work" - the inverse of the third-person
    agreement a stored statement is written with."""
    word = str(verb or "")
    lowered = word.lower()
    if lowered in _SECOND_PERSON_EXCEPTIONS:
        return _SECOND_PERSON_EXCEPTIONS[lowered]
    if lowered.endswith("ies") and len(lowered) > 4:
        return lowered[:-3] + "y"
    if lowered.endswith(("shes", "ches", "sses", "xes", "zes", "oes")):
        return lowered[:-2]
    if lowered.endswith("s") and not lowered.endswith("ss"):
        return lowered[:-1]
    return lowered


def describe_for_user(record: PersonalContextRecord) -> str:
    """One stored statement as it reads when said back to the person - the
    same fact, in the second person ("The user is learning X" -> "You're
    learning X")."""
    text = str(record.content or "").strip()
    stated = re.match(r"^The user stated \(regarding (?P<topic>[^)]*)\):\s*(?P<rest>.*)$", text)
    if stated:
        return f"About your {stated.group('topic')}: {stated.group('rest')}".rstrip(".") + "."
    replacements = (
        (r"^The user's\b", "Your"),
        (r"^The user is\b", "You're"),
        (r"^The user has\b", "You've"),
        (r"^The user was\b", "You were"),
        (r"^The user attends\b", "You attend"),
        # A habitual statement is stored in the third person ("The user goes
        # to the gym"), and the person is addressed in the second ("you go").
        # Without the agreement, the answer read "you goes to the gym".
        (r"^The user\s+(?P<verb>[a-z][a-z\-]*)\b", lambda m: "You " + _second_person_verb(m.group("verb"))),
        (r"^The user\b", "You"),
        (r"^my\b", "Your"),
        (r"\btheir\b", "your"),
        (r"\bmy\b", "your"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    text = text[:1].upper() + text[1:]
    return text if text.endswith((".", "!", "?")) else text + "."
