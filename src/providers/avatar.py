"""The avatar contract - Stage 17's own first seam: a presentation
identity an agent's presence can be represented by, distinct from every
other identity type this project already has.

**Why this is not VoiceProfile, Personality, or Identity.** Each of
those already answers a different question about who an agent is - how
it sounds, how it tends to communicate, its name and role. An avatar
answers a fourth, purely visual one: what the agent looks like when
present. Keeping it a separate type is what makes "change Alfred's
avatar" a fact that cannot possibly also change how he reasons, what he
remembers, or how he sounds - the same guarantee this project already
gives voice-versus-personality-versus-identity, extended one layer.

**Descriptive, never a rendering asset.** Exactly the same discipline
``VoiceProfile`` already holds for a voice: every field is a
characteristic a future renderer maps onto whatever it actually
supports, never a baked-in model path, texture, or vendor asset id. A
"visual direction/prototype" picture is not a permanent identity - the
catalog below is illustrative style *labels*, not shipped 3D assets,
so a real renderer arriving later is a provider change, never a
rewrite of what an avatar *is*.

**Why a provider, not a hardcoded value.** The identical reasoning
``CelebrationProvider``/``CalendarEventProvider``/``Connection`` already
proved: a small ABC plus a factory means a future avatar/rendering
technology is a new class and one factory entry, never a change to the
agent architecture that reads it.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class AvatarProfile:
    """One agent's visual presentation identity, described rather than
    rendered.

    Every field but ``avatar_id``/``label`` is a *characteristic* - the
    same shape ``VoiceProfile`` already uses for the identical reason: a
    renderer that supports none of them still works, which is what lets
    the abstraction survive a change of rendering technology.

    ``style`` is opaque, carried and never interpreted here - the same
    contract ``VoiceProfile.style``/``MemoryRecord.context`` already
    have. It is where a future renderer's own parameters (a model id, a
    texture set, a rig name) go without this type ever learning what
    they mean.
    """

    avatar_id: str
    label: str
    body_type: str = ""
    accent_color: str = ""
    style: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.avatar_id).strip():
            raise ValueError("an AvatarProfile must have an avatar_id")
        if not str(self.label).strip():
            raise ValueError("an AvatarProfile must have a label")
        for name in ("avatar_id", "label", "body_type", "accent_color"):
            object.__setattr__(self, name, " ".join(str(getattr(self, name)).split()))
        object.__setattr__(self, "avatar_id", self.avatar_id.lower())
        object.__setattr__(self, "style", dict(self.style))

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload - the boundary format a
        presentation layer (a snapshot, a future renderer) reads,
        mirroring ``CelebrationReaction.to_dict()``'s own reasoning."""
        return {
            "avatar_id": self.avatar_id,
            "label": self.label,
            "body_type": self.body_type,
            "accent_color": self.accent_color,
            "style": dict(self.style),
        }

    def describe(self) -> str:
        return f"{self.label} ({self.avatar_id})"


class AvatarProvider(ABC):
    """A source of avatar profiles - the catalog a user chooses from,
    and the deterministic default an agent gets until they choose.

    Two narrow methods, the same shape every other provider in this
    project already proved cheap to implement and cheap to replace.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "default"."""

    @abstractmethod
    def catalog(self) -> tuple[AvatarProfile, ...]:
        """Every avatar profile a user may currently choose from. Never
        empty for a genuinely working provider - an empty catalog is
        what an honestly-unconfigured provider returns instead."""

    @abstractmethod
    def default_for(self, agent_key: str) -> AvatarProfile:
        """The avatar an agent gets until a user chooses one -
        deterministic from ``agent_key`` alone (the same
        no-randomness rule ``DefaultCelebrationProvider`` already
        keeps), so two agents that never chose still resolve to
        different, stable avatars rather than colliding on one."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
