"""What the person is studying - the grammar of a first-person study
statement, and nothing else.

**The distinction this module exists to hold, stated once so nothing
downstream has to re-derive it.** There are four separable ways Alfred
can know something, and only one of them belongs in Memory:

    general model knowledge   the Brain's own. Not stored, not storable,
                              and not the user's - see
                              ``src/providers/knowledge.py``.
    user documents/books      material the person supplied. Lives in
                              their own files and is *quoted*, never
                              copied into a memory store.
    live information          the present moment. Decays, so retaining it
                              would be retaining something that is about
                              to be false.
    WHAT THE USER IS STUDYING a stable, supersedable fact about the person
                              that should change how a future answer is
                              pitched.

**One store, not two (Pre-Stage-9 Campaign 1).** This module used to build
its own ``learning``-kind record, a second store of user knowledge beside
``personal_context.py``'s topic-keyed one - "I'm learning X" and "I've
learned X" then lived in different places, superseded nothing, and were
read by different code. It now only *recognises* the statement; the one
canonical writer is ``personal_context.py`` (``extract_learning_activity``),
which turns it into an ordinary personal-context record whose topic is the
subject. ``LEARNING_KIND`` survives solely so records written before this
change are still read back (see ``personal_context.all_current_personal_context``).

**Recognised by request shape, never by subject.** No subject, course,
book, exam board, school, country or discipline appears below and none
may be added. A marker earns its place here only by being a *first-person
statement of ongoing study*, true of any subject a person could ever
name: whatever follows the marker **is** the subject, stored as a plain
string, never matched against a list of known subjects.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# The kind older learning records were written under. Read-only now: no
# code path writes it (see this module's own docstring).
LEARNING_KIND = "learning"

# Adverbs a person naturally puts between "I'm" and the study verb ("I'm
# also learning", "I am still studying"). Grammar, never a subject.
_ADVERBS = r"(?:(?:also|still|currently|now|really|actively|just|already|even)\s+)*"
_STUDY_VERB = r"(?P<verb>studying|learning|revising|reading|practi[cs]ing)"
_PREPOSITION = r"(?P<prep>about\s+|for\s+)?"

# First-person statements of ongoing or newly-begun study. Whatever follows
# the verb is the subject, captured by the group - the code never reads it
# as anything but text.
#
# "reading" is included and "read" is not, deliberately: "I'm reading X"
# states a current activity, while "read X" is an instruction to somebody.
_LEARNING_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(pattern, re.IGNORECASE), tense)
    for pattern, tense in (
        (rf"\bi(?:'m|\s+am|m)\s+{_ADVERBS}{_STUDY_VERB}\s+{_PREPOSITION}(?P<subject>[^.!?]+)", "ongoing"),
        (rf"\bi(?:'m|\s+am|m)\s+{_ADVERBS}(?P<verb>preparing)\s+(?P<prep>for\s+)(?P<subject>[^.!?]+)", "ongoing"),
        # "taking" needs the noun as well: "I'm taking a course on X" states
        # study, "I'm taking the bus" states nothing this module should keep.
        (rf"\bi(?:'m|\s+am|m)\s+{_ADVERBS}(?P<verb>taking)\s+(?P<prep>a\s+|an\s+|the\s+)?"
         r"(?P<subject>[^.!?]*\b(?:course|class|module|paper)\b[^.!?]*)", "ongoing"),
        # A study that has just begun - "I've started learning X", "I began
        # studying X". Same subject slot, reported as started.
        (rf"\bi(?:'ve|\s+have)?\s+(?:just\s+|recently\s+)?(?:started|begun|began)\s+{_STUDY_VERB}\s+"
         rf"{_PREPOSITION}(?P<subject>[^.!?]+)", "started"),
        (rf"\bi(?:'m|\s+am|m)\s+{_ADVERBS}starting\s+to\s+(?P<verb>study|learn|revise|read|practi[cs]e)\s+"
         rf"{_PREPOSITION}(?P<subject>[^.!?]+)", "started"),
    )
)

# A leading "Alfred, " / "Hey Arthur, " address, stripped before extraction
# so who was spoken to is never mistaken for what was said. The identical
# shape-based recogniser ``capture.py`` already uses - no agent is named.
_VOCATIVE_ADDRESS = re.compile(
    r"^(?:hey|hi|hello)?\s*[A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2}\s*,\s*", re.IGNORECASE
)

# Words a person tacks onto the end of a statement that belong to the
# sentence, never to what it is about ("I'm learning X too" is about X).
# Filler grammar, the same role _TOPIC_STOPWORDS plays in personal_context.py.
_TRAILING_FILLER = re.compile(
    r"(?:\s*,?\s+(?:too|as\s+well|also|now|lately|these\s+days|at\s+the\s+moment|right\s+now|anymore|any\s+more))+\s*$",
    re.IGNORECASE,
)

# A subject is a phrase, not an essay. A "subject" longer than this is a
# sentence that happened to open with a study verb, and storing it would
# put prose in a field every later reader treats as a label.
_MAX_SUBJECT_CHARS = 120

_GERUND_OF = {
    "study": "studying", "learn": "learning", "revise": "revising", "read": "reading",
    "practise": "practising", "practice": "practicing",
}


@dataclass(frozen=True)
class LearningStatement:
    """One recognised study statement. ``activity`` is the study verb as a
    gerund ("learning"), ``subject`` the text itself (never a category),
    ``preposition`` whatever joined the two as it was said ("for ", "about
    ", or empty), and ``tense`` either "ongoing" or "started"."""

    activity: str
    subject: str
    tense: str
    preposition: str = ""


def strip_vocative_address(text: str) -> str:
    """``text`` without a leading "Name, " address - who was spoken to is
    never part of what was said."""
    stripped = str(text or "").strip()
    return _VOCATIVE_ADDRESS.sub("", stripped, count=1).strip() or stripped


def strip_trailing_filler(text: str) -> str:
    """``text`` without sentence-level filler at its end ("too", "as well",
    "now", "these days")."""
    return _TRAILING_FILLER.sub("", str(text or "")).strip(" ,;:")


def learning_intent(text: str) -> bool:
    """Whether this text states what the person is studying."""
    return extract_learning_statement(text) is not None


def extract_learning_statement(text: str) -> "LearningStatement | None":
    """The study statement ``text`` makes, or None."""
    stripped = str(text or "").strip()
    if not stripped:
        return None

    working = strip_vocative_address(stripped)
    for pattern, tense in _LEARNING_PATTERNS:
        match = pattern.search(working)
        if match is None:
            continue
        subject = strip_trailing_filler(" ".join(match.group("subject").split()))
        if not subject:
            continue
        if len(subject) > _MAX_SUBJECT_CHARS:
            subject = subject[:_MAX_SUBJECT_CHARS].rstrip() + " ..."
        verb = match.group("verb").lower()
        preposition = (match.groupdict().get("prep") or "").lower()
        if verb == "taking":
            # "a course on X" - the article belongs to the subject phrase.
            subject, preposition = f"{preposition}{subject}".strip(), ""
        return LearningStatement(
            activity=_GERUND_OF.get(verb, verb), subject=subject, tense=tense,
            preposition=" ".join(preposition.split()) + (" " if preposition.strip() else ""),
        )
    return None


def extract_learning_subject(text: str) -> str | None:
    """The subject the person said they are studying, or None."""
    statement = extract_learning_statement(text)
    return statement.subject if statement is not None else None
