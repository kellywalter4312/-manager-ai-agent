"""Agent Presence - Stage 17's own attachment point on the canonical
agent, parallel to (and never a replacement for) VoiceProfile.

    Agent
    +-- Brain
    +-- Memory
    +-- Identity
    +-- Personality
    +-- VoiceProfile
    +-- Capabilities
    +-- Connections
    +-- AgentPresence
          +-- AvatarProfile
          +-- DigitalHumanProfile (Stage 17.4 - the fuller
                presentation profile AvatarProfile was always the
                first slice of: avatar_id plus open, renderer-owned
                style dimensions - appearance/body/posture/clothing/
                idle/listening/speaking behaviour/expression/gesture
                style. Presentation-layer configuration only; never a
                second Brain, Memory, Agent or reasoning system.)

**Never a second agent, never a second Brain, never a second Memory.**
``AgentPresence`` is read *from* the canonical agent it describes -
whatever a stored avatar preference says, whatever this project already
knows about brain mode for a presence state - never constructed
independently of one.

**Bound callables, not a whole agent object - the identical narrowing
``resolve_celebration_intensity`` already uses.** Alfred's own
registered identity (``ManagerSkills``, what the registry actually
holds under "Alfred") does not expose ``.memory`` directly - only
``ManagerAgent`` itself does - while a specialist's own ``BaseAgent``
does. Rather than have this module special-case that asymmetry (which
would mean *this* module deciding "is this the Manager or a
specialist", a router-shaped question it has no business asking),
every function below takes ``recall``/``remember``/``forget`` as bound
callables, exactly the shape ``CapabilityRequest`` already hands a
capability handler. The caller - which already knows which real
``Memory`` object it means - supplies them; this module never guesses.

**States are derived, never asserted** - the exact rule
``ARCHITECTURE.md``'s own Digital Human section already states for this
project's presentation layer generally. A snapshot with nothing
happening reports IDLE, honestly, rather than guessing.

**Extensible on purpose.** ``PresenceState`` ships three members for
this phase (IDLE, LISTENING, SPEAKING); THINKING, EMOTIONAL, GESTURING,
INTERRUPTED, ERROR and OFFLINE are anticipated, not built - adding one
later is a one-line enum change, never a rewrite of anything that reads
this module.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterator

from src.providers.avatar import AvatarProfile, AvatarProvider
from src.providers.environment import EnvironmentProfile, EnvironmentProvider
from src.services.memory.memory import MemoryRecord

AVATAR_PREFERENCE_FACT_TYPE = "avatar_preference"
# Whole-System Reality Audit: the identical governed-preference pattern
# avatar already has, extended to the environment axis - found missing
# during that audit (EnvironmentProvider/environment_provider existed
# as a real, registered Ecosystem category with no per-agent selection
# or persistence at all, the exact orphan-feature shape avatar itself
# had before Stage 17's own preference work).
ENVIRONMENT_PREFERENCE_FACT_TYPE = "environment_preference"

_Recall = Callable[..., tuple[MemoryRecord, ...]]
_Remember = Callable[[MemoryRecord], object]
_Forget = Callable[[str], bool]


class PresenceState(str, Enum):
    """What a presence is observably doing right now.

    Three members for this phase - IDLE, LISTENING, SPEAKING - covering
    exactly what this project can currently tell honestly apart: nothing
    in flight, a request just addressed to this agent, and the turn this
    agent itself just answered. THINKING/EMOTIONAL/GESTURING/INTERRUPTED/
    ERROR/OFFLINE are deliberately not declared yet - inventing them
    before anything could honestly derive them would be exactly the
    "asserted by the presentation layer" mistake this module refuses.
    """

    IDLE = "idle"
    LISTENING = "listening"
    SPEAKING = "speaking"


class PresenceRegistry:
    """Stage 17.2 - the one place a *live* presence signal is allowed to
    exist, so ``presence_for()`` below can keep its own rule intact:
    state is only ever what a caller asserts, never computed here.

    This is not a second Memory and not a second Brain. It remembers
    nothing about content, reasoning or history - only, for as long as
    a real thing is actually happening, which of this project's own
    three ``PresenceState`` members applies to one agent right now. The
    same class of fact ``Availability`` already is: evidence, held only
    as long as it stays true, never a record of what happened.

    Genuinely in-process and genuinely momentary. There is no
    persistence, no expiry timer and no second store: a caller that
    asserts a state is also the caller responsible for clearing it
    (see ``speaking()`` below, which does so even on an exception), and
    a state nobody is currently asserting simply is not here, which is
    exactly what lets ``presence_for()`` fall back to its own default.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, PresenceState] = {}
        # Stage 17.9: the identical live-signal discipline as _state
        # above, extended to carry an optional PresentationIntent
        # alongside a state - never a second registry, never a second
        # locking scheme. A caller that asserts an intent is the same
        # caller responsible for clearing it; an agent nobody has
        # asserted one for simply is not here, which is what lets
        # presence_for() fall back to None (neutral, by absence).
        self._intent: dict[str, PresentationIntent] = {}

    def set(self, agent_key: str, state: PresenceState) -> None:
        with self._lock:
            self._state[str(agent_key)] = state

    def clear(self, agent_key: str) -> None:
        with self._lock:
            self._state.pop(str(agent_key), None)

    def get(self, agent_key: str) -> "PresenceState | None":
        with self._lock:
            return self._state.get(str(agent_key))

    def set_intent(self, agent_key: str, intent: "PresentationIntent") -> None:
        with self._lock:
            self._intent[str(agent_key)] = intent

    def clear_intent(self, agent_key: str) -> None:
        with self._lock:
            self._intent.pop(str(agent_key), None)

    def get_intent(self, agent_key: str) -> "PresentationIntent | None":
        with self._lock:
            return self._intent.get(str(agent_key))

    def __repr__(self) -> str:
        with self._lock:
            return f"<PresenceRegistry state={dict(self._state)!r} intent={dict(self._intent)!r}>"


@contextmanager
def speaking(registry: "PresenceRegistry | None", agent_key: str) -> Iterator[None]:
    """Mark ``agent_key`` SPEAKING for exactly as long as the wrapped
    call actually runs - the real synchronisation point Stage 17.2
    needed between the voice playback lifecycle and Agent Presence.

    Used around the one real, already-existing, already-blocking call
    in this codebase that produces actual audio
    (``SpeechAdapter.speak()``, reached through
    ``VoiceChannel.deliver()`` - see that module's own docstring: "this
    is the only place speech happens"). A synchronous engine
    (``WindowsSpeechAdapter``, ``ElevenLabsSpeechAdapter``) blocks
    inside the ``with`` body for the real duration of playback, so
    SPEAKING is true for exactly that long - never a fabricated timer
    guessed from text length. ``NoSpeech`` returns instantly, so the
    window is real but momentary, which is the honest answer for an
    installation with no engine.

    Clears on every exit, including an exception - a caller must never
    be left permanently SPEAKING because delivery raised. ``registry``
    may be None (no live signal wired), in which case this is a no-op:
    every existing caller that predates Stage 17.2 keeps working
    unchanged.
    """
    if registry is None or not agent_key:
        yield
        return
    registry.set(agent_key, PresenceState.SPEAKING)
    try:
        yield
    finally:
        registry.clear(agent_key)


# Stage 17.4 - the open, renderer-owned dimensions a future digital-human
# renderer may support, beyond the avatar identity itself. Named once so
# validation (set_digital_human_style below), tests, and any future
# reader all agree on the closed set of *field names* - never on a closed
# set of *values*, because no renderer exists yet to say what a valid
# "posture" or "clothing" value even is. Ten dimensions - not a
# speculative extra added "for completeness"; a new one is a one-line
# addition here, the same extensibility PresenceState's own docstring
# already promises.
#
# Stage 17.13 adds the tenth, ``symbolic_environment`` - deliberately
# still just one more open string in the same list, the same "open
# field, curated renderer menu on top" contract Stage 17.11/17.12 already
# established for body/posture/idle_behavior/gesture_style. It names the
# agent's own 3D symbolic backdrop (see AVATAR_SYMBOLIC_ENVIRONMENTS in
# ui/index.html) - a concept genuinely distinct from the human body mesh
# itself (``body``/``posture``/``appearance``), never merged into it.
DIGITAL_HUMAN_STYLE_FIELDS = (
    "appearance",
    "body",
    "posture",
    "clothing",
    "idle_behavior",
    "listening_behavior",
    "speaking_behavior",
    "expression_style",
    "gesture_style",
    "symbolic_environment",
    # Stage 17.15: hair, kept a separate dimension from ``appearance``
    # (which now only names "glasses" or not) rather than folded into
    # it - two independent choices, never a combined vocabulary that
    # would force one field to say both at once.
    "hair_style",
)

DIGITAL_HUMAN_PROFILE_FACT_TYPE = "digital_human_profile"


@dataclass(frozen=True)
class DigitalHumanProfile:
    """The user's own configuration of how one agent presents itself -
    the presentation-layer profile Stage 17.1's bare avatar choice was
    always the first slice of. Stage 17.4 gives it a real, named,
    persisted shape without pretending a renderer exists to act on most
    of it yet.

        AgentPresence
              +-- DigitalHumanProfile
                    +-- avatar_id           (real: AvatarProvider's own catalog)
                    +-- appearance )
                    +-- body       )
                    +-- posture    )  open, renderer-owned labels -
                    +-- clothing   )  never a closed enum this project
                    +-- idle_behavior       )  invents on a renderer's
                    +-- listening_behavior  )  behalf. Empty string means
                    +-- speaking_behavior   )  "not configured" - the
                    +-- expression_style    )  renderer's own default,
                    +-- gesture_style       )  never a fabricated choice.
                    +-- symbolic_environment)  (Stage 17.13 - the agent's
                                              own 3D backdrop, a concept
                                              distinct from the body mesh)
                    +-- hair_style          )  (Stage 17.15 - kept
                                              separate from appearance,
                                              which only ever names
                                              "glasses" or not)

    ``avatar_id`` is the one field with a real catalog behind it
    (``AvatarProvider.catalog()``) and the one field this type never
    invents its own storage for - see ``digital_human_profile_for()``,
    which always resolves it through the *existing* Stage 17.1 avatar
    preference, never a second value. Every other field is exactly the
    same "characteristics, never a rendering asset" contract
    ``AvatarProfile.style``/``PresentationIntent.gesture`` already use,
    because this stage's own brief is explicit: do not invent a fixed
    vocabulary of postures or clothing options when no renderer exists
    to define what they mean - the renderer decides how each label is
    represented, or ignores it.

    Never a second Brain, Memory, Agent or reasoning system - every
    field here is a string a renderer may or may not act on, and
    nothing here can reach a Brain, a Memory, a tool or a capability.
    """

    avatar_id: str
    appearance: str = ""
    body: str = ""
    posture: str = ""
    clothing: str = ""
    idle_behavior: str = ""
    listening_behavior: str = ""
    speaking_behavior: str = ""
    expression_style: str = ""
    gesture_style: str = ""
    symbolic_environment: str = ""
    hair_style: str = ""

    def __post_init__(self) -> None:
        if not str(self.avatar_id).strip():
            raise ValueError("a DigitalHumanProfile must name an avatar_id")
        for name in ("avatar_id",) + DIGITAL_HUMAN_STYLE_FIELDS:
            object.__setattr__(self, name, " ".join(str(getattr(self, name) or "").split()))
        object.__setattr__(self, "avatar_id", self.avatar_id.lower())

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload, mirroring
        ``AvatarProfile.to_dict()``'s own reasoning - the boundary
        format a snapshot or a future renderer reads."""
        return {"avatar_id": self.avatar_id, **{f: getattr(self, f) for f in DIGITAL_HUMAN_STYLE_FIELDS}}


@dataclass(frozen=True)
class AgentPresence:
    """One agent's current presentation state - an avatar and what it is
    observably doing - never a second identity for the agent it
    describes.

    ``agent_key`` names whose presence this is, the same provenance
    every other observation in this project already carries; there is
    no field here in which a Brain, a Memory, a tool or a capability
    could be reached - a presence describes an agent, it does not hold
    one.

    ``profile`` (Stage 17.4) is the fuller ``DigitalHumanProfile``
    ``avatar`` is itself always a part of - optional and defaulting to
    None so every construction that predates Stage 17.4 (direct
    ``AgentPresence(...)`` calls throughout this project's own tests)
    is completely unaffected. ``presence_for()`` below always attaches
    one for its own callers.

    ``intent`` (Stage 17.9) is this agent's own live
    ``PresentationIntent``, if any real producer has genuinely
    asserted one right now (see ``VoiceChannel.deliver()`` and
    ``presentation_intent_for_turn()`` above) - optional, defaulting to
    None, for the identical "every construction that predates this
    stage is unaffected" reason ``profile`` already has.
    """

    agent_key: str
    avatar: AvatarProfile
    state: PresenceState = PresenceState.IDLE
    profile: "DigitalHumanProfile | None" = None
    intent: "PresentationIntent | None" = None

    def __post_init__(self) -> None:
        if not str(self.agent_key).strip():
            raise ValueError("an AgentPresence must name the agent it describes")
        object.__setattr__(self, "agent_key", " ".join(str(self.agent_key).split()))

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload - the boundary format a snapshot
        or a future renderer reads, mirroring
        ``AvatarProfile.to_dict()``'s own reasoning.

        ``profile``/``intent`` are included only when present, so this
        stays the exact shape every pre-Stage-17.4/17.9 reader already
        expects when nothing supplied one - never a new required key
        nothing wrote.
        """
        d: dict[str, object] = {
            "agent_key": self.agent_key,
            "avatar": self.avatar.to_dict(),
            "state": self.state.value,
        }
        if self.profile is not None:
            d["profile"] = self.profile.to_dict()
        if self.intent is not None:
            d["intent"] = {"gesture": self.intent.gesture, "intensity": self.intent.intensity}
        return d


@dataclass(frozen=True)
class PresentationIntent:
    """Stage 17.2's gesture seam - the smallest abstraction that lets a
    renderer receive "a small hand gesture, a nod, an acknowledgement"
    without this project's own agent architecture changing to add it.

    ``gesture`` is an open, renderer-owned vocabulary - a short label
    such as "acknowledge" or "emphasis" - never an animation primitive
    (a joint name, a keyframe, a duration): exactly the same
    "characteristics, never a rendering asset" boundary
    ``AvatarProfile`` already draws for appearance, drawn here for
    behaviour instead. Nothing here enforces a closed set of values -
    see ``PRESENTATION_INTENT_VOCABULARY`` below for the small,
    explicit list this project's own producer/renderer actually agree
    on today; a future producer naming a value outside it is still a
    valid ``PresentationIntent``, exactly as an unrecognised
    ``avatar_id`` is still a valid string, honoured or ignored entirely
    at the renderer's own discretion.

    **Stage 17.9 wired the first real producer; Stage 17.10 widened
    it.** ``VoiceChannel.deliver()`` is the one real producer (see its
    own docstring), calling ``presentation_intent_for_turn()`` below,
    which classifies a small, fixed set of already-governed structured
    facts a turn carries (celebration, success, memory recall, tool
    use, delegation) - never by inspecting what any text means, and
    never a second call to an LLM. A turn matching none of them still
    gets None, the same honest "neutral, by absence" default
    ``presence_for()`` already uses for every other optional signal in
    this module.
    """

    gesture: str
    intensity: float = 1.0

    def __post_init__(self) -> None:
        if not str(self.gesture).strip():
            raise ValueError("a PresentationIntent must name a gesture")
        object.__setattr__(self, "gesture", " ".join(str(self.gesture).split()).lower())


# Stage 17.9: the small, explicit vocabulary this phase's own producer
# and renderer actually agree on - never enforced by PresentationIntent
# itself (see its own docstring on why), so a future producer or
# renderer can extend it without a type change here. Kept in one place
# so "what does this project currently mean by 'acknowledging'" has one
# answer, not one guessed independently by every future caller.
PRESENTATION_INTENT_VOCABULARY = (
    "neutral",
    "explaining",
    "emphasizing",
    "acknowledging",
    "thinking",
    "listening",
    "concluding",
)


def presentation_intent_for_turn(turn: object) -> "PresentationIntent | None":
    """The one real presentation-intent producer - a deterministic
    *structural* classification of facts ``observe()`` (see
    ``src/control_center/trail.py``) already computed about this exact
    turn, never a semantic reading of its request/outcome text and
    never a second call to any LLM. Stage 17.10 widens this from Stage
    17.2/17.9's single celebration check to the small set of other
    already-real, already-governed facts a turn carries - still
    exactly one honest classifier, still neutral by absence whenever
    none of them apply.

    Checked in this fixed priority order, each a real field the
    orchestration layer already produced before this turn ever reached
    a channel:

    1. ``celebration`` set             -> "acknowledging" (Stage 12's own
                                           governed milestone signal;
                                           unchanged since Stage 17.9)
    2. ``succeeded`` is not exactly
       ``True``                         -> None (a failure - or a
                                           foreign/duck-typed object
                                           that never declared success
                                           at all - has no honest
                                           positive presentation to
                                           assign; missing evidence
                                           stays neutral rather than
                                           being assumed successful)
    3. ``memory_recalled`` > 0          -> "thinking" (this agent
                                           genuinely consulted Memory
                                           before answering)
    4. ``tool_used`` is not None        -> "emphasizing" (a concrete
                                           capability genuinely ran)
    5. ``delegated`` is true            -> "explaining" (the Manager
                                           genuinely handed this to a
                                           specialist - a real,
                                           meaningfully-varying fact,
                                           unlike ``len(steps)``, which
                                           ``observe()`` always
                                           populates with several
                                           entries regardless and so
                                           cannot honestly discriminate
                                           anything)
    6. otherwise                        -> "concluding" (a plain,
                                           self-contained, successful
                                           answer - genuinely the "final
                                           word" case, nothing else
                                           notable happened)

    "listening" has no entry here by design: it names the renderer's
    existing LISTENING-activity behaviour (``PresenceState.LISTENING``,
    Stage 17.2/17.3's own already-real signal), not something a
    *speaking*-window PresentationIntent ever produces.

    Duck-typed (``getattr``, never an import of ``ConversationTurn``):
    this module has no reason to depend on the conversation/control-
    center layer, and every other optional collaborator in this file
    already reads what it needs the same way.
    """
    if getattr(turn, "celebration", None) is not None:
        return PresentationIntent(gesture="acknowledging")
    if getattr(turn, "succeeded", None) is not True:
        return None
    if (getattr(turn, "memory_recalled", 0) or 0) > 0:
        return PresentationIntent(gesture="thinking")
    if getattr(turn, "tool_used", None) is not None:
        return PresentationIntent(gesture="emphasizing")
    if getattr(turn, "delegated", False):
        return PresentationIntent(gesture="explaining")
    return PresentationIntent(gesture="concluding")


def _avatar_tag(agent_key: str) -> str:
    return "avatar_preference:" + "_".join(str(agent_key).lower().split())


def avatar_preference_for(agent_key: str, *, recall: "_Recall | None") -> str | None:
    """The avatar id ``agent_key``'s own governed Memory currently has
    stored as a preference, or None when nothing has ever been chosen
    (or no ``recall`` reaches this call at all - the same honest-absence
    shape every other optional collaborator in this project already
    has)."""
    if recall is None:
        return None
    for record in recall(kind="user_context", tags={_avatar_tag(agent_key)}, limit=1):
        stored = (record.context or {}).get("avatar_id")
        if stored:
            return str(stored)
    return None


def set_avatar_preference(
    agent_key: str,
    avatar_id: str,
    *,
    provider: AvatarProvider,
    recall: "_Recall",
    remember: "_Remember",
    forget: "_Forget",
) -> bool:
    """Store ``avatar_id`` as this agent's own chosen avatar, replacing
    any prior choice - the same forget-then-remember composition Stage
    12's personal-recurring-events confirmation already uses for "the
    governed memory update path".

    Returns False, writing nothing, when ``avatar_id`` does not name a
    real entry in ``provider.catalog()`` - never a fabricated choice.
    """
    catalog_ids = {profile.avatar_id for profile in provider.catalog()}
    normalised = " ".join(str(avatar_id or "").split()).lower()
    if normalised not in catalog_ids:
        return False

    tag = _avatar_tag(agent_key)
    for existing in recall(kind="user_context", tags={tag}, limit=5):
        forget(existing.id)

    remember(
        MemoryRecord(
            content=f"Avatar preference for {agent_key} is {normalised}.",
            kind="user_context",
            tags={AVATAR_PREFERENCE_FACT_TYPE, tag},
            context={
                "fact_type": AVATAR_PREFERENCE_FACT_TYPE,
                "agent_key": agent_key,
                "avatar_id": normalised,
                "source": "conversation",
            },
        )
    )
    return True


def _environment_tag(agent_key: str) -> str:
    return "environment_preference:" + "_".join(str(agent_key).lower().split())


def environment_preference_for(agent_key: str, *, recall: "_Recall | None") -> str | None:
    """The environment id ``agent_key``'s own governed Memory currently
    has stored as a preference, or None when nothing has ever been
    chosen - the identical honest-absence shape
    ``avatar_preference_for()`` already has."""
    if recall is None:
        return None
    for record in recall(kind="user_context", tags={_environment_tag(agent_key)}, limit=1):
        stored = (record.context or {}).get("environment_id")
        if stored:
            return str(stored)
    return None


def set_environment_preference(
    agent_key: str,
    environment_id: str,
    *,
    provider: EnvironmentProvider,
    recall: "_Recall",
    remember: "_Remember",
    forget: "_Forget",
) -> bool:
    """Store ``environment_id`` as this agent's own chosen environment,
    replacing any prior choice - the identical forget-then-remember
    composition ``set_avatar_preference`` already uses.

    Returns False, writing nothing, when ``environment_id`` does not
    name a real entry in ``provider.catalog()`` - never a fabricated
    choice.
    """
    catalog_ids = {profile.environment_id for profile in provider.catalog()}
    normalised = " ".join(str(environment_id or "").split()).lower()
    if normalised not in catalog_ids:
        return False

    tag = _environment_tag(agent_key)
    for existing in recall(kind="user_context", tags={tag}, limit=5):
        forget(existing.id)

    remember(
        MemoryRecord(
            content=f"Environment preference for {agent_key} is {normalised}.",
            kind="user_context",
            tags={ENVIRONMENT_PREFERENCE_FACT_TYPE, tag},
            context={
                "fact_type": ENVIRONMENT_PREFERENCE_FACT_TYPE,
                "agent_key": agent_key,
                "environment_id": normalised,
                "source": "conversation",
            },
        )
    )
    return True


def environment_for(
    agent_key: str, *, provider: EnvironmentProvider, recall: "_Recall | None" = None
) -> EnvironmentProfile:
    """This agent's current environment - its chosen one, or the
    provider's own deterministic default when nothing has been chosen -
    the identical resolution ``presence_for()`` already performs for an
    avatar, extracted so a caller (a status screen, a future renderer)
    can ask for just this without needing a full ``AgentPresence``.
    """
    chosen = environment_preference_for(agent_key, recall=recall)
    environment = next(
        (p for p in provider.catalog() if p.environment_id == chosen), None
    ) if chosen else None
    return environment if environment is not None else provider.default_for(agent_key)


def _profile_tag(agent_key: str) -> str:
    return "digital_human_profile:" + "_".join(str(agent_key).lower().split())


def digital_human_style_for(agent_key: str, *, recall: "_Recall | None") -> dict[str, str]:
    """The stored style dimensions (every ``DIGITAL_HUMAN_STYLE_FIELDS``
    field but ``avatar_id``, which lives in the existing, separate
    avatar-preference record - see ``digital_human_profile_for()``),
    or all-empty when nothing was ever configured. The identical
    honest-absence shape ``avatar_preference_for()`` already has.
    """
    empty = {f: "" for f in DIGITAL_HUMAN_STYLE_FIELDS}
    if recall is None:
        return empty
    for record in recall(kind="user_context", tags={_profile_tag(agent_key)}, limit=1):
        context = record.context or {}
        return {f: str(context.get(f) or "") for f in DIGITAL_HUMAN_STYLE_FIELDS}
    return empty


def set_digital_human_style(
    agent_key: str,
    fields: "dict[str, str]",
    *,
    recall: "_Recall",
    remember: "_Remember",
    forget: "_Forget",
) -> bool:
    """Merge ``fields`` into this agent's own stored style, replacing
    any prior value for just the dimensions given - never the whole
    profile, so choosing a posture today does not erase a clothing
    choice made yesterday. The same forget-then-remember composition
    ``set_avatar_preference`` already uses, applied to one record per
    agent covering every style dimension together - not one record per
    field, which would be nine parallel little databases instead of
    one governed preference.

    Returns False, writing nothing, when ``fields`` names anything
    outside ``DIGITAL_HUMAN_STYLE_FIELDS`` (never a fabricated
    eleventh dimension) or when a given value is empty after
    whitespace normalisation (there is nothing to store, and an empty
    string already means "not configured" - writing one would be
    indistinguishable from never having asked).
    """
    unknown = set(fields) - set(DIGITAL_HUMAN_STYLE_FIELDS)
    if unknown:
        return False

    normalised: dict[str, str] = {}
    for name, value in fields.items():
        cleaned = " ".join(str(value or "").split())
        if not cleaned:
            return False
        normalised[name] = cleaned

    if not normalised:
        return False

    merged = {**digital_human_style_for(agent_key, recall=recall), **normalised}

    tag = _profile_tag(agent_key)
    for existing in recall(kind="user_context", tags={tag}, limit=5):
        forget(existing.id)

    remember(
        MemoryRecord(
            content=f"Digital-human presentation style for {agent_key}: {merged}.",
            kind="user_context",
            tags={DIGITAL_HUMAN_PROFILE_FACT_TYPE, tag},
            context={
                "fact_type": DIGITAL_HUMAN_PROFILE_FACT_TYPE,
                "agent_key": agent_key,
                **merged,
            },
        )
    )
    return True


def digital_human_profile_for(
    agent_key: str, avatar: AvatarProfile, *, recall: "_Recall | None" = None
) -> DigitalHumanProfile:
    """The fuller presentation profile for one agent, composed from two
    governed sources: the *existing* Stage 17.1 avatar preference
    (``avatar`` - already resolved by the caller, e.g. ``presence_for``
    below, so this never re-derives it and there is never a second
    "which avatar" answer) and the Stage 17.4 style preference above.

    Never a second database: both sources are ordinary, governed
    ``Memory`` records reached through the identical ``recall`` this
    module's other functions already use.
    """
    style = digital_human_style_for(agent_key, recall=recall)
    return DigitalHumanProfile(avatar_id=avatar.avatar_id, **style)


def presence_for(
    agent_key: str,
    *,
    provider: AvatarProvider,
    recall: "_Recall | None" = None,
    state: PresenceState = PresenceState.IDLE,
    live: "PresenceRegistry | None" = None,
) -> AgentPresence:
    """This agent's current presence - its chosen avatar, or the
    provider's own deterministic default when nothing has been chosen -
    and whatever state the caller has genuine evidence for.

    ``state`` is accepted, never computed here: only a caller that
    actually knows something happened (a turn this agent just answered,
    an address this agent just received) has evidence for anything
    other than IDLE, and this function has no route to a Conversation
    or a ConversationTurn to find out for itself - the same "narrowed
    to exactly what it needs" discipline every other collaborator in
    this project already lives under.

    ``live`` (Stage 17.2) is the one exception to "no route to find out
    for itself", and it is not actually an exception: a
    ``PresenceRegistry`` is not a Conversation and does not derive
    anything - it is a caller (``VoiceChannel.deliver()``, a real
    blocking ``speak()`` call in progress) that already asserted a
    state, held in one shared place so *this* caller does not have to
    be the same one. When ``live.get(agent_key)`` returns something, it
    wins over ``state`` - a real, currently-true signal outranks an
    explicit-but-static default. ``live=None`` (every caller that
    predates Stage 17.2) leaves this function's behaviour byte-for-byte
    unchanged.
    """
    live_state = live.get(agent_key) if live is not None else None
    if live_state is not None:
        state = live_state
    # Stage 17.9: the identical "a real, currently-true signal, held
    # in one shared place" reasoning as live_state above, extended to
    # PresentationIntent - None (neutral, by absence) unless the one
    # real producer (VoiceChannel.deliver()) currently has something
    # genuinely asserted for this exact agent.
    live_intent = live.get_intent(agent_key) if live is not None else None
    chosen = avatar_preference_for(agent_key, recall=recall)
    avatar = next(
        (p for p in provider.catalog() if p.avatar_id == chosen), None
    ) if chosen else None
    if avatar is None:
        avatar = provider.default_for(agent_key)
    profile = digital_human_profile_for(agent_key, avatar, recall=recall)
    return AgentPresence(
        agent_key=agent_key, avatar=avatar, state=state, profile=profile, intent=live_intent
    )
