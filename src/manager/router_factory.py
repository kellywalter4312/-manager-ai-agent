"""Turns a configured router *name* into a router *instance*.

The same small-registry pattern as providers/factory.py and AgentRegistry. It
is the single place where configuration, providers and routing strategies meet,
which is what keeps each of them ignorant of the others.

Note that "llm" does not build a bare LLMRouter. It builds an LLMRouter wrapped
in a FallbackRouter, because an LLM router alone would leave the system unable
to route whenever a model is unavailable - which, with the default provider, is
always. Assembling that pairing here means no caller has to remember it.
"""

from __future__ import annotations

from typing import Callable

from src.config.settings import Settings
from src.manager.fallback_router import FallbackRouter
from src.manager.llm_router import LLMRouter
from src.manager.router import CapabilityRouter, Router
from src.providers.base import LLMProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings, LLMProvider], Router]] = {
    "capability": lambda settings, provider: CapabilityRouter(),
    "llm": lambda settings, provider: FallbackRouter(
        primary=LLMRouter(provider),
        fallback=CapabilityRouter(),
    ),
}


def available_routers() -> list[str]:
    """Every router name that can currently be configured."""
    return sorted(_BUILDERS)


def build_router(settings: Settings, provider: LLMProvider) -> Router:
    """Construct the router named by ``settings.router``.

    Fails fast on an unknown name, listing the valid options - a typo in
    configuration should stop the program with a readable message.
    """
    try:
        builder = _BUILDERS[settings.router]
    except KeyError:
        raise ValueError(
            f"Unknown router {settings.router!r}. "
            f"Available routers: {', '.join(available_routers())}"
        ) from None

    router = builder(settings, provider)
    logger.info("router ready: %s (%s)", settings.router, type(router).__name__)
    return router
