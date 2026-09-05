"""The one real, generic ``EnvironmentProvider`` this project ships:
deterministic, no network, no vendor SDK, no rendering engine - the
identical role ``DefaultAvatarProvider`` already plays for appearance,
applied here to the space an agent is presented within.

A small, swappable catalog, never one hardcoded scene - nothing here
ties any one environment to any one agent name.
"""

from __future__ import annotations

import zlib

from src.providers.environment import EnvironmentProfile, EnvironmentProvider

_CATALOG: tuple[EnvironmentProfile, ...] = (
    EnvironmentProfile(
        environment_id="quiet_study",
        label="Quiet Study",
        mood="calm, focused",
        lighting="warm, low-key",
        style={"backdrop": "a book-lined study"},
    ),
    EnvironmentProfile(
        environment_id="open_workspace",
        label="Open Workspace",
        mood="alert, collaborative",
        lighting="bright, even",
        style={"backdrop": "an open, modern workspace"},
    ),
    EnvironmentProfile(
        environment_id="evening_lounge",
        label="Evening Lounge",
        mood="relaxed, informal",
        lighting="dim, amber",
        style={"backdrop": "a quiet lounge at dusk"},
    ),
    EnvironmentProfile(
        environment_id="technical_bay",
        label="Technical Bay",
        mood="precise, attentive",
        lighting="cool, directional",
        style={"backdrop": "a clean technical workbench"},
    ),
)


class DefaultEnvironmentProvider(EnvironmentProvider):
    @property
    def name(self) -> str:
        return "default"

    def catalog(self) -> tuple[EnvironmentProfile, ...]:
        return _CATALOG

    def default_for(self, agent_key: str) -> EnvironmentProfile:
        key = " ".join(str(agent_key or "").split()).lower()
        index = zlib.crc32(key.encode("utf-8")) % len(_CATALOG)
        return _CATALOG[index]
