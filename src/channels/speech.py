"""Turning speech into text and text into speech - or honestly saying you cannot.

The contract, and nothing that implements it against real audio. That is
deliberate and it is the same position `web_search` is in: this repository has
no speech library installed, and shipping one would mean choosing a dependency,
a model and a platform on the user's behalf for a feature nobody has yet asked
to run.

So what exists here is the seam. `NoSpeech` is what the system actually has,
and it reports that plainly rather than pretending:

    availability   UNAVAILABLE, "no speech engine is installed"
    listen         raises, because there is nothing to listen with
    speak          returns False, meaning "this was not spoken"

A caller must be able to tell "Alfred said nothing" from "Alfred spoke", and
the boolean returned by speak() is that difference. A speak() that silently
returned True would make a mute system indistinguishable from a talking one,
which is the exact class of lie the rest of this project refuses.

**Why availability is not a boolean.** This project has already made this
mistake once, in the place it mattered most. `assess_cognition` used to read a
provider *name* and report FULL BRAIN, so an API key with an exhausted balance
was described as verified model reasoning - confidently, forever, until
somebody asked a question and got nothing. `src/providers/availability.py` was
written to end that, and its rule is the one adopted here:

    configured   a library imported, an object constructed, a key present
    usable       a call to it has actually succeeded

Those are different claims, and a boolean cannot hold both. It has two states
and there are three facts, so the one that gets lost is always the same one -
"we have not tried yet" - and an implementer forced to collapse it will write
`return self._client is not None`, which is the old bug with a new name. A
speech engine has every ingredient for it: a library that imports cleanly, a
cloud quota with nothing left, an audio device that is not there.

So the same three-state `Availability` this project already uses for models is
reused verbatim rather than reinvented for speech. UNKNOWN is a real answer;
only a call that came back can produce AVAILABLE.

**Why an adapter rather than a voice module.** Speech is I/O. Whether it comes
from a local model, an OS service or a cloud API changes nothing about Alfred:
he receives a string and produces a string either way. Keeping the engine
behind this interface is what stops a microphone reaching into the Manager, and
what will let a real engine arrive later as an additive change.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.providers.availability import Availability, AvailabilityState
from src.utils.logging import get_logger

logger = get_logger(__name__)


class SpeechUnavailable(RuntimeError):
    """Raised when speech is asked for and no engine can provide it.

    A distinct type rather than a bare RuntimeError so a caller can catch
    exactly this and fall back to text - which is what every caller in this
    project does, because text is always available and voice is a convenience.
    """


class SpeechAdapter(ABC):
    """Speech in, speech out. Narrow on purpose.

    Two methods and a flag. A narrow interface is cheap to implement, which is
    what makes a real engine - or a fake one in a test - practical to add
    without touching anything else.

    Note what is absent: no wake word, no audio buffer, no device handling, no
    streaming. Those belong to whatever implements this, and putting them in
    the contract would tie every future engine to the shape of the first one.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for logs and status, e.g. "none"."""

    @property
    @abstractmethod
    def availability(self) -> Availability:
        """What was last observed about this engine. Evidence, not a verdict.

        The source of truth, and the only thing an implementation has to
        answer. Recorded by whatever actually calls the engine - the same
        place `LLMManagerBrain` records it, for the same reason: that method
        already wraps its call in a boundary catch, so observing costs nothing
        and no extra call is ever made to ask.

        UNKNOWN until a call has happened, and UNKNOWN is a real answer rather
        than a placeholder. An engine whose library imported, whose key parsed
        and whose object constructed has proved somebody intended speech; it
        has not proved a speaker exists.

        One Availability covers the whole adapter for now, so an engine that
        can speak but not hear reports a single verdict for both. That is a
        known simplification and not a trap: when listening and speaking split
        into separate interfaces, each publishes this same property under this
        same name.
        """

    @property
    def available(self) -> bool:
        """Whether a call to this engine has actually succeeded.

        Concrete and derived, deliberately. Having a boolean is not the
        problem - `Availability.is_usable` and `CognitiveProfile.is_full_brain`
        are both booleans. The problem is a boolean that each implementation
        *answers for itself*, because the natural thing to write is "is my
        client configured", and configuration is not usability.

        Being non-abstract is what makes that unwriteable: there is no True to
        return here that evidence did not produce. An engine cannot declare
        itself usable; it can only be observed to have worked.
        """
        return self.availability.is_usable

    @abstractmethod
    def listen(self, timeout: float | None = None) -> str:
        """Capture one spoken utterance and return it as text.

        Raises SpeechUnavailable when there is nothing to listen with. It must
        never return an empty string to mean failure - a caller cannot tell
        that from someone saying nothing.
        """

    @abstractmethod
    def speak(self, text: str) -> bool:
        """Say something. Returns whether it was actually spoken.

        False is a real answer, not an error: with no engine installed the
        system still has a written response, and a caller that knows the words
        were not spoken can make sure they are read.
        """


# What NoSpeech reports, fixed and shared. Frozen, so one value is safe for
# every instance, and a constant rather than a per-call construction because
# nothing about it is ever observed - see the class below.
NO_ENGINE_INSTALLED = Availability(
    state=AvailabilityState.UNAVAILABLE,
    reason="no speech engine is installed",
)


class NoSpeech(SpeechAdapter):
    """The adapter this project actually ships: none.

    Not a placeholder to be replaced before the system works - it is what makes
    "there is no speech engine here" a valid, testable state rather than a
    special case. The same role NullProvider plays for models and an empty
    ToolSet plays for capabilities.

    **UNAVAILABLE rather than UNKNOWN, and the difference is deliberate.** A
    model provider reports UNKNOWN before its first call because it genuinely
    cannot know - there is a network and a billing account in the way, and no
    provider this project speaks to publishes a quota endpoint. This adapter is
    not in that position. Its inability is structural and local: there is no
    engine, nothing to try, and no future call that could change the answer.
    Every listen() raises and every speak() returns False, so the failure is
    guaranteed rather than hypothetical.

    Reporting UNKNOWN would be the weaker claim, and it would cost something
    real: a screen could no longer tell "there is no engine here" from "an
    engine exists and has not been tried yet". Those are different situations
    and a person looking at a status line deserves to know which one they are
    in.

    Nothing here probes for a device, a library or a driver. The certainty
    comes from the fact that this class implements no engine at all, which is
    knowable without asking anything.
    """

    @property
    def name(self) -> str:
        return "none"

    @property
    def availability(self) -> Availability:
        return NO_ENGINE_INSTALLED

    def listen(self, timeout: float | None = None) -> str:
        raise SpeechUnavailable(
            "no speech engine is installed, so nothing can be heard. "
            "Type instead - text is always available."
        )

    def speak(self, text: str) -> bool:
        logger.debug("speech requested but no engine is installed")
        return False
