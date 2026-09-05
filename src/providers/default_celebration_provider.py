"""The one real, generic ``CelebrationProvider`` this project ships:
deterministic, no network, no vendor SDK, no per-milestone-type branch.

The same role ``WikipediaLiveInfoProvider`` plays for current information
and ``NullProvider`` plays for language models: a genuinely working
default, not a placeholder. There is no "Null" variant of this one
because there is nothing to be null *about* - no key, no network, no
credential a celebration reaction could be missing; "off" is expressed
through ``intensity``, an ordinary input, not through swapping providers.

**Style comes from each agent's own declared Personality, never from its
name.** ``_energy_bias`` reads whatever free-text traits
``personality_for(agent)`` already returns - "energetic"/"casual" leaning
one way, "composed"/"formal" the other - the same words a document author
already chose to describe how this agent communicates. Nothing here
checks ``agent_key == "arthur"``; two differently-described agents
naturally resolve to different reactions, and a third agent that
declares nothing resolves to the same calm baseline every agent gets
until it says otherwise.

**Variation comes from a stable, reproducible hash, never randomness.**
``random``/``Math.random()``-style non-determinism would make a reaction
untestable and unreproducible between runs; a checksum of
(milestone_type, agent_key, occurrence) is deterministic, varies as any
of those three inputs vary, and needs no state remembered anywhere to
compute the same answer twice.
"""

from __future__ import annotations

import zlib
from typing import TYPE_CHECKING

from src.providers.celebration import CelebrationProvider, CelebrationReaction, INTENSITY_LEVELS

if TYPE_CHECKING:
    from src.services.identity.personality import AgentPersonality

# Generic presentation labels - what kind of thing happens, never *which*
# milestone it is for. Adding a new label here is a data change available
# to every milestone type and every agent immediately; there is no
# per-type list to keep in step.
_AVATAR_ACTIONS: tuple[str, ...] = ("nod", "smile", "raise_hand", "bow", "spin_lightly")
_ENVIRONMENT_ACTIONS: tuple[str, ...] = ("soft_pulse", "warm_glow", "gentle_sparkle", "bright_flash", "ripple")
_OVERLAYS: tuple[str, ...] = ("card", "banner", "badge")

# Free-text markers read from an agent's own declared Personality - never
# an agent name. Symmetric and small on purpose: this is a bias, not a
# psychology engine.
_ENERGETIC_MARKERS: tuple[str, ...] = ("energetic", "playful", "casual", "brisk", "lively", "bright")
_COMPOSED_MARKERS: tuple[str, ...] = ("composed", "formal", "measured", "reserved", "professional", "premium")

_BASE_DURATION_MS: dict[str, int] = {"subtle": 900, "full": 2400}
_MIN_DURATION_MS = 200


def _personality_text(personality: "AgentPersonality | None") -> str:
    if personality is None:
        return ""
    return " ".join(personality.traits).lower()


def _energy_bias(personality: "AgentPersonality | None") -> int:
    """A small, generic lean derived from an agent's own declared
    Personality traits. Positive leans toward a brighter, quicker
    reaction; negative toward a calmer, longer one; zero (an
    undeclared or neutral Personality) is the ordinary baseline."""
    text = _personality_text(personality)
    return sum(1 for m in _ENERGETIC_MARKERS if m in text) - sum(
        1 for m in _COMPOSED_MARKERS if m in text
    )


def _stable_index(*parts: str, modulo: int) -> int:
    """A deterministic, run-stable index from arbitrary text - never
    Python's own salted string hash, which varies between processes and
    would make selection unreproducible and untestable."""
    joined = "|".join(parts).encode("utf-8")
    return zlib.crc32(joined) % modulo


class DefaultCelebrationProvider(CelebrationProvider):
    @property
    def name(self) -> str:
        return "default"

    def select(
        self,
        *,
        milestone_type: str,
        agent_key: str,
        personality: "AgentPersonality | None",
        intensity: str,
        occurrence: int = 0,
    ) -> CelebrationReaction:
        resolved_intensity = intensity if intensity in INTENSITY_LEVELS else "subtle"

        if resolved_intensity == "off":
            return CelebrationReaction(
                milestone_type=milestone_type,
                agent_key=agent_key,
                reaction_id="none",
                intensity="off",
                enabled=False,
            )

        bias = _energy_bias(personality)
        lean = max(bias, 0)

        avatar_action = _AVATAR_ACTIONS[
            (_stable_index(milestone_type, agent_key, str(occurrence), "avatar", modulo=len(_AVATAR_ACTIONS)) + lean)
            % len(_AVATAR_ACTIONS)
        ]
        environment_action = _ENVIRONMENT_ACTIONS[
            (_stable_index(milestone_type, agent_key, str(occurrence), "env", modulo=len(_ENVIRONMENT_ACTIONS)) + lean)
            % len(_ENVIRONMENT_ACTIONS)
        ]

        overlay = ""
        duration = _BASE_DURATION_MS[resolved_intensity]
        if resolved_intensity == "full":
            overlay = _OVERLAYS[
                _stable_index(milestone_type, agent_key, str(occurrence), "overlay", modulo=len(_OVERLAYS))
            ]
            duration += bias * 150
        duration = max(duration, _MIN_DURATION_MS)

        reaction_id = f"{milestone_type}:{avatar_action}:{environment_action}"[:80]

        return CelebrationReaction(
            milestone_type=milestone_type,
            agent_key=agent_key,
            reaction_id=reaction_id,
            intensity=resolved_intensity,
            avatar_action=avatar_action,
            environment_action=environment_action,
            overlay=overlay,
            duration_ms=duration,
            enabled=True,
        )
