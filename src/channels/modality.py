"""What kind of thing a person put into a conversation. A representation,
not a processor.

Today, exactly two modalities are genuinely handled - typed text
(``TextChannel``) and spoken audio, which an ``STT`` provider turns into text
before anything else in this project ever sees it (``src/channels/speech.py``,
``src/providers/speech_factory.py``). A camera photo, a video, an existing
file are all real things a person will eventually want to bring into a
conversation with Alfred, and this module exists so *naming* that future is
possible without inventing a competing representation the day one of them is
actually built.

**This module processes nothing.** There is no image decoder, no video
frame extractor, no file reader here - only an enum naming the shapes a
conversation input can take, and a small value type carrying one. Wiring
``IMAGE``/``VIDEO``/``FILE`` into a real capability - a vision-capable Brain
call, a file-reading tool - is exactly the kind of work this stage explicitly
does not do, and pretending otherwise here would be the fabricated-camera
mistake this stage was told not to make, just moved into a type definition
instead of a UI.

**Deliberately not wired into ``Task``/``Channel`` yet.** The existing text
and voice pipeline (``Channel.ask(request: str)``) is unaffected by this
module's existence - it does not import it, and this module does not import
anything from ``src.channels.channel`` or ``src.core.contracts``. That is
the honest state: a clean seam a future stage can build against, not a claim
that conversations already carry a modality tag today.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class InputModality(str, Enum):
    """Inherits str for the same reason every closed-set status in this
    project does (``TaskStatus``, ``UpgradeStatus``, ``ActivationState``): a
    plain, JSON-serialisable value and a closed set at once."""

    TEXT = "text"
    AUDIO = "audio"
    IMAGE = "image"
    VIDEO = "video"
    FILE = "file"


# Modalities an existing seam can actually turn into something Alfred acts
# on today: typed text directly, spoken audio via an STT provider. Naming
# this set once, here, is what lets ConversationInput.is_handled answer
# honestly rather than every caller re-deriving the same two-item list.
_HANDLED: frozenset[InputModality] = frozenset({InputModality.TEXT, InputModality.AUDIO})


@dataclass(frozen=True)
class ConversationInput:
    """One piece of input a person brought into a conversation, and which
    shape it takes - never what it means or how it was produced.

    ``text`` is the input's textual form when it has one already - what a
    person typed, or what an STT provider already transcribed. It is empty
    for a modality nothing here can turn into text yet.

    ``media`` is opaque, carried and never interpreted here - the same
    contract ``MemoryRecord.context`` and ``VoiceProfile.style`` already
    have. It is where a future stage's own representation of "which photo",
    "which file path", "which video frame range" goes, without this type
    ever learning what any of those mean.
    """

    modality: InputModality
    text: str = ""
    media: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "media", dict(self.media))

    @property
    def is_handled(self) -> bool:
        """Whether an existing seam can actually turn this into something
        Alfred acts on today - never a claim about what *should* eventually
        be possible, only what genuinely is.

        True for TEXT and AUDIO: a Channel already accepts typed text
        directly, and an STT provider already exists to turn spoken audio
        into text (whether that provider is actually usable is a separate,
        honest question - see ``SpeechAdapter.availability``). False for
        IMAGE, VIDEO and FILE - naming them is this module's whole job;
        acting on them is not.
        """
        return self.modality in _HANDLED

    def describe(self) -> str:
        detail = "handled" if self.is_handled else "not yet handled"
        return f"{self.modality.value} ({detail})"
