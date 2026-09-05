"""Turns a configured live-information provider *name* into a provider
*instance*.

The identical small registry ``src/providers/factory.py`` already is,
applied to current-information sources instead of language models: a
lookup table mapping a string to something that builds the real thing, so
adding a backend later means adding one entry here, not editing
composition.py or anything under ``src/manager/``.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.live_info import LiveInfoProvider
from src.providers.null_live_info_provider import NullLiveInfoProvider
from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Values are factory callables rather than instances, so a provider is only
# constructed when it is actually selected - the same reasoning
# src/providers/factory.py's own _BUILDERS already states.
_BUILDERS: dict[str, Callable[[Settings], LiveInfoProvider]] = {
    "null": lambda settings: NullLiveInfoProvider(),
    "wikipedia": lambda settings: WikipediaLiveInfoProvider(),
}


def available_live_info_providers() -> list[str]:
    """Every live-information provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_live_info_provider(
    settings: Settings, env: Mapping[str, str] | None = None
) -> LiveInfoProvider:
    """Construct the live-information provider named by
    ``settings.live_info_provider``.

    ``env`` is accepted, unused, for signature symmetry with
    ``build_provider`` - no builder here currently reads a credential, so
    there is nothing to inject, but a future one that needed a key would
    read it the same way ``_build_anthropic`` already does.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.live_info_provider]
    except KeyError:
        raise ValueError(
            f"Unknown live-information provider {settings.live_info_provider!r}. "
            f"Available: {', '.join(available_live_info_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("live-information provider ready: %s", provider.name)
    return provider
