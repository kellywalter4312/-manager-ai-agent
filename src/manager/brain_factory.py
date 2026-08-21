"""Turns a configured brain *name* into a brain *instance*.

The same small-registry pattern as router_factory.py, providers/factory.py and
AgentRegistry. It is the single place where configuration, providers and
reasoning strategies meet, which is what keeps each of them ignorant of the
others.

This module is what completes the dependency chain:

    Settings -> build_provider -> LLMProvider -> LLMManagerBrain -> ManagerAgent

Read that left to right and notice what never happens: the Manager is at the
end of the chain, receiving a finished ManagerBrain. It never sees Settings, a
provider, a model name or a vendor. Adding a second reasoning backend later
means adding one entry to _BUILDERS, not editing main.py and not editing
anything the Manager imports.

The default is "null", matching the router's conservative default. Note that
"llm" is not the dangerous option people expect: LLMManagerBrain receives
whatever build_provider returned, so with the default null provider it produces
an honest UNKNOWN and still makes no network call. Turning on real reasoning
takes two deliberate settings, not one.
"""

from __future__ import annotations

from typing import Callable

from src.config.settings import Settings
from src.manager.brain import ManagerBrain, NullBrain
from src.manager.llm_brain import LLMManagerBrain
from src.providers.base import LLMProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Values are factory callables rather than instances, so a brain is only
# constructed when it is actually selected - the same reasoning as the provider
# registry, where an entry that needs a credential must not demand one merely
# by existing.
_BUILDERS: dict[str, Callable[[Settings, LLMProvider], ManagerBrain]] = {
    "null": lambda settings, provider: NullBrain(),
    "llm": lambda settings, provider: LLMManagerBrain(provider),
}


def available_brains() -> list[str]:
    """Every brain name that can currently be configured."""
    return sorted(_BUILDERS)


def build_brain(settings: Settings, provider: LLMProvider) -> ManagerBrain:
    """Construct the brain named by ``settings.brain``.

    Fails fast on an unknown name, listing the valid options - a typo in
    configuration should stop the program with a readable message rather than
    surface later as a Manager that mysteriously has no opinions.
    """
    try:
        builder = _BUILDERS[settings.brain]
    except KeyError:
        raise ValueError(
            f"Unknown brain {settings.brain!r}. "
            f"Available brains: {', '.join(available_brains())}"
        ) from None

    brain = builder(settings, provider)
    logger.info("brain ready: %s (%s)", settings.brain, type(brain).__name__)
    return brain
