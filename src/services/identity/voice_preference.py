"""A governed, runtime-changeable voice preference - kept apart from
``voice.py`` on purpose.

The gap this closes: IDENTITY.md's own ``preferred_voice:`` line is a
real, honoured override (see ``WindowsSpeechAdapter._select_voice_name``)
- but the only way to change it was editing that file and restarting.
This project already solved the identical shape of problem once, for a
sibling concern: ``avatar_preference_for``/``set_avatar_preference`` in
``services/identity/presence.py`` let a person change an agent's avatar
at runtime, through the agent's own governed Memory, without touching a
document. The functions below are that same pattern, applied to voice -
not a new mechanism.

**Why this is its own module and not simply added to voice.py.**
``voice.py`` is verified, structurally
(``tests/test_voice_identity.py``, ``tests/test_voice_activation.py``,
``tests/test_agent_voice_identity.py``), to import nothing from ``src``
at all - a deliberate purity guarantee for a pure value object and
resolver. A governed Memory preference genuinely needs
``MemoryRecord`` and a caller-supplied ``recall``/``remember``/``forget``,
so it lives here instead: a thin layer *above* ``voice.py``, never
inside it. Nothing in ``voice.py`` changed, and nothing there needed to.

Not state held by this module either: ``recall``, ``remember`` and
``forget`` are the caller's own governed Memory, passed in exactly as
every other optional collaborator in this project already is - the
identical claim ``presence.py`` makes about ``avatar_preference_for``,
for the same reason.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable

from src.services.identity.voice import VoiceProfile, voice_for
from src.services.memory.memory import MemoryRecord

_Recall = Callable[..., "tuple[MemoryRecord, ...]"]
_Remember = Callable[[MemoryRecord], object]
_Forget = Callable[[str], bool]

VOICE_PREFERENCE_FACT_TYPE = "voice_preference"


def _voice_tag(agent_key: str) -> str:
    return "voice_preference:" + "_".join(str(agent_key).lower().split())


def voice_preference_for(agent_key: str, *, recall: "_Recall | None") -> str | None:
    """The voice name ``agent_key``'s own governed Memory currently has
    stored as a preference, or None when nothing has ever been chosen
    (or no ``recall`` reaches this call at all) - the identical honest-
    absence shape ``avatar_preference_for`` already has.
    """
    if recall is None:
        return None
    for record in recall(kind="user_context", tags={_voice_tag(agent_key)}, limit=1):
        stored = (record.context or {}).get("voice_name")
        if stored:
            return str(stored)
    return None


def set_voice_preference(
    agent_key: str,
    voice_name: str,
    *,
    available: "tuple[str, ...] | None" = None,
    recall: "_Recall",
    remember: "_Remember",
    forget: "_Forget",
) -> bool:
    """Store ``voice_name`` as this agent's own chosen voice, replacing
    any prior choice - the same forget-then-remember composition
    ``set_avatar_preference`` already uses.

    ``available``, when given, is matched case-insensitively and the
    request is refused (returns False, writing nothing) for a name that
    is not genuinely one of them - the same "never a fabricated choice"
    rule ``set_avatar_preference`` enforces against its own catalog.
    None (the default) skips this check entirely: not every speech
    adapter can enumerate its own voices (``SpeechAdapter``'s own
    contract deliberately carries no such method), so a caller that
    cannot ask one honestly cannot validate against it either. The
    stored preference still degrades safely at *selection* time
    regardless (``_select_voice_name``'s own documented fallback), so
    skipping validation here never risks a crash - only a preference
    that turns out unusable on whatever engine eventually reads it,
    precisely the same risk an IDENTITY.md typo already carries today.
    """
    normalised = " ".join(str(voice_name or "").split())
    if not normalised:
        return False
    if available is not None:
        catalog = {name.strip().lower() for name in available}
        if normalised.lower() not in catalog:
            return False

    tag = _voice_tag(agent_key)
    for existing in recall(kind="user_context", tags={tag}, limit=5):
        forget(existing.id)

    remember(
        MemoryRecord(
            content=f"Voice preference for {agent_key} is {normalised}.",
            kind="user_context",
            tags={VOICE_PREFERENCE_FACT_TYPE, tag},
            context={
                "fact_type": VOICE_PREFERENCE_FACT_TYPE,
                "agent_key": agent_key,
                "voice_name": normalised,
                "source": "conversation",
            },
        )
    )
    return True


def effective_voice_for(agent: Any, *, recall: "_Recall | None") -> VoiceProfile | None:
    """The voice this agent should actually be heard in right now: a
    runtime-stored preference when one exists, overriding
    ``voice_for(agent)``'s own declared default - never the reverse,
    and never inventing a voice for an agent that published none at
    all.

    This is the one function a channel about to speak should call
    instead of bare ``voice_for`` - see ``VoiceChannel._voice_of``. The
    override changes only ``preferred_voice``; every other declared
    characteristic (accent, pace, pitch, warmth, style) is untouched,
    because choosing a different real voice to speak in is not the same
    fact as redescribing who the agent is.
    """
    base = voice_for(agent)
    if base is None:
        return None
    override = voice_preference_for(getattr(agent, "name", base.key), recall=recall)
    if not override:
        return base
    return replace(base, preferred_voice=override)
