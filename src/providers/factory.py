"""Turns a configured provider *name* into a provider *instance*.

This is a small registry, the same pattern as AgentRegistry: a lookup table
mapping a string to something that builds the real thing. It exists so that
adding a backend later means adding one entry here, not editing main.py or
anything under src/manager/.

Note the direction of the dependency. Settings does not know which providers
exist, and providers do not read the environment. This module is the only
place the two meet, which keeps both sides independently testable.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.base import LLMProvider
from src.providers.null_provider import NullProvider
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The environment variable the Anthropic SDK reads for itself. Named here so
# the factory can tell whether a credential exists *without* importing the SDK
# to ask it - keeping the vendor package off the import path even while
# validating that using it would work.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider is selected but cannot be configured.

    Distinct from a provider *failing* at run time. A failure mid-run is an
    expected operational event that degrades to "no answer"; this is a
    misconfiguration, and the right moment to report it is startup, loudly,
    before anything has run.
    """


def _anthropic_credential(
    settings: Settings, env: Mapping[str, str] | None = None
) -> str | None:
    """Whether a key is reachable, and from where. Never returns the key's value
    to a caller that logs - only its presence matters to this module.

    Two sources, because there are two reasonable places to have put one:

        MANAGER_API_KEY    this project's own configuration
        ANTHROPIC_API_KEY  what the SDK would find by itself

    Checking both means a user who configured the standard variable is not told
    their key is missing when it plainly is not.
    """
    if settings.api_key:
        return settings.api_key

    source = os.environ if env is None else env
    key = (source.get(ANTHROPIC_KEY_ENV) or "").strip()
    return key or None


def _build_anthropic(
    settings: Settings, env: Mapping[str, str] | None = None
) -> LLMProvider:
    """Build the Claude provider, importing its module only when selected.

    The import lives inside the function on purpose: it keeps the vendor SDK
    off the import path for everyone using the default null provider, and lets
    the project run even where the ``anthropic`` package is not installed.

    The credential is checked *before* that import, and deliberately by reading
    the environment rather than by asking the SDK. Two things follow: choosing
    this provider still costs no vendor import, and a missing key is reported
    here rather than surfacing an hour later as a brain that mysteriously has
    no opinions.

    That last point is the whole reason this check exists. LLMManagerBrain
    catches provider failures and degrades to "no view" - correct behaviour for
    a model that times out, and completely wrong as a way to learn that nobody
    ever configured a key. A misconfiguration must not be able to disguise
    itself as an absence of opinion.
    """
    if _anthropic_credential(settings, env) is None:
        raise ProviderConfigurationError(
            "The 'anthropic' provider is selected but no API key is configured. "
            f"Set {ANTHROPIC_KEY_ENV} in the environment, or MANAGER_API_KEY for "
            "this project specifically. To run without a model instead, set "
            "MANAGER_PROVIDER=null (the default)."
        )

    from src.providers.anthropic_provider import AnthropicProvider

    return AnthropicProvider(model=settings.model, api_key=settings.api_key)


# Values are factory callables rather than instances, so a provider is only
# constructed when it is actually selected. An entry that needs a credential
# therefore does nothing - and demands nothing - unless chosen.
_BUILDERS: dict[str, Callable[[Settings, Mapping[str, str] | None], LLMProvider]] = {
    "null": lambda settings, env: NullProvider(),
    "anthropic": _build_anthropic,
}


def available_providers() -> list[str]:
    """Every provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_provider(
    settings: Settings, env: Mapping[str, str] | None = None
) -> LLMProvider:
    """Construct the provider named by ``settings.provider``.

    Selection is by name and by name only. A provider is never chosen because
    a key happens to be present or an SDK happens to be installed - the default
    stays null on a machine with both, and reaching a model always takes a
    deliberate MANAGER_PROVIDER=anthropic.

    ``env`` is injectable for the same reason Settings.from_env's is: tests
    should not have to mutate os.environ to describe a configuration.

    Fails fast and loudly on an unknown name or a missing credential. A
    misconfiguration should stop the program at startup with a readable
    message, not surface later as a mysterious absence of behaviour.
    """
    try:
        builder = _BUILDERS[settings.provider]
    except KeyError:
        raise ValueError(
            f"Unknown provider {settings.provider!r}. "
            f"Available providers: {', '.join(available_providers())}"
        ) from None

    provider = builder(settings, env)
    logger.info("provider ready: %s", provider.name)
    return provider
