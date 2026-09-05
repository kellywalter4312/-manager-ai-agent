"""Turning something said in a session into something worth keeping.

A conversation and a memory answer different questions:

    session      what are we talking about right now?
    memory       what should change a future decision?

Most of a conversation is the first and only the first. "What did you find?",
"try the other one", "thanks" - all useful in the moment, all noise a week
later. A system that persisted every utterance would be building the
history-instead-of-knowledge store the memory policy already exists to prevent,
and it would do it at the fastest possible rate.

So this module does the smallest honest thing: it finds the places where a
person **asked** for something to be kept.

    "remember that we are building Project Beacon"   -> a candidate
    "what did you find?"                             -> not a candidate

**Candidates, not memories.** Nothing here writes anything. It produces records
and hands them back; whether any of them is retained is decided by the agent's
own MemoryPolicy, through the agent's own governed write path. Extraction and
admission stay separate for the same reason Memory and MemoryPolicy do - one
proposes, the other is the authority.

**Deterministic on purpose.** No model is consulted, so this behaves identically
in Half Brain. Judging what "mattered" in a conversation without being asked is
exactly the kind of inference that needs a model and that degrades quietly when
it is wrong; asking is cheaper and honest. Model-judged extraction is a Full
Brain upgrade layered on top of this floor, never a replacement for it.

**Provenance travels.** A retained note records the conversation it came from,
so a record read back months later can say where it was learned - the same rule
an observation follows through ToolResult.

This module imports one thing from the project - the record type it builds -
and knows nothing about Conversation, agents, brains or stores.
"""

from __future__ import annotations

from typing import Iterable

from src.services.memory.memory import MemoryRecord

# The phrasings that mean "keep this". Deliberately explicit and few.
#
# Every one of these is a person *asking*, which is the whole point: the
# alternative is guessing which sentences mattered, and a guess that is wrong
# fills a durable store with things nobody chose. Adding a phrasing here is
# cheap; adding inference is not.
RETENTION_MARKERS: tuple[str, ...] = (
    "remember that",
    "remember this:",
    "note that",
    "make a note that",
    "keep in mind that",
    "don't forget that",
    "do not forget that",
    "for future reference,",
)

# What a retained note is filed as. USER_CONTEXT is the Manager policy's
# category for "a stable fact about the person or the project that should
# inform future decisions", which is precisely what somebody asking to be
# remembered is describing.
RETAINED_KIND = "user_context"

# Where a retained note came from. Recorded so a record read back later can
# say how it was learned rather than appearing as an unattributed assertion.
RETAINED_SOURCE = "conversation"


def _asked_to_keep(text: str) -> str | None:
    """The part of an utterance a person asked to be remembered, if any.

    Returns what follows the marker rather than the whole sentence: "remember
    that we are building Project Beacon" is a request to keep *the project*,
    not to keep the request. Storing the instruction alongside the fact would
    make every recalled note read like a transcript.
    """
    lowered = text.lower()
    for marker in RETENTION_MARKERS:
        position = lowered.find(marker)
        if position == -1:
            continue
        remainder = text[position + len(marker):].strip(" ,.:;\t\n")
        if remainder:
            return remainder
    return None


def retention_requests(
    said: Iterable[str],
    *,
    conversation_id: str = "",
    kind: str = RETAINED_KIND,
) -> tuple[MemoryRecord, ...]:
    """Candidate records for the things a person asked to be remembered.

    ``said`` is the text of utterances - the caller decides whose. That
    deliberately keeps this function free of any knowledge of participants,
    rooms or agents: it reads sentences.

    Returns candidates in the order they were said. An empty tuple is the
    normal answer for an ordinary conversation, and is not a failure.

    Nothing is written. The caller offers these to an agent's own policy, and
    the policy decides - which is what stops this becoming a way to put things
    into a store without passing the rules that guard it.
    """
    candidates: list[MemoryRecord] = []
    for text in said:
        content = _asked_to_keep(str(text or ""))
        if not content:
            continue
        candidates.append(
            MemoryRecord(
                content=content,
                kind=kind,
                # Provenance, in the opaque context field Memory carries and
                # never interprets. A note recalled in a later session can say
                # it was learned in conversation, and which one.
                context={
                    "source": RETAINED_SOURCE,
                    "conversation": conversation_id,
                },
            )
        )
    return tuple(candidates)
