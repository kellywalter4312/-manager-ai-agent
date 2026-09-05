"""The visual-environment contract - the same seam ``AvatarProvider``
already is, applied to *where* an agent is presented rather than *what
it looks like*.

**Why this is not AvatarProfile.** An avatar answers "what does this
agent look like"; an environment answers a separate question, "what
does the space around it look like" - scene, lighting, background,
mood. Two independent questions, two independent providers, the same
guarantee this project already gives every other axis: changing one
can never silently change the other.

**Descriptive, never a rendering asset - identical discipline to
``AvatarProvider``, for the identical reason.** This project has no 3D
renderer today (see ARCHITECTURE.md's own hardware/scope finding), the
same honest starting point ``AvatarProvider``'s own docstring already
documents for avatars ("descriptive, never a rendering asset... a real
renderer arriving later is a provider change, never a rewrite of what
an avatar *is*"). Building a fake rendering pipeline just to have
"video generation provider swappability" also visible would be exactly
the sanctioned-fabrication this project's own standing rules forbid.
What genuinely exists, and is genuinely useful without any renderer at
all, is a real, swappable, persisted *description* of the intended
scene - the same real value ``DigitalHumanProfile``'s own open,
renderer-owned labels already provide for posture/clothing/expression.
A future renderer reads these labels; nothing here waits for one to
exist to be real.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class EnvironmentProfile:
    """One scene/space description an agent may be presented within -
    a characteristic set, never a shipped 3D asset, the identical shape
    ``AvatarProfile`` already uses for the identical reason: a renderer
    that supports none of these fields still works, which is what lets
    this abstraction survive a change of rendering technology.
    """

    environment_id: str
    label: str
    mood: str = ""
    lighting: str = ""
    style: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.environment_id).strip():
            raise ValueError("an EnvironmentProfile must have an environment_id")
        if not str(self.label).strip():
            raise ValueError("an EnvironmentProfile must have a label")
        for name in ("environment_id", "label", "mood", "lighting"):
            object.__setattr__(self, name, " ".join(str(getattr(self, name)).split()))
        object.__setattr__(self, "environment_id", self.environment_id.lower())
        object.__setattr__(self, "style", dict(self.style))

    def to_dict(self) -> dict[str, object]:
        """A plain, JSON-shaped payload - the boundary format a
        presentation layer (a snapshot, a future renderer) reads,
        mirroring ``AvatarProfile.to_dict()``'s own reasoning."""
        return {
            "environment_id": self.environment_id,
            "label": self.label,
            "mood": self.mood,
            "lighting": self.lighting,
            "style": dict(self.style),
        }

    def describe(self) -> str:
        return f"{self.label} ({self.environment_id})"


class EnvironmentProvider(ABC):
    """A source of environment profiles - the catalog a user chooses
    from, and the deterministic default an agent gets until they
    choose. Two narrow methods, the same shape every other provider in
    this project already proved cheap to implement and cheap to
    replace."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used in logs and provenance, e.g. "default"."""

    @abstractmethod
    def catalog(self) -> tuple[EnvironmentProfile, ...]:
        """Every environment profile a user may currently choose from.
        Never empty for a genuinely working provider - an empty catalog
        is what an honestly-unconfigured provider returns instead."""

    @abstractmethod
    def default_for(self, agent_key: str) -> EnvironmentProfile:
        """The environment an agent gets until a user chooses one -
        deterministic from ``agent_key`` alone, the identical
        no-randomness rule ``DefaultAvatarProvider.default_for``
        already keeps."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r}>"
