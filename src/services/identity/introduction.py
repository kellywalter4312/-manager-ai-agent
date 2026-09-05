"""Turning a loaded identity into the words an agent uses to introduce itself.

**The defect this exists to remove.** Every agent in this system loads a real
identity document - a name, a role, a purpose, a description, an area of
responsibility - and hands it to every handler that runs. All of it arrived
correctly and none of it was ever *said*. Asked "hello", the orchestrator
answered a fixed string with no name in it. Asked "who are you", it answered
that it could not reason about that yet - which is false: answering "who are
you" from a document that names you needs no reasoning at all, and reporting a
missing capability as a missing model is worse than either failing.

**What this module is.** One place that renders an already-loaded identity into
a sentence. It is not a conversation system, not a response library, and not a
second brain:

- It **composes, never invents.** Every content word in what it returns is read
  from the identity document. If a document declares nothing, the caller is
  told so (``None``) and answers honestly rather than receiving filler.
- It **holds no knowledge.** There is no table of agents, no list of names, no
  branch on who an agent is. It is given an identity and returns words about
  that identity, so an agent registered tomorrow introduces itself correctly
  without a line changing here.
- It **decides nothing.** Which handler runs, which agent a task reaches, and
  whether a model is consulted are all decided exactly where they already
  were. This is called after those decisions, never instead of them.

**Why it is shared.** ``AgentIdentity`` (the orchestrator's, from IDENTITY.md)
and ``SpecialistIdentity`` (a specialist's, from identities/*.md) are separate
types that already declare the same four fields for the same four reasons. A
structural protocol lets both use this without either being made to depend on
the other, and without a second copy of the same sentences existing in two
places and drifting.

**The honesty boundary, stated once.** Personality is deliberately NOT
performed here. A document saying an agent is "warm, friendly and
conversational" describes how a *model* should write as that agent; a
deterministic renderer that tried to sound warm would be imitating a
personality rather than expressing one, which is the fabrication this project
refuses everywhere else. So personality is read for one honest purpose only -
whether to end a greeting with an offer of help - and its actual expression
stays where it belongs, in the prompt an LLM brain builds when one exists.
"""

from __future__ import annotations

import re
from typing import Protocol, Sequence, runtime_checkable

__all__ = [
    "IdentityLike",
    "identity_question",
    "self_introduction",
    "greeting_from_identity",
]


@runtime_checkable
class IdentityLike(Protocol):
    """The four fields both identity types already declare.

    Structural on purpose: neither ``AgentIdentity`` nor ``SpecialistIdentity``
    is imported here and neither has to know this protocol exists. A future
    third identity type is covered the moment it carries the same fields.
    """

    name: str
    role: str
    purpose: str
    description: str


# ---------------------------------------------------------------------------
# Recognising a question about identity
# ---------------------------------------------------------------------------

# Asked as whole phrases, not as a bag of words. "who are you" is a question
# about identity; "who should I send this to" contains "who" and is not. Word
# overlap cannot tell those apart, which is exactly the case ManagerCapability
# .claims exists for - see its own comment.
_IDENTITY_PHRASES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"\bwho\s+(?:are|r)\s+(?:you|u)\b",
        r"\bwho\s+am\s+i\s+(?:talking|speaking|chatting)\s+(?:to|with)\b",
        r"\bwhat(?:'s|\s+is|\s+are)?\s+your\s+name\b",
        r"\bwhat\s+(?:do|can)\s+you\s+do\b",
        r"\bwhat\s+are\s+you\b",
        r"\bwhat\s+is\s+your\s+(?:role|job|purpose)\b",
        r"\bintroduce\s+yourself\b",
        r"\btell\s+me\s+about\s+yourself\b",
    )
)

# A question about identity is short. "who are you going to delegate the
# quarterly review to, and what do you do about the ones that fail" opens with
# an identity phrase and is not an identity question - it is real work with a
# turn of phrase in front of it. The same cap, for the same reason,
# conversational_intent already applies to "thanks".
_MAX_IDENTITY_QUESTION_WORDS = 10


def identity_question(text: str) -> bool:
    """Whether this whole message is asking who the agent is.

    Deliberately conservative. A false negative costs an ordinary answer from
    an ordinary handler; a false positive answers a real request with a
    biography, which is worse.
    """
    lowered = (text or "").strip().lower()
    if not lowered:
        return False
    if len(lowered.split()) > _MAX_IDENTITY_QUESTION_WORDS:
        return False
    return any(pattern.search(lowered) for pattern in _IDENTITY_PHRASES)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _first_sentence(text: str, limit: int = 200) -> str:
    """The opening statement of a document field, without its elaboration.

    These fields are prose written for a person to read, and often run to
    several sentences. An introduction wants the first of them. Split on a
    full stop followed by a space so "gmail.readonly" and "0.1.0" survive.
    """
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    first = parts[0].strip() if parts else cleaned
    # A dash in these documents separates the statement from its gloss
    # ("Orchestrator and coordinator - the agent that receives a mission...").
    # The statement is the half an introduction wants.
    head = re.split(r"\s+[-–—]\s+", first, maxsplit=1)[0].strip()
    # Only a degenerate head - an empty one, or a stray initial - is worth
    # discarding. The first version of this guard demanded twelve characters,
    # which threw away every genuinely short role ("Scheduler", "QA") and kept
    # the entire explanatory gloss instead, producing exactly the run-on
    # sentence the split exists to prevent.
    if len(head) < 3:
        head = first
    return head[:limit].rstrip(" .,;:") if len(head) > limit else head.rstrip(" .,;:")


def _lower_first(text: str) -> str:
    """Lower an initial capital only when the word is not itself a name.

    "Orchestrator and coordinator" reads correctly mid-sentence as
    "orchestrator and coordinator"; "Gmail" does not become "gmail". A single
    leading word that is capitalised and followed by another capitalised word
    is left alone.
    """
    if not text:
        return text
    words = text.split()
    if len(words) > 1 and words[0][:1].isupper() and words[1][:1].isupper():
        return text
    return text[:1].lower() + text[1:]


def self_introduction(
    identity: IdentityLike | None,
    capabilities: Sequence[str] = (),
) -> str | None:
    """Who this agent is, composed from its own loaded document.

    Returns ``None`` when the identity declares nothing to say - the caller
    then answers honestly rather than being handed an empty sentence dressed
    up as an answer. Never consults a model, never reads memory, and never
    reports a capability the caller did not pass in.

    ``capabilities`` is accepted and deliberately NOT listed. The first
    version of this ended every introduction with the agent's own real
    capability set, which is honest and reads terribly: those are router
    match words, so the orchestrator introduced itself with fifty-seven of
    them ("about, afternoon, alright, appreciate...") and the specialist with
    seventeen. Router vocabulary is backend architecture, and reciting it at
    a person is exactly the leak this product is trying to stop - what an
    agent does is already stated, in prose written for a person, in the
    ``description`` field above. The parameter stays in the signature because
    the caller genuinely has the list and a future surface built to *show*
    capabilities properly should be handed it rather than having to find it
    again.
    """
    if identity is None:
        return None
    name = (getattr(identity, "name", "") or "").strip()
    if not name:
        return None

    # What it is, then what it does. Role answers the first and description
    # the second; either may be absent, and a document that declares only one
    # gets a shorter, still-true sentence rather than a padded one.
    role = _first_sentence(getattr(identity, "role", "") or "")
    description = _first_sentence(getattr(identity, "description", "") or "", limit=240)

    parts: list[str] = []
    if role:
        parts.append(f"I'm {name} - {_lower_first(role)}.")
    else:
        parts.append(f"I'm {name}.")
    if description and _lower_first(description) != _lower_first(role):
        parts.append(f"{description}.")

    if not role and not description:
        # A name and nothing else is still a true answer, and a better one
        # than a fabricated job description.
        purpose = _first_sentence(getattr(identity, "purpose", "") or "")
        if purpose:
            parts.append(f"{purpose}.")

    return " ".join(parts)


def greeting_from_identity(identity: IdentityLike | None) -> str | None:
    """A greeting that says who is greeting.

    The failure this replaces was one fixed string - "Hello! What can I help
    you with?" - returned by every agent in the system, so a person who had
    just addressed one specific agent by name got a reply that could have come
    from any of them, or from nothing at all.

    Short on purpose: a greeting is not an introduction, and answering "hi"
    with a paragraph is its own kind of wrong. Returns ``None`` when the
    identity has no name, so the caller keeps its own honest fallback.
    """
    if identity is None:
        return None
    name = (getattr(identity, "name", "") or "").strip()
    if not name:
        return None
    role = _first_sentence(getattr(identity, "role", "") or "")
    if role:
        return f"{name} here - {_lower_first(role)}. What do you need?"
    return f"{name} here. What do you need?"
