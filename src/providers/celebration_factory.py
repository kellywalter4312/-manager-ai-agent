"""Turns a configured celebration provider *name* into a provider
*instance*.

The identical small registry ``src/providers/live_info_factory.py`` and
``src/providers/communication_factory.py`` already are, applied to
presentation reactions instead of information or connections: a lookup
table mapping a string to something that builds the real thing, so a
future celebration implementation is a data change here, never a rewrite
of ``composition.py`` or anything under ``src/manager/``.

Only one builder ships today - "default", ``DefaultCelebrationProvider``.
Unlike ``live_info``/``communication``, this is not an honestly-empty
placeholder waiting for a real backend: ``DefaultCelebrationProvider`` is
already the one, genuinely working implementation this project ships (see
that module's own docstring for why no "null" variant exists - there is
nothing to be null *about*). This factory exists purely to close a
consistency gap a Stage 12 audit found: every sibling provider
(``LLMProvider``, ``LiveInfoProvider``, ``CommunicationProvider``) is
selected by name from ``Settings``, and celebration was the one that
was not, despite being just as replaceable.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.celebration import CelebrationProvider
from src.providers.default_celebration_provider import DefaultCelebrationProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings], CelebrationProvider]] = {
    "default": lambda settings: DefaultCelebrationProvider(),
}


def available_celebration_providers() -> list[str]:
    """Every celebration provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_celebration_provider(
    settings: Settings, env: Mapping[str, str] | None = None
) -> CelebrationProvider:
    """Construct the celebration provider named by
    ``settings.celebration_provider``.

    ``env`` is accepted, unused, for signature symmetry with
    ``build_live_info_provider``/``build_communication_provider`` - no
    builder here currently reads a credential, so there is nothing to
    inject.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.celebration_provider]
    except KeyError:
        raise ValueError(
            f"Unknown celebration provider {settings.celebration_provider!r}. "
            f"Available: {', '.join(available_celebration_providers())}"
        ) from None

    provider = builder(settings)
    logger.info("celebration provider ready: %s", provider.name)
    return provider
