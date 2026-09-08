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

from dataclasses import dataclass, replace
from typing import Any, Callable

from src.services.identity.voice import VoiceProfile, voice_for
from src.services.identity.voice_options import VoiceOption
from src.services.memory.memory import MemoryRecord

_Recall = Callable[..., "tuple[MemoryRecord, ...]"]
_Remember = Callable[[MemoryRecord], object]
_Forget = Callable[[str], bool]

VOICE_PREFERENCE_FACT_TYPE = "voice_preference"

# A genuinely different fact from VOICE_PREFERENCE_FACT_TYPE above, and
# kept apart on purpose - see AutonomousVoiceChoice's own docstring.
VOICE_AUTONOMOUS_CHOICE_FACT_TYPE = "voice_autonomous_choice"


def _voice_tag(agent_key: str) -> str:
    return "voice_preference:" + "_".join(str(agent_key).lower().split())


def _autonomous_tag(agent_key: str) -> str:
    return "voice_autonomous_choice:" + "_".join(str(agent_key).lower().split())


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


@dataclass(frozen=True)
class AutonomousVoiceChoice:
    """What an agent's own governed decision path (``select_voice``, see
    ``src/services/identity/voice_selection.py``) chose for itself,
    genuinely distinct from ``VOICE_PREFERENCE_FACT_TYPE`` above - that
    tag exists only for a person's own explicit, typed choice
    (``voice <agent> <name>`` in Control Center). This one records the
    agent's own autonomous choice among real, discovered options, and
    exists specifically so the two can be told apart: an explicit
    override always outranks an autonomous choice (see
    ``effective_voice_for`` below), and only an explicit override may
    ever silence future autonomous re-selection.

    ``provider`` is carried so ``effective_voice_for`` applies the
    choice to the correct provider-specific field - a plain name is
    only ever enough for the Windows engine (matched against its own
    installed roster); an ElevenLabs choice needs its real
    ``voice_id`` written into ``style``, exactly where that provider
    already looks for one (see ``elevenlabs_speech_provider.py``'s own
    ``_voice_id_for``)."""

    voice_id: str
    name: str
    provider: str
    language: "str | None" = None
    accent: "str | None" = None
    gender: "str | None" = None


def build_voice_autonomous_choice_record(
    agent_key: str, option: "VoiceOption", *,
    language_satisfied: bool, accent_satisfied: bool, gender_satisfied: bool = False,
) -> MemoryRecord:
    """The MemoryRecord an agent's own governed write path
    (``request.retain`` inside the ``select_voice`` capability) records
    for a genuine autonomous choice - constructed here, never written
    here: this module holds no Memory reference of its own, the same
    boundary every other collaborator in this project's capability
    handlers already crosses through ``CapabilityRequest`` alone."""
    return MemoryRecord(
        content=f"{agent_key} autonomously chose the voice '{option.name}' ({option.provider}).",
        kind="user_context",
        tags={VOICE_AUTONOMOUS_CHOICE_FACT_TYPE, _autonomous_tag(agent_key)},
        context={
            "fact_type": VOICE_AUTONOMOUS_CHOICE_FACT_TYPE,
            "agent_key": agent_key,
            "voice_id": option.voice_id,
            "voice_name": option.name,
            "provider": option.provider,
            "language": option.language,
            "accent": option.accent,
            "gender": option.gender,
            "language_satisfied": language_satisfied,
            "accent_satisfied": accent_satisfied,
            "gender_satisfied": gender_satisfied,
            "source": "autonomous_selection",
        },
    )


def forget_prior_autonomous_choice(agent_key: str, *, recall: "_Recall", forget: "_Forget") -> None:
    """Remove any previously-recorded autonomous choice for this agent -
    the same forget-then-remember composition ``set_voice_preference``
    already uses for explicit overrides, exposed here so a capability
    handler (which writes through the governed ``request.retain`` path,
    not a raw ``remember``) can still keep this fact singular rather than
    accumulating one record per re-selection."""
    for existing in recall(kind="user_context", tags={_autonomous_tag(agent_key)}, limit=5):
        forget(existing.id)


def autonomous_voice_choice_for(agent_key: str, *, recall: "_Recall | None") -> "AutonomousVoiceChoice | None":
    """This agent's own last autonomous choice, if any - the identical
    honest-absence shape ``voice_preference_for`` already has. Reads
    back exactly what ``build_voice_autonomous_choice_record`` wrote,
    never re-derives it."""
    if recall is None:
        return None
    for record in recall(kind="user_context", tags={_autonomous_tag(agent_key)}, limit=1):
        ctx = record.context or {}
        voice_id, name, provider = ctx.get("voice_id"), ctx.get("voice_name"), ctx.get("provider")
        if not voice_id or not name or not provider:
            continue
        return AutonomousVoiceChoice(
            voice_id=str(voice_id), name=str(name), provider=str(provider),
            language=ctx.get("language"), accent=ctx.get("accent"), gender=ctx.get("gender"),
        )
    return None


# Provider axes whose speak() reads a real id out of VoiceProfile.style
# rather than matching preferred_voice by name - see each provider's own
# _voice_id_for. Named here, once, so both the autonomous-choice branch
# (which always knows a real provider) and the explicit-override branch
# (which resolves one by name, when it can) apply a choice to the
# correct field the identical way, never two implementations of this
# mapping drifting apart.
_STYLE_KEYED_PROVIDERS: "dict[str, str]" = {
    "elevenlabs": "elevenlabs_voice_id",
    "kokoro": "kokoro_voice_id",
}


def _apply_provider_choice(base: VoiceProfile, *, provider: str, voice_id: str, name: str) -> VoiceProfile:
    """Write a real, provider-attributed choice onto ``base`` in
    whichever field that provider's own adapter actually reads -
    ``style[...]`` for an id-keyed provider, ``preferred_voice``
    (matched by name at speak time) for everything else, Windows
    included. The one place this mapping is expressed - both callers
    below go through it."""
    style_key = _STYLE_KEYED_PROVIDERS.get(provider)
    if style_key is not None:
        return replace(base, style={**base.style, style_key: voice_id})
    return replace(base, preferred_voice=name)


def effective_voice_for(
    agent: Any, *, recall: "_Recall | None", discover_voices: "Callable[[], Any] | None" = None
) -> VoiceProfile | None:
    """The voice this agent should actually be heard in right now.

    Governance order (requirement 4's own precedence, checked in this
    order and stopping at the first that applies):

        1. an explicit user override (``voice_preference_for``) - always
           wins, and is never silently replaced by an autonomous choice
        2. the agent's own last autonomous choice (``autonomous_voice_choice_for``)
        3. the bare declared default (``voice_for(agent)``), unchanged

    Never inventing a voice for an agent that published none at all -
    ``base is None`` short-circuits before either preference is even
    read.

    ``discover_voices``, when given, lets an explicit override be
    resolved against real, currently-discovered options the identical
    way an autonomous choice already is - a person who typed a real
    ElevenLabs/Kokoro voice's own name (``voice Alfred Bella``) gets it
    applied to the field that provider's adapter actually reads, not
    silently written to ``preferred_voice`` where an id-keyed provider
    would never look. ``None`` (no discovery reaches this call) degrades
    to the original, unchanged behaviour: the typed string goes straight
    to ``preferred_voice``, which is exactly correct for a name-matched
    engine like Windows and an honest best-effort for anything else.
    """
    base = voice_for(agent)
    if base is None:
        return None

    agent_key = getattr(agent, "name", base.key)

    override = voice_preference_for(agent_key, recall=recall)
    if override:
        if discover_voices is not None:
            for option in discover_voices():
                if option.name.lower() == override.lower() or option.voice_id.lower() == override.lower():
                    return _apply_provider_choice(base, provider=option.provider, voice_id=option.voice_id, name=option.name)
        return replace(base, preferred_voice=override)

    choice = autonomous_voice_choice_for(agent_key, recall=recall)
    if choice is not None:
        return _apply_provider_choice(base, provider=choice.provider, voice_id=choice.voice_id, name=choice.name)

    return base
