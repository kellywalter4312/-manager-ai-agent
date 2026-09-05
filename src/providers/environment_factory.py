"""Turns a configured environment provider *name* into a provider
*instance*. The identical small registry ``avatar_factory.py``/
``celebration_factory.py`` already are. "default" is what an
unconfigured installation gets, for the identical reason
``avatar_provider`` already makes that choice: a deterministic,
no-network catalog is genuinely working, not an empty placeholder
waiting for configuration.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.default_environment_provider import DefaultEnvironmentProvider
from src.providers.environment import EnvironmentProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings], EnvironmentProvider]] = {
    "default": lambda settings: DefaultEnvironmentProvider(),
}


def available_environment_providers() -> list[str]:
    """Every environment provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_environment_provider(settings: Settings, env: Mapping[str, str] | None = None) -> EnvironmentProvider:
    """Construct the environment provider named by
    ``settings.environment_provider``.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.environment_provider]
    except KeyError:
        raise ValueError(
            f"Unknown environment provider {settings.environment_provider!r}. "
            f"Available: {', '.join(available_environment_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("environment provider ready: %s", provider.name)
    return provider
