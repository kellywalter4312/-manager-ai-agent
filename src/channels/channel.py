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

    def ask(self, request: str, *, explain: bool = True) -> Response:
        """Put one request to Alfred. Does not present it, and does not speak.

        **The single path.** Every channel funnels through here, and here
        funnels through `observe`, which is the same function the Control
        Center's typed interface uses. There is no second orchestration, and
        no channel can reach the Manager any other way without going around
        this method deliberately.

        The Response comes back with ``spoken`` False in every channel,
        because nothing has been presented yet. See ``deliver``.
        """
        turn = observe(self._manager, Task(description=request), explain=explain)
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
    ) -> None:
        super().__init__(manager)
        self._speech = speech if speech is not None else NoSpeech()
        # Text is always available, so it is always the fallback. This is what
        # makes "voice is enabled" never mean "typing is disabled".
        self._fallback = fallback if fallback is not None else TextChannel(manager)

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
        """
        spoken = self._speech.speak(response.speech)
        print(response.text)
        if not spoken and self._speech.available:
            logger.warning("the speech engine accepted nothing to say")
        return replace(response, spoken=spoken)
