"""What counts as an ordinary conversational turn, in one place.

**Why this module exists.** The vocabulary below - greetings, thanks, plain
acknowledgements - and the rule for telling a real request apart from a turn
that merely contains one of those words, were written for the orchestrator's
own capability layer and lived inside it. That was correct while the
orchestrator was the only thing anyone spoke to.

It is not correct now. A specialist addressed directly - "hello Arthur" -
reached ``EmailAgent.execute`` and fetched the inbox, because a specialist has
no capability floor: every message it receives is domain work by construction.
So the specialist answered "hello" with six email subjects, and answered "who
are you" with the same six email subjects.

Fixing that by copying the vocabulary into the agent layer would create two
lists of greeting words that drift. Fixing it by importing the orchestrator's
capability module into an agent would point a dependency upwards, from the
layer that does the work to the layer that coordinates it - and
``tests/test_architecture_audit.py`` exists partly to keep that from
happening.

So the shared part moves down here, into services, which both layers already
depend on. ``src/manager/capabilities.py`` imports these names and re-exports
them under the names it always used, so nothing that already read them
changed; ``src/agents/cognitive_agent.py`` imports the same ones. One
vocabulary, one rule, two callers.

**What this is not.** It is not a conversation system, not a router and not a
response library. It recognises a shape of message and says so. What to *say*
in reply is composed from the answering agent's own identity, next door in
``src/services/identity/introduction.py``, and which agent answers at all is
decided where it always was.
"""

from __future__ import annotations

import re
from typing import Sequence

from src.core.contracts import Task, tokenize

__all__ = [
    "GREETING_WORDS",
    "THANKS_WORDS",
    "ACKNOWLEDGEMENT_WORDS",
    "CONVERSATIONAL_WORDS",
    "CONVERSATIONAL_FILLER",
    "conversational_intent",
    "asks_for_explanation",
]


GREETING_WORDS: frozenset[str] = frozenset(
    {
        "hello", "hi", "hey", "greetings", "morning", "afternoon", "evening",
        "good",
    }
)

THANKS_WORDS: frozenset[str] = frozenset(
    {"thanks", "thank", "cheers", "appreciate", "appreciated"}
)

ACKNOWLEDGEMENT_WORDS: frozenset[str] = frozenset(
    {
        "okay", "ok", "alright", "understood", "noted", "got", "it",
        "sounds", "good", "great", "perfect", "cool", "nice", "awesome",
        "yep", "yeah", "yes", "sure", "roger", "no", "nope", "nah",
    }
)

CONVERSATIONAL_WORDS: frozenset[str] = (
    GREETING_WORDS | THANKS_WORDS | ACKNOWLEDGEMENT_WORDS
)

# Words that carry no intent of their own and do not disqualify a turn from
# being conversational - "thank you so much" is still just thanks, and
# "hi there" is still just hi. "there" was missing, and its absence sent
# "hi there" to a specialist as domain work.
CONVERSATIONAL_FILLER: frozenset[str] = frozenset(
    {"you", "so", "very", "much", "a", "lot", "there", "again",
     # Phase 11 closure: "thanks, that's great" and "nice one" reached open
     # conversation - and in Half Brain were told no reasoning was
     # configured - because "that", the "s" of "that's" and "one" carry no
     # intent of their own either.
     "that", "s", "one", "is", "really", "just"}
)


def conversational_intent(task: Task, participants: Sequence[str] = ()) -> bool:
    """Whether this whole message is a short conversational turn - a
    greeting, a thanks, or a plain acknowledgement - rather than a
    substantive request that merely contains one of those words.

    Checked on the *whole* message, deliberately not by word-overlap
    scoring: "thanks for finding that, can you also check for remote
    roles" contains "thanks" and is a new, substantive request, and score
    overlap alone cannot tell the two apart. Every meaningful token must
    be conversational - not merely one of them - so a request that mixes
    an acknowledgement with real work is correctly left for something
    else to answer, never swallowed here.

    Capped at a handful of words on purpose: this is for "thanks", not
    for a longer message that happens to open with one.

    ``participants`` are the names in play - the roster, when the
    orchestrator asks; the agent's own name, when a specialist does. They
    are discounted, because addressing someone by name does not stop a
    greeting being a greeting: "hello Arthur" is the same turn as "hello",
    and before this was used, the name token made it fail the subset test
    and fall through to the specialist as mailbox work. The parameter was
    already on this signature and already supplied by both callers; it was
    simply never read.

    Discounting a name can only ever make a message MORE conversational,
    never less, and only for names that are genuinely registered - so it
    cannot swallow a real request unless that request consists of nothing
    but greetings and a name.
    """
    tokens = tokenize(task.description)
    if not tokens or len(tokens) > 6:
        return False
    named = {str(p).strip().lower() for p in participants if str(p).strip()}
    meaningful = tokens - CONVERSATIONAL_FILLER - named
    if not meaningful:
        return False
    return meaningful.issubset(CONVERSATIONAL_WORDS)


# An explanatory question - "why do X so often Y?", "what causes X?", "how come
# X?". Openers only: nothing here names a domain, a subject or a capability.
_EXPLANATORY_OPENER = re.compile(
    r"^(?:so\s+|and\s+|but\s+|ok(?:ay)?,?\s+)?(?:please\s+)?"
    r"(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?"
    r"(?:(?:explain|tell\s+me)\s+(?:why|how|what|who)\b"
    r"|why\b"
    r"|how\s+(?:come|do|does|did|can|could|would|is|are|was|were)\b"
    r"|what(?:'s|\s+is|\s+are|\s+was|\s+were|\s+makes|\s+causes|\s+happens)\b"
    r"|who(?:'s|\s+is|\s+are|\s+was|\s+were)\b"
    r"|when\s+(?:did|was|were|do|does)\b"
    r"|where\s+(?:is|are|did|do|does)\b)",
    re.IGNORECASE,
)
# A reference to the person's own material, or to something already in play.
# Grammatical, never a vocabulary: this is what separates "what's in my inbox"
# (work with the person's own things) from "what makes phishing work"
# (a question about the subject).
_POINTS_AT_OWN_MATERIAL = re.compile(
    r"\b(?:my|mine|our|ours|me|i|we|us|this|that|these|those|it|its|"
    r"yours|your|you)\b",
    re.IGNORECASE,
)


def _without_address(text: str, names: Sequence[str]) -> str:
    """``text`` with a leading or trailing address to one of ``names`` removed -
    "Arthur, why does X happen?" -> "why does X happen?". Addressing someone by
    name does not change what is being asked, the same reason
    ``conversational_intent`` discounts the same names from its own tokens."""
    working = " ".join(str(text or "").split())
    for name in sorted({str(n).strip() for n in names if str(n).strip()}, key=len, reverse=True):
        working = re.sub(rf"^{re.escape(name)}\s*[,:]\s*", "", working, flags=re.IGNORECASE)
        working = re.sub(rf"\s*,\s*{re.escape(name)}\s*([?.!]*)$", r"\1", working, flags=re.IGNORECASE)
    return working.strip()


def asks_for_explanation(text: str, names: Sequence[str] = ()) -> bool:
    """Whether this message asks for an explanation *about* a subject rather
    than for work with the person's own material.

    The specialist layer's counterpart to ``conversational_intent``, and it
    exists for the next step of the same problem this module was created for.
    A domain specialist treats everything it is handed as domain work unless
    something says otherwise; the zero-overlap case was already handled ("tell
    me about Napoleon" names none of the agent's vocabulary and goes to
    conversation). What was not handled is a *question about the domain*: one
    token of overlap was enough to make "why do phishing emails so often use
    urgent language?" fetch and list six real inbox messages, found live with
    the real local model during the Phase 11 closure campaign.

    Purely grammatical, so it generalizes to a domain nobody has written yet:
    an explanatory opener, and no possessive or deictic pointing at the
    person's own things. Deliberately conservative in the same direction
    ``identity_question`` is - a false negative costs only today's behaviour,
    while a false positive would answer real work with a discussion of it.
    """
    working = _without_address(text, names)
    if not working:
        return False
    if _EXPLANATORY_OPENER.match(working) is None:
        return False
    return _POINTS_AT_OWN_MATERIAL.search(working) is None
