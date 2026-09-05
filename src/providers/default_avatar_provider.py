"""The one real, generic ``AvatarProvider`` this project ships:
deterministic, no network, no vendor SDK, no rendering engine.

The same role ``DefaultCelebrationProvider`` already plays: a genuinely
working default, not a placeholder. Four style *directions* - never
one hardcoded, permanent Alfred appearance. "The pictured Alfred human
is a visual direction/prototype, not a permanently hard-coded identity"
(this stage's own brief) is exactly what a small, swappable catalog
plus a deterministic-not-fixed default makes true from the first
commit: nothing here ties any one avatar to any one agent name.

Stage 17.13 added the fourth entry, ``sharp_technical`` - a real,
reviewed addition (that stage's own explicit brief asked for a genuine
cyan accent option, which nothing in the first three directions
offered), the same open-ended extensibility this module's own docstring
already promised, not a special case for any one agent.

**Selection is a stable hash, never randomness** - the identical
argument ``DefaultCelebrationProvider``'s own docstring already makes
for reaction variety, applied here to which catalog entry an
unconfigured agent defaults to: reproducible, testable, and different
agents land on different defaults without a name ever being compared.
"""

from __future__ import annotations

import zlib

from src.providers.avatar import AvatarProfile, AvatarProvider

_CATALOG: tuple[AvatarProfile, ...] = (
    AvatarProfile(
        avatar_id="warm_formal",
        label="Warm & Formal",
        body_type="humanoid",
        accent_color="amber",
        style={"direction": "composed, measured presence"},
    ),
    AvatarProfile(
        avatar_id="calm_composed",
        label="Calm & Composed",
        body_type="humanoid",
        accent_color="teal",
        style={"direction": "quiet, steady presence"},
    ),
    AvatarProfile(
        avatar_id="bright_approachable",
        label="Bright & Approachable",
        body_type="humanoid",
        accent_color="coral",
        style={"direction": "light, energetic presence"},
    ),
    AvatarProfile(
        avatar_id="sharp_technical",
        label="Sharp & Technical",
        body_type="humanoid",
        accent_color="cyan",
        style={"direction": "precise, technical presence"},
    ),
)


class DefaultAvatarProvider(AvatarProvider):
    @property
    def name(self) -> str:
        return "default"

    def catalog(self) -> tuple[AvatarProfile, ...]:
        return _CATALOG

    def default_for(self, agent_key: str) -> AvatarProfile:
        key = " ".join(str(agent_key or "").split()).lower()
        index = zlib.crc32(key.encode("utf-8")) % len(_CATALOG)
        return _CATALOG[index]
