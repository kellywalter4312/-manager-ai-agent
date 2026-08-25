"""Saying what happened, in Alfred's own words rather than from a phrase book.

The requirement this module answers is easy to state and easy to get wrong:
Alfred should be able to tell somebody a new capability arrived without a
developer having written that sentence in advance.

The wrong way is a dictionary - `{"capability.available": "I have learned X"}`.
It works for exactly the events somebody thought of, and the first unanticipated
event either says nothing or says something false. It also means every new
capability needs a code change in the conversational layer, which is the
coupling the registry architecture exists to remove.

**So phrasing is composed from the event's own facts.** Two paths, and the
choice between them is the Brain distinction the whole project runs on:

    HALF BRAIN   compose deterministically. Frames are grammatical scaffolding
                 with real values dropped in - "a new capability", "the tool it
                 needs" - and the frame is chosen from the event kind and
                 varied by the event itself, so repeated events do not repeat
                 word for word. No model, no network, always available.

    FULL BRAIN   ask the brain to phrase the same facts. It is handed what
                 happened, never a sentence to embellish, and if it fails or
                 says nothing usable the deterministic wording is used instead.

The important part is that the *facts* come from the architecture either way.
The brain changes how something is said, never whether it is true - it is given
values that were already established and cannot introduce a capability, a tool
or a result that did not happen.

**Variation is not randomness.** The frame is picked from a stable hash of the
event, so the same event always narrates the same way. A system that phrased an
identical situation differently on each read would be impossible to test and
faintly unsettling to use.
"""

from __future__ import annotations

from typing import Any, Sequence

from src.events.events import Significance, SystemEvent
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Grammatical scaffolding, not a vocabulary of things Alfred may say. Each
# frame takes real values from the event; none contains a claim of its own.
# Several per kind so repetition does not sound mechanical, chosen stably so
# the same event always reads the same way.
_FRAMES: dict[str, tuple[str, ...]] = {
    "capability.available": (
        "I can now take on {capability}.",
        "A new capability is available to me: {capability}.",
        "{capability} is something I can handle from now on.",
    ),
    "tool.unavailable": (
        "I have lost access to {tool}, so anything needing it will have to wait.",
        "{tool} is no longer available to me.",
        "The {tool} capability has gone offline.",
    ),
    "work.completed": (
        "{summary}",
        "That is finished: {summary}",
        "Done - {summary}",
    ),
    "delegation": (
        "I passed that to {specialist}.",
        "{specialist} is better suited to that, so it went there.",
        "That went to {specialist}.",
    ),
    "brain.mode": (
        "My reasoning is now {mode}.",
        "I am operating in {mode}.",
        "{mode} - {reason}",
    ),
    "run.failed": (
        "Something went wrong: {error}",
        "That did not work - {error}",
        "I hit a problem: {error}",
    ),
}

# Used when an event kind has no frame. Built from the event rather than
# guessing at it: an unfamiliar event is described accurately and plainly
# instead of being forced into wording that was written for something else.
_FALLBACK = "{actor}: {kind}"


def _choose(frames: Sequence[str], event: SystemEvent) -> str:
    """Pick a frame stably from the event itself.

    Hash of the facts rather than a random choice, so narrating the same event
    twice gives the same sentence. Testable, and it avoids the unsettling
    effect of a system that rephrases an unchanged situation.
    """
    seed = f"{event.kind}|{event.actor}|" + "|".join(
        f"{k}={v}" for k, v in sorted(event.facts.items())
    )
    return frames[sum(ord(c) for c in seed) % len(frames)]


def _fill(frame: str, event: SystemEvent) -> str | None:
    """Put the event's real values into a frame.

    Returns None when the event does not carry what the frame needs. A missing
    value is not filled with a plausible one - the caller falls back to plainer
    wording, which is honest, rather than to an invented detail.
    """
    values: dict[str, Any] = {"actor": event.actor, "kind": event.kind}
    values.update({k: v for k, v in event.facts.items() if v not in (None, "")})
    try:
        return frame.format(**values)
    except (KeyError, IndexError):
        return None


def compose(event: SystemEvent) -> str:
    """Phrase one event deterministically, from its own facts.

    Always available. Needs no model, makes no call, and works identically in
    Half Brain - which is the point: Alfred describing his own system must not
    be a thing that only works when a provider is reachable.
    """
    frames = _FRAMES.get(event.kind)
    if frames:
        filled = _fill(_choose(frames, event), event)
        if filled:
            return filled

        # The frame wanted something this event did not carry. Try the others
        # before giving up - a shorter frame may need less.
        for frame in frames:
            filled = _fill(frame, event)
            if filled:
                return filled

    return _fill(_FALLBACK, event) or event.describe()


def compose_with_brain(event: SystemEvent, brain: Any) -> str:
    """Let the brain phrase the same facts, when there is one that works.

    The brain is handed what happened and asked for wording. It is never handed
    a sentence to improve, and it cannot add a capability, a tool or a result -
    the facts were settled before it was asked, and the deterministic wording
    is what is used if it declines, fails, or returns something unusable.

    Falls back rather than raising, because a narrator that could break the
    system by failing to find a nicer sentence would be a poor trade.
    """
    plain = compose(event)
    if brain is None:
        return plain

    from src.manager.cognition import assess_cognition

    if not assess_cognition(brain).is_full_brain:
        # Not merely "a brain exists" - the model has to be verified usable.
        # Asking an unproven provider would either fail or stall, and the
        # deterministic wording is already correct.
        return plain

    try:
        from src.core.contracts import Task

        facts = ", ".join(f"{k}: {v}" for k, v in sorted(event.facts.items()))
        decision = brain.deliberate(
            Task(
                description=(
                    "Phrase this system event for the person you assist, in one "
                    "short sentence. State only what the facts say."
                ),
                payload={"event": event.kind, "facts": event.facts},
            ),
            (),
            (f"event: {event.kind}", f"actor: {event.actor}", f"facts: {facts}"),
        )
    except Exception as exc:  # noqa: BLE001 - narration must never break anything
        logger.debug("brain could not phrase an event: %s", type(exc).__name__)
        return plain

    spoken = (getattr(decision, "reason", "") or "").strip()
    if not spoken or len(spoken) > 300:
        return plain
    return spoken


def announcements(events: Sequence[SystemEvent], brain: Any = None) -> tuple[str, ...]:
    """What is worth saying, unprompted, from a batch of events.

    Filters before it phrases. Only IMPORTANT events reach a person without
    being asked for, and that check happens here once rather than being left to
    every caller to remember.
    """
    return tuple(
        compose_with_brain(event, brain) if brain is not None else compose(event)
        for event in events
        if event.significance is Significance.IMPORTANT
    )
