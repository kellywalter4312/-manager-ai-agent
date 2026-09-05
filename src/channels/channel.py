"""Ways of reaching Alfred. There is only ever one Alfred.

The rule this module exists to enforce, stated once:

**A channel is a door, not a second agent.** Text and voice are two ways into
the same Manager - the same brain, the same memory, the same capabilities, the
same tools, the same registry, the same trail. A voice path that built its own
Alfred would be a second system that drifts, and the first time the two
disagreed nobody would know which was real.

So a Channel does exactly two things: it collects a request, and it delivers a
response. It does not route, select a capability, consult a brain, read a
memory, invoke a tool or choose a specialist. Every one of those already
belongs to something, and a channel that started doing any of them would have
stopped being a door.

                    TEXT            VOICE
                      |               |
                      +-------+-------+
                              |
                       observe(manager, task)
                              |
                        the one Alfred
                              |
                      +-------+-------+
                      |               |
                  WRITTEN         SPOKEN

Note that the written response survives whichever door was used. Speech can
fail, an engine can be missing, a room can be loud - and a person still needs
to be able to read what was said. `Response.spoken` records whether the words
actually left a speaker, so "Alfred answered but could not say it aloud" is
distinguishable from "Alfred spoke".
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, replace

from src.channels.speech import NoSpeech, SpeechAdapter, SpeechUnavailable
from src.control_center.trail import observe
from src.control_center.views import ConversationTurn
from src.core.contracts import Task
from src.manager.manager import ManagerAgent
from src.services.identity.presence import (
    PresenceRegistry,
    presentation_intent_for_turn,
    speaking,
)
from src.services.identity.voice import VoiceProfile
from src.services.identity.voice_preference import effective_voice_for
from src.utils.logging import get_logger

logger = get_logger(__name__)

# How much of an answer is worth saying aloud. A specialist that analysed a
# whole inbox returns pages; reading pages at somebody is not conversation.
# The written response always carries the whole thing.
SPOKEN_LIMIT = 400


@dataclass(frozen=True)
class Response:
    """One answer, in every form it took.

    ``text`` is always present and always complete. ``speech`` is what would be
    said aloud, which may be shorter. ``spoken`` records what actually
    happened, so nothing has to assume.

    **``spoken`` is False until a delivery has actually happened.** ``ask``
    produces an answer; it does not present one, and an answer that has not
    been presented has not been said aloud. Only ``deliver`` can set this
    True, and only by returning a new Response after an engine reported
    success. A Response fresh from ``ask`` claiming it had been spoken would
    be describing a speaker that had not yet been reached.
    """

    text: str
    speech: str
    spoken: bool
    turn: ConversationTurn

    @property
    def channel_note(self) -> str:
        """A short, honest line about how this answer was delivered."""
        return "spoken and written" if self.spoken else "written only"


def speakable(turn: ConversationTurn) -> str:
    """What is worth saying aloud from a turn.

    Derived from the turn rather than chosen from a list of phrases. The first
    meaningful lines of the real answer are what a person wants to hear; the
    rest is available to read. A system with a fixed set of spoken sentences
    would be a toy, and would start lying the moment the answer did not fit
    one of them.
    """
    text = (turn.outcome or "").strip()
    if not text:
        return "I have nothing to report for that."

    first = text.split("\n\n")[0].strip() or text
    if len(first) <= SPOKEN_LIMIT:
        return first

    cut = first[:SPOKEN_LIMIT]
    stop = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if stop > SPOKEN_LIMIT // 3:
        return cut[: stop + 1]
    return cut.rstrip() + "..."


class Channel(ABC):
    """A way of reaching the Manager. Collects a request, delivers an answer.

    Holds the Manager it talks to and nothing else. In particular it holds no
    brain, no memory, no toolset and no registry: it asks the Manager, and the
    Manager does what it has always done.
    """

    def __init__(self, manager: ManagerAgent) -> None:
        self._manager = manager

    @property
    def manager(self) -> ManagerAgent:
        return self._manager

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier, e.g. "text" or "voice"."""

    @abstractmethod
    def receive(self) -> str | None:
        """One request from whoever is on the other side, or None to stop."""

    @abstractmethod
    def deliver(self, response: Response) -> Response:
        """Present an answer through this channel, and report what happened.

        Returns the response *as delivered*, which is not always the response
        that went in: a voice channel that reached a speaker returns one whose
        ``spoken`` is True. Returning it rather than mutating it keeps Response
        frozen and keeps the channel stateless - a channel that remembered its
        last delivery would be holding conversation state, which belongs to the
        Manager's memory and not to a door.

        **This is the only place speech happens.** ``ask`` obtains an answer
        and stops. Speaking during ``ask`` as well as here is how one request
        gets said aloud twice, which is a bug nothing notices while the shipped
        engine is silent.
        """

    def ask(
        self,
        request: str,
        *,
        explain: bool = True,
        addressed: str | None = None,
    ) -> Response:
        """Put one request to Alfred. Does not present it, and does not speak.

        **The single path.** Every channel funnels through here, and here
        funnels through `observe`, which is the same function the Control
        Center's typed interface uses. There is no second orchestration, and
        no channel can reach the Manager any other way without going around
        this method deliberately.

        The Response comes back with ``spoken`` False in every channel,
        because nothing has been presented yet. See ``deliver``.
        """
        # `addressed` is carried, never acted on here. A door that chose
        # an agent would be a second router; this hands the Manager the
        # context and the Manager decides what it means.
        turn = observe(
            self._manager,
            Task(description=request),
            explain=explain,
            addressed=addressed,
        )
        return Response(
            text=turn.outcome or turn.detail or "",
            speech=speakable(turn),
            spoken=False,
            turn=turn,
        )


class TextChannel(Channel):
    """Typing. Always available, and the fallback for everything else."""

    def __init__(self, manager: ManagerAgent, prompt: str = "> ") -> None:
        super().__init__(manager)
        self._prompt = prompt

    @property
    def name(self) -> str:
        return "text"

    def receive(self) -> str | None:
        try:
            line = input(self._prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return None
        return line or None

    def deliver(self, response: Response) -> Response:
        """Print it. Typing never speaks, so ``spoken`` stays False."""
        print(response.text)
        return response


class VoiceChannel(Channel):
    """Speaking and being spoken to - the same Alfred, a different door.

    Given no working speech engine this degrades to text rather than failing:
    the request is still read, Alfred still answers, the answer is still
    written, and `Response.spoken` says it was not said aloud. A voice channel
    that refused to work without a microphone would make voice a prerequisite
    for using the system rather than a convenience.
    """

    def __init__(
        self,
        manager: ManagerAgent,
        speech: SpeechAdapter | None = None,
        fallback: Channel | None = None,
        presence: PresenceRegistry | None = None,
    ) -> None:
        super().__init__(manager)
        self._speech = speech if speech is not None else NoSpeech()
        # Text is always available, so it is always the fallback. This is what
        # makes "voice is enabled" never mean "typing is disabled".
        self._fallback = fallback if fallback is not None else TextChannel(manager)
        # Stage 17.2: optional, and None by every caller that predates
        # this - a channel built without one behaves exactly as before.
        # When given, it is the same shared PresenceRegistry the Control
        # Center reads (see composition.py's Runtime.presence_registry),
        # so a real speak() call below becomes a real, live SPEAKING
        # signal somebody else can observe while it is still true.
        self._presence = presence

    @property
    def name(self) -> str:
        return "voice"

    @property
    def speech(self) -> SpeechAdapter:
        return self._speech

    @property
    def listening(self) -> bool:
        """Whether hearing is worth attempting. A report, never a decision.

        Deliberately ``not is_known_broken`` rather than ``is_usable``. Those
        differ on exactly one state, and it is the state that matters: an
        engine that has never been called is UNKNOWN, which is not proof that
        it works and is not proof that it does not. Reporting such an engine as
        not-listening would be claiming knowledge nobody has.

            NoSpeech           UNAVAILABLE   -> False, and certainly so
            a fresh engine     UNKNOWN       -> True, meaning "worth trying"
            a proven engine    AVAILABLE     -> True

        Nothing consults this before calling ``listen``. It exists so a status
        screen can say something true; the moment it gated anything it would
        recreate the deadlock described in ``receive``.
        """
        return not self._speech.availability.is_known_broken

    def receive(self) -> str | None:
        """Try to hear something; fall back to typing when that fails.

        **Attempted, never pre-authorised.** There used to be a check here
        that skipped ``listen`` unless the engine reported itself available,
        which looked careful and was a deadlock. Availability is evidence, and
        evidence only comes from a call: an engine that has never been used is
        UNKNOWN, so it was refused; being refused, it was never used; never
        being used, it stayed UNKNOWN. A real microphone would have been locked
        out permanently by the check meant to describe it.

        So the call is made and failure is handled, which is the same shape
        `LLMManagerBrain.deliberate` uses - it always calls the provider and
        records what happened, rather than asking permission from a record it
        is itself responsible for writing. `availability.py` says it plainly:
        availability "grants nothing, gates nothing and decides nothing".

        Behaviour for a system with no engine is unchanged. NoSpeech raises
        SpeechUnavailable, the except catches it, and typing takes over exactly
        as before - one raised-and-caught exception per turn is the whole cost.
        """
        try:
            heard = self._speech.listen()
        except SpeechUnavailable as exc:
            logger.info("falling back to text: %s", exc)
            return self._fallback.receive()
        return heard.strip() or None

    def deliver(self, response: Response) -> Response:
        """Say it once, print it either way, and report which happened.

        Printing is not a fallback here - it is the point. A spoken answer that
        vanished the moment it was said would be worse than a written one, so
        the text is always shown and speech is added when it can be.

        Exactly one call to the engine per delivery. There is deliberately no
        ``ask`` override on this class: an earlier version had one that spoke
        so it could report ``spoken``, which meant a caller doing the obvious
        ``deliver(ask(x))`` said everything twice. Nothing caught it, because
        the only engine that ships is silent.

        **Stage 17.2: the real speaking/presence synchronisation point.**
        Whoever answered (``response.turn.handled_by``) is marked SPEAKING,
        via ``speaking()``, for exactly as long as the call below actually
        takes - real for a blocking engine (``WindowsSpeechAdapter``,
        ``ElevenLabsSpeechAdapter`` both block until playback finishes),
        momentary and still honest for ``NoSpeech``. This is the *only*
        place that happens, because this is the only place speech happens -
        adding a second SPEAKING assertion elsewhere would be exactly the
        double-announcement this method's own docstring already refuses for
        audio itself.

        **Stage 17.9: the one real PresentationIntent producer.**
        Immediately before speaking, this asks
        ``presentation_intent_for_turn()`` for a real, structured
        presentation intent - never invented, never a reading of what
        the words mean (see that function's own docstring) - and, when
        one exists, asserts it on the same shared registry for exactly
        the speaking window, cleared in ``finally`` so an exception can
        never leave a stale intent behind. This does not create a
        second SPEAKING-style mechanism: it reuses the identical
        registry ``speaking()`` above already writes to, through its
        own, separate ``set_intent``/``clear_intent`` pair.
        """
        # Only a real, registered agent is marked SPEAKING - the same
        # "is this an agent?" guard scripts/exchange.py already applies
        # before attributing a spoken line in the room's own transcript.
        # `handled_by` carries the literal sentinel "nobody" when
        # nothing matched the request, and that must never become a
        # presence entry - "nobody" is not an agent this project could
        # ever ask presence_for() about.
        answered = getattr(response.turn, "handled_by", "") or ""
        if answered and self._manager.registry.get(answered) is None:
            answered = ""
        intent = presentation_intent_for_turn(response.turn)
        if self._presence is not None and answered and intent is not None:
            self._presence.set_intent(answered, intent)
        try:
            with speaking(self._presence, answered):
                spoken = self._speech.speak(response.speech, self._voice_of(response))
        finally:
            if self._presence is not None and answered and intent is not None:
                self._presence.clear_intent(answered)
        print(response.text)
        if not spoken and self._speech.available:
            logger.warning("the speech engine accepted nothing to say")
        return replace(response, spoken=spoken)

    def _voice_of(self, response: Response) -> VoiceProfile | None:
        """Whose voice this answer should be said in.

        Looked up, never decided. The turn already records *who answered*, so
        the channel asks the registry for that agent and asks the agent for its
        voice - which is a fact about the agent's identity, not a choice this
        door gets to make. A channel that picked a voice would be assigning
        identity from the presentation layer.

        No agent is named here and no mapping is kept. A future specialist is
        heard in its own voice because it has one, not because this method
        learned about it.

        Returns None when the agent declares no voice or cannot be found. None
        means "no preference", which a speaker handles; inventing a voice would
        be the presentation layer deciding what somebody sounds like.

        0.2 Reality Audit: asks for the *effective* voice, not the bare
        declared default - a runtime preference set through the "voice"
        command (services/identity/voice.py's own governed mechanism,
        the same shape avatar preferences already use) overrides
        IDENTITY.md's own ``preferred_voice:`` line, exactly as a user
        selection must. ``recall`` is resolved the same way
        scripts/exchange.py's own ``memory_for`` already does: the
        Manager's own store when the answerer is the Manager himself
        (``own_skills`` exposes no ``.memory`` directly), that agent's
        own store otherwise. No recall reaches this call for an agent
        with no memory at all, which degrades to exactly the bare
        declared voice - never a crash, never a fabricated preference.
        """
        answered = getattr(response.turn, "handled_by", "")
        if not answered:
            return None
        try:
            agent = self._manager.registry.get(answered)
        except Exception:  # noqa: BLE001 - looking up a voice must not break delivery
            return None
        if agent is None:
            return None
        manager_name = self._manager.own_skills.name if self._manager.own_skills else None
        memory = self._manager.memory if answered == manager_name else getattr(agent, "memory", None)
        recall = memory.recall if memory is not None else None
        return effective_voice_for(agent, recall=recall)
