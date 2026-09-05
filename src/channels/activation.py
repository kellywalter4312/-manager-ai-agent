"""Ambient voice activation, and the continuous conversation it starts.

The problem this module exists to solve: without it, every single voice
turn would need its own wake phrase, because nothing remembers that a
conversation is already under way. A person would have to name the agent
they are talking to before every single sentence they speak to it - which
is not how talking to somebody works.

    an agent already exists and is already running
                    |
                    v
    VoiceActivation.matches() - a heard utterance names this agent
                    |
                    v
    ACTIVATED - this agent is now the active conversational target
                    |
                    v
    every turn from here on goes through the exact existing Channel this
    session was built with - the same ask(), the same deliver(), the same
    Manager, the same Brain, the same Memory - until the conversation ends
                    |
                    v
    ENDED -> IDLE, ready for the next activation phrase

**Activation selects; it never constructs.** This module holds no brain, no
memory, no registry and no provider. It is handed an already-built
``Channel`` - which already holds an already-built agent - and an already-
declared ``VoiceActivation``, and its entire job is deciding *when* the
existing ``Channel.ask``/``Channel.deliver`` pair gets called. There is no
path from here to constructing a second agent, a second brain or a second
memory, because there is nothing in this module that constructs anything at
all.

**Agent-agnostic, on purpose.** Nothing here branches on which agent it was
built for, checks a name, or imports anything identity- or provider-
specific. A session built for one agent today works unmodified for any
future agent that declares its own ``VoiceActivation`` - see
``src/services/identity/voice.py``.

**One state machine, matching the lifecycle a real conversation has:**

    IDLE        listening for the activation phrase; nothing else happens
    ACTIVATED   the phrase just matched; the conversation has not yet had
                its first turn
    LISTENING   between turns, ready for the next thing the person says
    THINKING    a turn is in flight - the request has been asked, no
                answer yet
    SPEAKING    an answer has come back and is being delivered
    ENDED       the conversation just concluded; the session settles back
                to IDLE immediately after, ready for the next activation

Activation happens once per conversation; a conversation may contain many
turns. Nothing about reaching LISTENING a second, third or fourth time asks
for the activation phrase again - only returning to IDLE does.
"""

from __future__ import annotations

from enum import Enum
from typing import Callable

from src.channels.channel import Channel, Response
from src.channels.speech import SpeechUnavailable
from src.services.identity.presence import PresenceRegistry, PresenceState
from src.services.identity.voice import VoiceActivation
from src.utils.logging import get_logger

logger = get_logger(__name__)


class ActivationState(str, Enum):
    """Inherits str for the same reason src.core.contracts.TaskStatus and
    src.control_center.upgrade_planner.UpgradeStatus do: a plain,
    JSON-serialisable value and a closed set at once - which matters here
    specifically, because a browser-side controller reports this same value
    over the wire (see ARCHITECTURE.md's ambient voice section)."""

    IDLE = "idle"
    ACTIVATED = "activated"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ENDED = "ended"


# States in which a conversation is actually under way - activation has
# happened and the session has not yet ended. Named once so "is a turn
# legal right now" is one fact, not a condition re-derived at every call
# site with room to drift.
_CONVERSING = frozenset(
    (ActivationState.ACTIVATED, ActivationState.LISTENING,
     ActivationState.THINKING, ActivationState.SPEAKING)
)


class NoActiveConversation(RuntimeError):
    """Raised when a turn is attempted before activation has happened.

    A distinct type, not a bare RuntimeError, so a caller can tell "you
    tried to skip activation" from any other failure - and so a test can
    assert on exactly this rather than on any exception at all.
    """


class VoiceActivationSession:
    """One continuous, activation-gated voice conversation over one
    already-existing Channel.

    Holds a ``Channel`` and a ``VoiceActivation`` and nothing else - no
    brain, no memory, no registry, no provider. Every turn is answered by
    calling that channel's own ``ask``/``deliver``, which is the same path
    a typed message already uses; this class adds only the question of
    *whether* a heard utterance is allowed to reach that path yet.

    Lifecycle is ``start()`` / ``stop()`` / ``dispose()``, and ``start()``
    is idempotent: calling it again on an already-started session changes
    nothing. That is what makes rewiring this session safe across whatever
    repeatedly re-runs setup code - a page re-rendering a view, a process
    reconnecting - because the second and further calls are no-ops rather
    than a second session accumulating alongside the first.
    """

    def __init__(
        self,
        channel: Channel,
        activation: VoiceActivation,
        presence: PresenceRegistry | None = None,
    ) -> None:
        self._channel = channel
        self._activation = activation
        self._state = ActivationState.IDLE
        self._started = False
        # Stage 17.2: optional, and None by every caller that predates
        # this - unchanged behaviour when omitted. SPEAKING itself is
        # never asserted here (see turn() below): the wrapped channel's
        # own deliver() already does that, for real, around the one
        # real speak() call. What this class adds is LISTENING - a fact
        # only *this* session actually knows (which agent was activated,
        # and that a conversation with it is genuinely open right now).
        self._presence = presence

    @property
    def channel(self) -> Channel:
        return self._channel

    @property
    def activation(self) -> VoiceActivation:
        return self._activation

    @property
    def state(self) -> ActivationState:
        return self._state

    @property
    def started(self) -> bool:
        """Whether this session is currently listening for anything at
        all - its activation phrase if IDLE, or a turn if already
        conversing. False after construction and after ``stop()``."""
        return self._started

    @property
    def active(self) -> bool:
        """Whether a conversation is currently under way - activation has
        happened and ``end()``/``stop()`` has not been called since."""
        return self._state in _CONVERSING

    @property
    def listening(self) -> bool | None:
        """Whether hearing is worth attempting, exactly as the wrapped
        channel itself reports it.

        Delegated, never re-derived: this session has no engine of its own
        to ask, so it asks the one thing that does. None when the wrapped
        channel has no speech concept at all (a plain text Channel) -
        "not applicable" is a different fact from "not listening", and
        collapsing them would be the exact boolean-shaped mistake
        src/channels/speech.py's own docstring describes.
        """
        return getattr(self._channel, "listening", None)

    def start(self) -> bool:
        """Begin listening for this session's activation phrase.

        Idempotent: an already-started session is left exactly as it was
        and this returns False, so a caller does not need to track whether
        it has already called this - it can simply always call it. Returns
        True only the one time this call actually changed anything.
        """
        if self._started:
            return False
        self._started = True
        self._state = ActivationState.IDLE
        logger.info("voice activation session started for '%s'", self._activation.key)
        return True

    def hear_activation(self, utterance: str) -> bool:
        """Offer one heard utterance as a possible activation phrase.

        Only meaningful while started and IDLE - every other state is
        already inside a conversation, which does not need its own
        activation phrase repeated (that is the entire point of this
        module). Returns whether this utterance activated the session;
        False for unrelated speech, for a session that has not been
        started, and for a session already conversing.
        """
        if not self._started or self._state is not ActivationState.IDLE:
            return False
        if not self._activation.matches(utterance):
            return False
        self._state = ActivationState.ACTIVATED
        # A real conversation with this specific agent just opened - the
        # session is now genuinely waiting to hear the first thing said
        # to it, which is exactly what AgentPresence's own LISTENING
        # means (Stage 17.2).
        if self._presence is not None:
            self._presence.set(self._activation.key, PresenceState.LISTENING)
        logger.info("voice activation matched for '%s'", self._activation.key)
        return True

    def turn(self, request: str, *, explain: bool = True) -> Response:
        """One conversational turn, through the channel this session wraps
        - no second routing, no second delegation, no second Brain call.

        Raises NoActiveConversation when no activation has happened yet, so
        unrelated speech heard before the activation phrase can never be
        mistaken for a request - activation happening first is an
        invariant this method enforces, not a convention a caller is
        trusted to honour.
        """
        if not self.active:
            raise NoActiveConversation(
                "no active voice conversation - the activation phrase has "
                "not been heard yet"
            )
        self._state = ActivationState.THINKING
        response = self._channel.ask(request, explain=explain)
        self._state = ActivationState.SPEAKING
        # SPEAKING itself is asserted by the channel's own deliver() -
        # not duplicated here - because deliver() is the one place that
        # actually knows the answering agent (response.turn.handled_by
        # may differ from self._activation.key, when the activated
        # agent delegates mid voice-turn) and the one place a real
        # speak() call blocks for real. This session's own presence
        # signal below is a different fact: this session's own
        # conversation has resumed listening, true again the instant
        # delivery returns.
        delivered = self._channel.deliver(response)
        self._state = ActivationState.LISTENING
        if self._presence is not None:
            self._presence.set(self._activation.key, PresenceState.LISTENING)
        return delivered

    def end(self) -> ActivationState:
        """End the current conversation. Returns to IDLE immediately,
        ready for the next activation phrase - this session keeps
        listening for it unless ``stop()`` is also called.

        A no-op, safely, when no conversation was active: ending nothing
        still lands at IDLE, which is where an unstarted or already-idle
        session already was.
        """
        ended = ActivationState.ENDED
        self._state = ActivationState.IDLE
        if self._presence is not None:
            self._presence.clear(self._activation.key)
        logger.info("voice conversation ended for '%s'", self._activation.key)
        return ended

    def stop(self) -> None:
        """Stop this session entirely - no longer listening for the
        activation phrase, and no longer mid-conversation. ``start()`` must
        be called again before ``hear_activation``/``turn`` do anything.
        """
        self._started = False
        self._state = ActivationState.IDLE
        if self._presence is not None:
            self._presence.clear(self._activation.key)

    def dispose(self) -> None:
        """Release this session. Equivalent to ``stop()``; kept as a
        separate name so cleanup code at a call site reads as cleanup
        rather than as a pause that might resume."""
        self.stop()


# ---------------------------------------------------------------------------
# 0.2 Reality Audit: the missing glue - continuous listening, driving this
# session from real (or scripted) heard speech rather than typed text.
# ---------------------------------------------------------------------------
#
# The gap this closes: this class already answers "is a heard utterance
# allowed to start or continue a conversation" - that was always the hard,
# stateful part, and it was already right. What never existed anywhere in
# this project was the loop that actually calls a Listener repeatedly and
# feeds what it hears in here. No new engine, no new dependency: the
# existing bounded, local `SpeechAdapter.listen()` (Windows SAPI, already
# real and already verified this audit) called back-to-back *is* ambient
# listening for a system with no continuous audio stream - each call is a
# bounded window of silence-or-speech, and calling it again the instant it
# returns is indistinguishable, from the outside, from one long-running
# listener. This is the smallest correct implementation, not a
# placeholder for a "real" one: it reuses 100% existing primitives
# (Listener.listen, hear_activation, turn, end) and adds only the loop
# and the ordinary-silence-vs-device-failure distinction listen() itself
# already reports through Availability.

# Phrases that close an open conversation early, the same "recognised by
# marker phrase, never a subject list" discipline every other intent
# recognizer in this project already follows - see
# src/manager/capabilities.py's own conversational_intent for the
# identical reasoning applied to a different boundary.
_END_CONVERSATION_MARKERS: tuple[str, ...] = (
    "goodbye", "good bye", "that's all", "that is all",
    "stop listening", "never mind", "nothing else",
)

# Consecutive "no speech was recognized" results, once a conversation is
# open, after which the session closes itself and returns to waiting for
# the activation phrase again - the ambient-listening equivalent of a
# person trailing off and not being spoken to again. Never applied while
# IDLE: silence before activation is simply the normal, expected state
# and must never end anything, because nothing is open to end.
_DEFAULT_IDLE_TURNS_BEFORE_AUTO_END = 3


class MicrophoneUnavailable(RuntimeError):
    """Raised when listening cannot even be attempted - no engine, or the
    engine's own first response was a genuine device/permission failure
    rather than ordinary silence.

    Distinct from ``SpeechUnavailable`` (which every ordinary "nobody is
    speaking right now" moment also raises, and which this loop treats as
    expected and keeps going through): this is what the loop raises
    *upward* when it has decided listening itself cannot continue, so a
    caller can report a real, actionable failure rather than a silent
    hang.
    """


def _is_ordinary_silence(exc: SpeechUnavailable) -> bool:
    """Whether this failure is just "nobody said anything in the window" -
    the expected, constant background state of ambient listening - as
    opposed to a genuine engine, device or permission failure.

    Read from the exception's own message rather than a new field on
    ``SpeechUnavailable``, because every existing engine (and every
    existing test double) already raises this type with a message and
    nothing here needs a second attribute to agree on.
    """
    return "no speech was recognized" in str(exc).lower()


def _sounds_like_end_of_conversation(utterance: str) -> bool:
    lowered = " ".join(str(utterance or "").lower().split())
    return any(marker in lowered for marker in _END_CONVERSATION_MARKERS)


def run_ambient_loop(
    session: VoiceActivationSession,
    *,
    listen_timeout: float = 5.0,
    idle_turns_before_auto_end: int = _DEFAULT_IDLE_TURNS_BEFORE_AUTO_END,
    should_continue: "Callable[[], bool] | None" = None,
) -> None:
    """Listen continuously, drive ``session`` from what is actually heard,
    and let a matched activation flow straight into the existing
    conversation/exchange path this session already wraps.

    **What one iteration does:**

        listen (bounded window)
          -> nothing heard (ordinary silence)  -> loop again
          -> IDLE and it matches the activation phrase -> hear_activation()
          -> already conversing                -> turn() (asks, delivers,
                                                   speaks - the existing
                                                   voice path, unchanged)
          -> a heard utterance sounds like "goodbye"  -> end()
          -> too many silent turns in a row while conversing -> end()

    **Never persists anything itself.** Every word this loop hears either
    matches nothing (discarded, never stored, never logged as content) or
    reaches ``session.turn()``, which is the exact same
    ``Channel.ask``/``Channel.deliver`` pair a typed command already
    uses - so a genuine "record this for later" spoken after activation
    is captured through the identical, already-governed capability path,
    and ordinary ambient chatter that never triggers activation is never
    seen by anything that writes to memory at all.

    **Fails loudly, once, rather than spinning silently.** The very first
    ``SpeechUnavailable`` that is *not* ordinary silence (no engine
    installed, a device or permission failure reported by the engine
    itself) raises ``MicrophoneUnavailable`` and stops the loop - this is
    the honest boundary a person, not this loop, has to resolve (grant a
    permission, plug in a microphone, install an engine).

    ``should_continue``, when given, is polled once per iteration and
    lets a caller stop the loop on its own terms (a shutdown flag, a
    fixed iteration count in a test) without this function knowing
    anything about why.
    """
    session.start()
    speech = getattr(session.channel, "speech", None)
    if speech is None:
        raise MicrophoneUnavailable(
            f"the {session.channel.name} channel carries no speech engine to listen with"
        )

    idle_turns = 0
    while should_continue is None or should_continue():
        try:
            heard = speech.listen(timeout=listen_timeout)
        except SpeechUnavailable as exc:
            if not _is_ordinary_silence(exc):
                raise MicrophoneUnavailable(str(exc)) from exc
            if session.active:
                idle_turns += 1
                if idle_turns >= idle_turns_before_auto_end:
                    session.end()
                    idle_turns = 0
            continue

        idle_turns = 0

        if not session.active:
            session.hear_activation(heard)
            continue

        if _sounds_like_end_of_conversation(heard):
            session.end()
            continue

        try:
            session.turn(heard)
        except NoActiveConversation:
            # A race between this loop's own state check and a
            # concurrent end() - honestly nothing to do with this
            # utterance now; the next iteration re-evaluates cleanly.
            continue
