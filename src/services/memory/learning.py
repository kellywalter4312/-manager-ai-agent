"""What the person is studying - a fact about the *user*, kept where every
other fact about the user already lives.

**The distinction this module exists to hold, stated once so nothing
downstream has to re-derive it.** There are four separable ways Alfred
can know something, and only one of them belongs in Memory:

    general model knowledge   the Brain's own. Not stored, not storable,
                              and not the user's - see
                              ``src/providers/knowledge.py``.
    user documents/books      material the person supplied. Lives in
                              their own files and is *quoted*, never
                              copied into a memory store: a book is not
                              knowledge about the user, and admitting one
                              would fill the store with a dump of
                              something that belongs elsewhere - exactly
                              what ManagerMemoryPolicy's length rule
                              already refuses.
    live information          the present moment. Decays, so retaining it
                              would be retaining something that is about
                              to be false.
    LEARNING CONTEXT          THIS module. "I am studying X", "I am
                              reading Y", "I am preparing for Z". A
                              stable fact about the person that should
                              change how a future answer is pitched -
                              which is the entire admission test
                              ``src/manager/memory_policy.py`` states for
                              a category existing at all.

**Recognised by request shape, never by subject.** No subject, course,
book, exam board, school, country or discipline appears below and none
may be added. A marker earns its place here only by being a *first-person
statement of ongoing study*, true of any subject a person could ever
name: whatever follows the marker **is** the subject, stored as a plain
string, never matched against a list of known subjects. A field of study
nobody has mentioned yet needs no change here.

**Shared, never duplicated per agent.** This module knows nothing about
Alfred, Arthur, ManagerCapability or CapabilityRequest - it turns text
into a candidate ``MemoryRecord``, the same neutral shape
``build_recording`` already returns, and the caller offers it to
whichever agent's own governed policy applies. The same one-extraction-
two-callers rule ``src/services/memory/capture.py`` already documents.
"""

from __future__ import annotations

import re

from src.services.memory.memory import MemoryRecord

LEARNING_KIND = "learning"
LEARNING_SOURCE = "conversation"

# First-person statements of ongoing or intended study. Whatever follows
# the verb is the subject, captured by the group - the code never reads it
# as anything but text.
#
# "reading" is included and "read" is not, deliberately: "I'm reading X"
# states a current activity, while "read X" is an instruction to somebody.
# The same reason ``capture.py`` matches "record this" and not "record".
_LEARNING_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?studying\s+(?P<subject>[^.!?]+)",
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?learning\s+(?:about\s+)?(?P<subject>[^.!?]+)",
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?revising\s+(?:for\s+)?(?P<subject>[^.!?]+)",
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?reading\s+(?P<subject>[^.!?]+)",
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?preparing\s+for\s+(?P<subject>[^.!?]+)",
        # "taking" needs the noun as well: "I'm taking a course on X" states
        # study, "I'm taking the bus" states nothing this module should keep.
        r"\bi(?:'m|\s+am)\s+(?:currently\s+)?taking\s+(?:a\s+|an\s+|the\s+)?"
        r"(?P<subject>[^.!?]*\b(?:course|class|module|paper)\b[^.!?]*)",
        r"\bim\s+(?:currently\s+)?(?:studying|learning|revising|reading)\s+"
        r"(?:about\s+|for\s+)?(?P<subject>[^.!?]+)",
    )
)

# A leading "Alfred, " / "Hey Arthur, " address, stripped before extraction
# so who was spoken to is never mistaken for what was said. The identical
# shape-based recogniser ``capture.py`` already uses - no agent is named.
_VOCATIVE_ADDRESS = re.compile(
    r"^(?:hey|hi|hello)?\s*[A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2}\s*,\s*", re.IGNORECASE
)

# A subject is a phrase, not an essay. A "subject" longer than this is a
# sentence that happened to open with a study verb, and storing it would
# put prose in a field every later reader treats as a label.
_MAX_SUBJECT_CHARS = 120


def learning_intent(text: str) -> bool:
    """Whether this text states what the person is studying - the
    ``claims=`` predicate a capability uses to recognise its own request
    shape before word-overlap scoring ever runs, the same instrument
    ``capture_intent`` and ``forget_intent`` already are."""
    return extract_learning_subject(text) is not None


def extract_learning_subject(text: str) -> str | None:
    """The subject the person said they are studying, or None.

    Returns the text itself, normalised and bounded - never a category,
    never a lookup, never a guess. A historical period, a scientific
    discipline, a professional certification and a book title are all
    handled by the same line of code, because not one of them is
    recognised: the sentence shape is. The examples this was specified
    with live in its tests, where examples belong.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return None

    addressed = _VOCATIVE_ADDRESS.sub("", stripped, count=1).strip()
    working = addressed or stripped

    for pattern in _LEARNING_PATTERNS:
        match = pattern.search(working)
        if match is None:
            continue
        subject = " ".join(match.group("subject").split()).strip(" ,;:")
        if not subject:
            continue
        if len(subject) > _MAX_SUBJECT_CHARS:
            subject = subject[:_MAX_SUBJECT_CHARS].rstrip() + " ..."
        return subject
    return None


def build_learning_record(
    text: str,
    *,
    agent_name: str,
    kind: str = LEARNING_KIND,
) -> MemoryRecord | None:
    """One candidate learning-context record, or None when the text does
    not state one at all.

    ``agent_name`` travels in ``context`` as provenance - who was told
    this - the same rule ``build_recording`` already follows. Never used
    to reach into another agent's store: the caller decides whose memory
    this candidate is offered to.

    Carries full confidence (1.0) for the same reason a capture does: a
    person saying what they are studying is a statement, not a hunch, and
    treating it as tentative would refuse the exact thing this function
    exists to keep.
    """
    subject = extract_learning_subject(text)
    if subject is None:
        return None

    return MemoryRecord(
        # The subject, not the whole utterance. What should change a
        # future explanation is *what they are studying*; the sentence
        # they happened to say it in is conversation.
        content=f"Currently studying: {subject}",
        kind=kind,
        tags={"learning"},
        context={
            "subject": subject,
            "confidence": 1.0,
            "source": LEARNING_SOURCE,
            "noted_by": agent_name,
        },
    )
