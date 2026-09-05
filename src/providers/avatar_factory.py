"""Turns a configured avatar provider *name* into a provider *instance*.

The identical small registry ``celebration_factory.py``/
``calendar_events_factory.py``/``connection_factory.py`` already are.
"default" is what an unconfigured installation gets - the same choice
``celebration_provider`` already makes, for the identical reason: a
deterministic, no-network catalog is genuinely working, not an empty
placeholder waiting for configuration.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.avatar import AvatarProvider
from src.providers.default_avatar_provider import DefaultAvatarProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings], AvatarProvider]] = {
    "default": lambda settings: DefaultAvatarProvider(),
}


def available_avatar_providers() -> list[str]:
    """Every avatar provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_avatar_provider(settings: Settings, env: Mapping[str, str] | None = None) -> AvatarProvider:
    """Construct the avatar provider named by ``settings.avatar_provider``.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.avatar_provider]
    except KeyError:
        raise ValueError(
            f"Unknown avatar provider {settings.avatar_provider!r}. "
            f"Available: {', '.join(available_avatar_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("avatar provider ready: %s", provider.name)
    return provider
