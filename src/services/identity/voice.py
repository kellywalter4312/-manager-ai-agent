"""What an agent sounds like, and how it is addressed. Two facts, not one.

The requirement this exists for: in a conversation with several agents, a
person should be able to tell who is speaking without looking at the screen.
That makes a voice part of an agent's *identity* - alongside its name, its
brain domain and its memory store - rather than a setting on whatever happens
to be producing audio.

    AGENT IDENTITY  ->  VoiceProfile  ->  Speech interface  ->  a provider

Read that chain left to right and note where the vendor is: at the far end,
and only there.

**A second, distinct fact lives here too: how an agent is addressed by
voice.** ``VoiceProfile`` answers "how does this agent sound"; the
``VoiceActivation`` type below answers "how does the system recognize that
a person is talking to this agent" - a wake phrase, not a timbre. They are
kept in one module because they are both facts an identity document
declares about one agent, and kept as two distinct types because changing
one must never change the other: giving an agent a new voice must not
change how it is addressed, and giving it a new activation phrase must not
change how it sounds. Neither names an agent, exactly as ``VoiceProfile``
does not - a future specialist gets its own activation identity by
declaring one, and nothing in this module learns it exists.

**A profile describes a voice. It never names a vendor.** No model id, no
endpoint, no API key, no provider name - and no field for one to live in. The
reason is the same one that keeps capabilities out of a specialist document: a
document that could name an endpoint would be choosing network egress, and
choosing where a request goes is a composition decision, not a description of
who somebody is. Which actual voice a provider uses for "measured, low, formal,
nigerian-english" is the provider's mapping to make, and swapping providers
must not mean rewriting every agent.

**No agent is named here.** A profile is data; the identity document supplies
it. A future specialist gets its own voice by declaring one, and nothing in
this module learns it exists - the same rule the registry follows for brains
and memories.

What this module deliberately does not do: produce audio, choose a provider,
decide who speaks, or hold any state. It is a value object and a resolver.

A runtime, user-changeable voice *preference* - overriding the declared
default below without editing this document - is a separate concern, kept
in ``services/identity/voice_preference.py`` rather than here: this module
is verified (``tests/test_voice_identity.py`` and others) to import
nothing at all from ``src``, and a governed Memory preference genuinely
needs one. See that module for the mechanism.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class VoiceProfile:
    """One agent's voice identity, described rather than configured.

    Every field except ``key`` is a *characteristic* - the sort of thing you
    would tell a person who was going to read something aloud. A provider turns
    those into whatever it actually supports; a provider that supports none of
    them ignores them and still works, which is what makes the abstraction
    survive a change of vendor.

    ``style`` is opaque, carried and never interpreted here - the same contract
    ``MemoryRecord.context`` and ``Utterance.detail`` have. It is where future
    emotion or delivery parameters go without this type learning what they mean.

    ``preferred_voice`` (Stage 11.4) is different in kind from the four
    characteristics above it, and deliberately still not a vendor id: it is
    a name the user chose, in their own words, never a provider's internal
    identifier this type would have to understand. A concrete speech
    provider that supports explicit selection honours it only when it is
    genuinely one of *that provider's own* currently available voices, and
    falls back to its existing default choice otherwise - this field is a
    preference, never a guarantee, and asking for a voice that does not
    exist must not mean no voice at all. Which provider, and how it maps
    this string onto a real installed voice, is that provider's own
    module's concern, never this one's.
    """

    key: str
    accent: str = ""
    pace: str = ""
    pitch: str = ""
    warmth: str = ""
    preferred_voice: str = ""
    style: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("a VoiceProfile must have a key")
        for name in ("key", "accent", "pace", "pitch", "warmth", "preferred_voice"):
            object.__setattr__(self, name, " ".join(str(getattr(self, name)).split()))
        object.__setattr__(self, "key", self.key.lower())
        object.__setattr__(self, "style", dict(self.style))

    @property
    def traits(self) -> tuple[str, ...]:
        """The stated characteristics, in a stable order, blanks dropped.

        What a provider is given to work with. A profile that states nothing
        but a key is valid and means "this agent has its own voice, and no
        preference about how it sounds" - which is still enough to keep it
        distinct from another agent's.
        """
        return tuple(
            value
            for value in (self.accent, self.pace, self.pitch, self.warmth, self.preferred_voice)
            if value
        )

    def describe(self) -> str:
        stated = ", ".join(self.traits) or "no stated characteristics"
        return f"{self.key} ({stated})"


_TRAILING_PUNCTUATION = re.compile(r"[.!?,]+$")


def _normalised_phrase(text: str) -> str:
    """The form an activation phrase is compared in: lower-cased, collapsed
    whitespace, one trailing punctuation mark dropped.

    Deliberately not a fuzzy or semantic match - that would be a second
    reasoning engine living outside the Brain, guessing at intent from
    sound. This is the same honest, simple comparison a person doing this
    by hand would make, nothing cleverer.
    """
    collapsed = " ".join(str(text or "").split()).lower()
    return _TRAILING_PUNCTUATION.sub("", collapsed).strip()


@dataclass(frozen=True)
class VoiceActivation:
    """How the system recognizes that a person is addressing this agent by
    voice - before a single word of the request itself has been heard.

    A different responsibility from ``VoiceProfile`` above, and kept as a
    separate type for the same reason a voice is kept separate from a name:
    changing how an agent sounds must never change how it is addressed, and
    changing how it is addressed must never change how it sounds. A screen
    or an engine that conflated the two would make "give this agent a new
    wake phrase" and "give this agent a new voice" the same edit, and they
    are not the same fact.

    ``phrases`` holds every configured way of addressing this agent - an
    initial, default pair this project ships for the one agent that
    declares them today, never the limit of what this type can hold.
    Adding a third phrase, or a phrase for a future agent, is a matter of
    adding a string here (or a line in an identity document, see
    ``src/services/identity/identity.py``'s ``## Voice Activation``
    section) - never a change to whatever matches against this type.
    """

    key: str
    phrases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("a VoiceActivation must have a key")
        object.__setattr__(self, "key", " ".join(str(self.key).split()).lower())
        # Normalised and de-duplicated once, here, rather than on every
        # match() call - and de-duplicated with dict.fromkeys() rather than
        # a set so two authors writing the same phrase twice by accident
        # does not silently reorder the rest.
        normalised = tuple(
            dict.fromkeys(
                phrase
                for phrase in (_normalised_phrase(raw) for raw in self.phrases)
                if phrase
            )
        )
        object.__setattr__(self, "phrases", normalised)

    def matches(self, heard: str) -> bool:
        """Whether one heard utterance is this agent's activation phrase.

        An agent with no configured phrases matches nothing - silence is
        the honest answer for "how do I address an agent with no declared
        way of being addressed", never a guess.
        """
        return bool(self.phrases) and _normalised_phrase(heard) in self.phrases

    def describe(self) -> str:
        if not self.phrases:
            return f"{self.key} (no activation phrase configured)"
        return f"{self.key}: " + ", ".join(f'"{p}"' for p in self.phrases)


# What an agent gets when its document says nothing about how it sounds.
#
# Deliberately not a shared profile object. Two agents with no declared voice
# must not end up with the *same* key, because a screen or a provider keying on
# it would then give them one voice - which is the failure this whole module
# exists to prevent. So the default is derived from the agent's own name and is
# therefore already distinct.
def default_voice(agent_name: str) -> VoiceProfile:
    """A distinct, unconfigured voice identity for an agent that declares none."""
    key = " ".join(str(agent_name or "").split()).lower()
    if not key:
        raise ValueError("a default voice needs an agent name to derive a key from")
    return VoiceProfile(key=key)


def voice_for(agent: Any) -> VoiceProfile | None:
    """The voice this agent publishes, if it publishes one.

    Duck-typed, in the same spirit as ``describe_interface`` and
    ``availability_of``: an agent that has a voice is asked for it, and one that
    does not returns None rather than being given somebody else's. Asking by
    attribute means this function never learns which agents exist.

    Returns None rather than a default on purpose. "No voice declared" and "a
    voice that happens to sound plain" are different facts, and the caller -
    which knows whether it is rendering a status line or about to speak - is
    the one that should decide what to do about the difference.
    """
    declared = getattr(agent, "voice", None)
    return declared if isinstance(declared, VoiceProfile) else None


# What an agent gets when its document says nothing about how it is
# addressed. Derived from the agent's own name, the same rule default_voice
# follows above and for the same reason: two agents that both declared
# nothing must still be distinguishable, so a shared default is never an
# option. Name-derived rather than a hardcoded phrase list is what makes
# this call produce a distinct, sensible default for any agent without this
# function learning a second name.
def default_activation(agent_name: str) -> VoiceActivation:
    """A distinct, name-derived activation identity for an agent that
    declares no ``## Voice Activation`` section of its own."""
    key = " ".join(str(agent_name or "").split()).lower()
    if not key:
        raise ValueError("a default activation needs an agent name to derive a key from")
    return VoiceActivation(key=key, phrases=(key, f"hey {key}"))


def activation_for(agent: Any) -> VoiceActivation | None:
    """The activation identity this agent publishes, if it publishes one.

    Duck-typed, exactly as ``voice_for`` above is: an agent that has an
    activation identity is asked for it, and one that does not returns None
    rather than being given somebody else's or a manufactured one. Asking by
    attribute means this function never learns which agents exist, so a
    future specialist becomes activatable the day it declares one - nothing
    here has to change first.
    """
    declared = getattr(agent, "activation", None)
    return declared if isinstance(declared, VoiceActivation) else None


def parse_voice(lines: list[str], key: str) -> VoiceProfile | None:
    """Read a ``## Voice`` section into a profile, or None when it says nothing.

    The section is ``trait: value`` lines. Unknown traits are collected into
    ``style`` rather than dropped, so a document may describe something this
    version does not model yet and a later provider can still find it - dropping
    it silently would make the document a place where writing has no effect,
    which is the defect this project already fixed twice.

    ``key`` is supplied by the caller and defaults to the agent's own name, so a
    document never has to repeat it and two agents can never collide on it by
    forgetting to.
    """
    known = {"accent", "pace", "pitch", "warmth", "preferred_voice"}
    traits: dict[str, str] = {}
    style: dict[str, Any] = {}
    declared_key = ""

    for line in lines:
        name, separator, value = line.partition(":")
        if not separator:
            continue
        name, value = name.strip().lower(), value.strip()
        if not value:
            continue
        if name == "key":
            declared_key = value
        elif name in known:
            traits[name] = value
        else:
            style[name] = value

    if not (traits or style or declared_key):
        return None

    return VoiceProfile(key=declared_key or key, style=style, **traits)
