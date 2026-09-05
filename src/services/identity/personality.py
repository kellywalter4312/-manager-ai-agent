"""How an agent tends to communicate. Part of who it is, not a setting on
whatever happens to be generating its words.

    IDENTITY    who the agent is
    PERSONALITY how the agent tends to communicate    <- this module
    BRAIN       how deeply the agent can reason
    MEMORY      what the agent retains
    VOICE       how the agent sounds spoken aloud
    CAPABILITIES what the agent can do
    CONNECTIONS  external systems the agent can reach

Seven distinct dimensions of one canonical agent, and this module is
responsible for exactly one of them. It does not decide what an agent may
do (that is Capabilities), how deeply it reasons (Brain), what it retains
(Memory), or how it sounds spoken aloud (``VoiceProfile``, this package's
sibling module) - describing warmth and humor grants nothing, exactly as
describing a role grants nothing.

**Why this is not Voice.** ``VoiceProfile`` answers "how does this agent
sound" - accent, pace, pitch, the audio characteristics a speech provider
maps to a real timbre. This module answers a different question entirely:
"how does this agent tend to communicate" - tone, warmth, humor,
playfulness, whether it reaches for an emoji. An agent's chosen voice must
remain its voice regardless of personality, and its personality must
remain its personality regardless of which voice (or no voice at all,
text-only) carries it. Two facts, two types, kept separate for the same
reason ``VoiceActivation`` is kept separate from ``VoiceProfile``.

**A preference stated in words, never a rule this module enforces.** The
whole risk with "emoji support" is mechanical insertion - a filter that
tacks on 🙂 because a flag is set, whether or not it fits what was just
said. This module never touches a response. It produces a *description* of
a preference; something else - the model actually composing a reply -
is what has judgement about whether a given moment calls for warmth, a
joke, or neither. See ``personality_prompt_fragment`` below for the one
concrete way that description currently reaches such a thing.

**No agent is named here.** A future specialist gets its own personality by
declaring one, and nothing in this module learns it exists - the same rule
this whole package already follows for voices and activation identities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class AgentPersonality:
    """One agent's tendency of communication, described rather than
    configured.

    Every field but ``key`` is a *tendency*, stated in plain words - the
    sort of thing you would tell someone about to speak on this agent's
    behalf. Deliberately not a numeric slider: "occasional, dry" carries
    more of the actual intent than "humor: 0.4" ever would, and a handful
    of named tendencies is the whole model - this is not a psychology
    engine and does not grow one field at a time into being one.

    ``style`` is opaque, carried and never interpreted here - the same
    contract ``VoiceProfile.style`` and ``MemoryRecord.context`` already
    have. It is where a future, more specific characteristic goes without
    this type learning what it means.
    """

    key: str
    tone: str = ""
    warmth: str = ""
    formality: str = ""
    humor: str = ""
    playfulness: str = ""
    emoji_style: str = ""
    style: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.key).strip():
            raise ValueError("an AgentPersonality must have a key")
        for name in ("key", "tone", "warmth", "formality", "humor", "playfulness", "emoji_style"):
            object.__setattr__(self, name, " ".join(str(getattr(self, name)).split()))
        object.__setattr__(self, "key", self.key.lower())
        object.__setattr__(self, "style", dict(self.style))

    @property
    def traits(self) -> tuple[str, ...]:
        """The stated tendencies, in a stable order, blanks dropped.

        A personality that states nothing but a key is valid and means
        "this agent has its own personality, and no declared preference
        about how it communicates" - still enough to keep it distinct from
        an agent with a genuinely different one.
        """
        return tuple(
            value
            for value in (
                self.tone, self.warmth, self.formality,
                self.humor, self.playfulness, self.emoji_style,
            )
            if value
        )

    def describe(self) -> str:
        stated = ", ".join(self.traits) or "no stated tendencies"
        return f"{self.key} ({stated})"


# What an agent gets when its document says nothing about how it tends to
# communicate. Deliberately not a shared personality object - see
# default_voice's own reasoning, which applies verbatim: two agents that
# both declared nothing must still be distinguishable by key, and a shared
# default instance would blur that. Every tendency stays blank rather than
# invented, exactly as default_voice declares no accent/pace/pitch/warmth -
# "no personality declared" and "a personality that happens to be neutral"
# are different facts, and inventing the second to fill a gap would be
# exactly the fabrication this project refuses elsewhere.
def default_personality(agent_name: str) -> AgentPersonality:
    """An unconfigured, name-keyed personality for an agent that declares
    none of its own."""
    key = " ".join(str(agent_name or "").split()).lower()
    if not key:
        raise ValueError("a default personality needs an agent name to derive a key from")
    return AgentPersonality(key=key)


def personality_for(agent: Any) -> AgentPersonality | None:
    """The personality this agent publishes, if it publishes one.

    Duck-typed, exactly as ``voice_for``/``activation_for`` already are: an
    agent that has a personality is asked for it, and one that does not
    returns None rather than being given somebody else's or a manufactured
    one. Asking by attribute means this function never learns which agents
    exist.
    """
    declared = getattr(agent, "personality", None)
    return declared if isinstance(declared, AgentPersonality) else None


def parse_personality(lines: list[str], key: str) -> AgentPersonality | None:
    """Read a ``## Personality`` section into a personality, or None when
    it says nothing.

    The section is ``trait: value`` lines, read by the identical shape
    ``parse_voice`` already uses for exactly the same reason: two sections
    that look alike must not quietly grow two parsers. Unknown traits are
    collected into ``style`` rather than dropped, so a document may
    describe something this version does not model yet and a later reader
    can still find it.

    ``key`` is supplied by the caller and defaults to the agent's own name,
    so a document never has to repeat it and two agents can never collide
    on it by forgetting to.
    """
    known = {"tone", "warmth", "formality", "humor", "playfulness", "emoji_style"}
    traits: dict[str, str] = {}
    style: dict[str, Any] = {}
    declared_key = ""

    for line in lines:
        name, separator, value = line.partition(":")
        if not separator:
            continue
        name = name.strip()
        # A real trait line is a short key, never a sentence that happens to
        # contain a colon - "Formality shapes tone, not capability: nothing
        # here grants anything" would otherwise be misread as a trait named
        # after its own first clause, filed into `style` as if it were data.
        # A short, punctuation-free candidate is what a real "trait: value"
        # line always looks like; anything else is prose, and prose is
        # explanatory text this parser was never meant to capture.
        if len(name) > 24 or any(mark in name for mark in ".!?,;"):
            continue
        name, value = name.lower().replace(" ", "_"), value.strip()
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

    return AgentPersonality(key=declared_key or key, style=style, **traits)


def personality_prompt_fragment(personality: AgentPersonality | None) -> str:
    """Render a personality as natural-language instruction text - a
    preference *stated*, never a rule mechanically applied.

    This is the one place a personality's tendencies become words a model
    might actually read (see ``src/manager/llm_brain.py``'s optional use of
    this). It never decides that a given reply should be funny, warm, or
    carry an emoji - it describes a tendency and leaves the judgement
    about any single moment to whatever composes the reply, exactly as
    this module's own docstring promises.

    Deterministic: the same personality always renders to the same string.
    None, or a personality that declares nothing, renders to the empty
    string - appending nothing changes nothing about a base prompt.
    """
    if personality is None:
        return ""

    lines: list[str] = []
    if personality.tone:
        lines.append(f"- Tone: {personality.tone}.")
    if personality.warmth:
        lines.append(f"- Warmth: {personality.warmth}.")
    if personality.formality:
        lines.append(f"- Formality: {personality.formality}.")
    if personality.humor:
        lines.append(
            f"- Humor: {personality.humor}. Use judgement about whether a given "
            "moment calls for it - never at the expense of a serious request."
        )
    if personality.playfulness:
        lines.append(f"- Playfulness: {personality.playfulness}.")
    if personality.emoji_style:
        lines.append(
            f"- Emoji: {personality.emoji_style}. Use one only when it would "
            "feel natural - never mechanically, never in every reply."
        )

    if not lines:
        return ""

    return (
        "How you tend to communicate - part of who you are, expressing "
        "yourself, never overriding the person's request or the "
        "seriousness of what they asked:\n" + "\n".join(lines)
    )
