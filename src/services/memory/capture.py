""""Record this" - an explicit request to capture something for later,
kept distinct from "remember that X" on purpose.

**Memory vs. Recording, stated once, here, so nothing downstream has to
re-derive the distinction:**

    remember this      the user wants information retained as durable
                        knowledge/context - session.py's own territory,
                        unchanged by this module
    record this         the user wants something captured for later
                        retrieval - a note, an idea, a thing to come back
                        to - which is a *different* request even though
                        both end up in the same governed store
    record this for X   the same capture, with an explicit purpose the
                        record carries alongside it

Ordinary conversation does not become a recording merely by existing.
Exactly as ``session.py`` finds only what a person **asked** to be kept,
this module finds only what a person **asked** to be recorded - a small,
fixed set of phrasings, deterministic, no model, no guessing.

**Generic across purposes, not one branch per purpose.** "for later",
"for research", "for the email research" all flow through the same
extraction: whatever follows "for" *is* the purpose, stored as a plain
string, never matched against a fixed list of known purposes. A future
purpose nobody has said yet needs no change here.

**Shared by every agent that captures, never duplicated per agent.**
This module knows nothing about Alfred, Arthur, ManagerCapability or
CapabilityRequest - it turns text into a candidate ``MemoryRecord``, the
same neutral shape ``retention_requests`` already returns, and the
caller (a Manager capability, or a specialist's own request handling)
offers it to whichever agent's own governed policy applies.
"""

from __future__ import annotations

import re

from src.services.memory.memory import MemoryRecord

# The verb a capture request opens with. "record" and "keep" both mean
# the same thing here; neither implies a category as narrow as
# "remember" does.
#
# 0.2 Reality Audit fix: a colon stops the purpose clause too, not only
# sentence-ending punctuation. "record this for later: pick up the dry
# cleaning" is the ordinary, one-breath way this request is actually
# phrased - without the colon boundary, the non-greedy group had no
# in-sentence character to stop at (no "." was ever typed) and was
# forced all the way to the end of the string, swallowing the intended
# *content* into "purpose" and leaving nothing behind to be the content
# itself. Excluding ":" from the captured class, and accepting it as an
# alternative stop alongside [.!?], is the same discipline the character
# class already applies to those three; a purpose that itself needs a
# colon was never representable here regardless.
_CAPTURE_FOR = re.compile(
    r"\b(?:record|keep)\s+(?:this|that)\s+for\s+(?:the\s+)?([^.!?:]+?)(?:[.!?:]|$)",
    re.IGNORECASE,
)
_CAPTURE_PLAIN = re.compile(
    r"\b(?:record|keep)\s+(?:this|that)\b\s*:?\s*(.*)$",
    re.IGNORECASE,
)
# A leading "Alfred, " / "Hey Arthur, " address. No agent is named here -
# this recognises the grammatical *shape* of being spoken to (an optional
# greeting, then a short capitalised fragment, then a comma), the same
# way every other address-recognition in this project works without a
# name list. Stripped before extraction so who was addressed is never
# mistaken for what was said.
_VOCATIVE_ADDRESS = re.compile(
    r"^(?:hey|hi|hello)?\s*[A-Z][\w'\-]*(?:\s+[A-Z][\w'\-]*){0,2}\s*,\s*", re.IGNORECASE
)
_TRAILING_PUNCTUATION = re.compile(r"[.!?]+$")

DEFAULT_PURPOSE = "general"
CAPTURED_KIND = "recording"
CAPTURED_SOURCE = "conversation"


def capture_intent(text: str) -> bool:
    """Whether this text explicitly asks for something to be recorded -
    the ``claims=`` predicate a capability uses to recognise its own
    request shape before word-overlap scoring ever runs, the same
    instrument ``forget_intent``/``recall_memory_intent`` already use."""
    stripped = str(text or "")
    return bool(_CAPTURE_FOR.search(stripped) or _CAPTURE_PLAIN.search(stripped))


def extract_capture(text: str) -> tuple[str, str] | None:
    """``(content, purpose)`` for an explicit capture request, or None.

    ``purpose`` defaults to :data:`DEFAULT_PURPOSE` when none is named.
    ``content`` is whatever the person said beyond the trigger and
    purpose clause; when nothing else was said (a bare "record this."),
    the full utterance is kept rather than an empty string, because that
    utterance is honestly all there is to go on - never fabricated,
    never silently dropped.
    """
    stripped = str(text or "").strip()
    if not stripped:
        return None

    # Who was being spoken to is not what was said - stripped first, so
    # "Alfred, record this for later" and "record this for later" extract
    # identically.
    addressed = _VOCATIVE_ADDRESS.sub("", stripped, count=1).strip()
    working = addressed or stripped

    for_match = _CAPTURE_FOR.search(working)
    if for_match is not None:
        purpose = " ".join(for_match.group(1).split())
        content = (working[: for_match.start()] + working[for_match.end() :]).strip(" ,.:;")
        return (content or working), purpose

    plain_match = _CAPTURE_PLAIN.search(working)
    if plain_match is not None:
        content = _TRAILING_PUNCTUATION.sub("", plain_match.group(1)).strip()
        return (content or working), DEFAULT_PURPOSE

    return None


def build_recording(
    text: str,
    *,
    agent_name: str,
    kind: str = CAPTURED_KIND,
) -> MemoryRecord | None:
    """One candidate recording from an explicit "record this" request,
    or None when the text does not ask for one at all.

    ``agent_name`` travels in ``context`` as provenance - who was asked
    to capture this - the same rule ``retention_requests`` already
    follows for "which conversation this came from". Never used to
    reach into another agent's store: the caller decides *whose* memory
    this candidate is offered to, exactly as every other candidate in
    this project already works.

    Carries full confidence (1.0) in context: a specialist's own
    ``SpecialistMemoryPolicy`` refuses a tentative observation as not
    yet knowledge (see that module's own docstring), which is the right
    rule for a *self-derived* hunch and the wrong one here - a person
    explicitly asking for something to be recorded is not a hunch, and
    treating it as one would refuse the exact request this function
    exists to honour.
    """
    extracted = extract_capture(text)
    if extracted is None:
        return None
    content, purpose = extracted
    return MemoryRecord(
        content=content,
        kind=kind,
        tags={"recording", purpose.replace(" ", "_")},
        context={
            "purpose": purpose,
            "confidence": 1.0,
            "source": CAPTURED_SOURCE,
            "captured_by": agent_name,
        },
    )
