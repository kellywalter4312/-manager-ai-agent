"""Turns a configured provider *name* into a provider *instance*.

This is a small registry, the same pattern as AgentRegistry: a lookup table
mapping a string to something that builds the real thing. It exists so that
adding a backend later means adding one entry here, not editing main.py or
anything under src/manager/.

Note the direction of the dependency. Settings does not know which providers
exist, and providers do not read the environment. This module is the only
place the two meet, which keeps both sides independently testable.

Phase 1.5 added one more, optional, seam: an explicit ``ProviderAccount``
(``src/services/credentials/account.py``) may be passed in to name exactly
which credential (and, optionally, which model) this construction should
use - the genuine multi-account layer this project's credential
architecture needed, so a caller is never limited to "whatever the
environment currently says" for a given provider. Every existing caller
that never passes one gets exactly this module's prior behaviour,
unchanged: read the vendor's own environment variable, as always.
"""

from __future__ import annotations

import os
from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.base import LLMProvider
from src.providers.null_provider import NullProvider
from src.services.credentials.account import ProviderAccount
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The environment variable the Anthropic SDK reads for itself. Named here so
# the factory can tell whether a credential exists *without* importing the SDK
# to ask it - keeping the vendor package off the import path even while
# validating that using it would work.
ANTHROPIC_KEY_ENV = "ANTHROPIC_API_KEY"

# Phase 1.4: OpenAI and xAI each own their own credential variable, read
# directly from the environment - never from Settings.api_key, which is
# documented as anthropic-specific (see _anthropic_credential below and
# tests/test_brain_provider_credential_memory_independence.py::
# TestCredentialIsolation::test_settings_api_key_takes_precedence_over_the_
# environment_for_anthropic_only). The same pattern src/providers/
# speech_factory.py already uses for ELEVENLABS_API_KEY.
OPENAI_KEY_ENV = "OPENAI_API_KEY"
XAI_KEY_ENV = "XAI_API_KEY"


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider is selected but cannot be configured.

    Distinct from a provider *failing* at run time. A failure mid-run is an
    expected operational event that degrades to "no answer"; this is a
    misconfiguration, and the right moment to report it is startup, loudly,
    before anything has run.
    """


def _check_account(account: ProviderAccount | None, expected_provider: str) -> None:
    """An explicit account must belong to the provider actually selected.

    This is the one guard that makes "never silently reuse another
    provider's credential" (Phase 1.5) hold even when a caller passes an
    account explicitly rather than relying on the environment: an
    OpenAI account handed to the anthropic builder is refused outright,
    never quietly accepted as though it were an anthropic credential.
    """
    if account is not None and account.provider != expected_provider:
        raise ProviderConfigurationError(
            f"a '{account.provider}' account was supplied, but the "
            f"'{expected_provider}' provider was selected - an account "
            f"never authorises a different provider than the one it "
            f"names."
        )


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
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> LLMProvider:
    """Build the Claude provider, importing its module only when selected.

    The import lives inside the function on purpose: it keeps the vendor SDK
    off the import path for everyone using the default null provider, and lets
    the project run even where the ``anthropic`` package is not installed.

    The credential is checked *before* that import, and deliberately by reading
    the environment (or, when given, the explicit ``account``) rather than by
    asking the SDK. Two things follow: choosing this provider still costs no
    vendor import, and a missing key is reported here rather than surfacing
    an hour later as a brain that mysteriously has no opinions.

    That last point is the whole reason this check exists. LLMManagerBrain
    catches provider failures and degrades to "no view" - correct behaviour for
    a model that times out, and completely wrong as a way to learn that nobody
    ever configured a key. A misconfiguration must not be able to disguise
    itself as an absence of opinion.
    """
    _check_account(account, "anthropic")
    credential = account.credential if account is not None else _anthropic_credential(settings, env)
    if credential is None:
        raise ProviderConfigurationError(
            "The 'anthropic' provider is selected but no API key is configured. "
            f"Set {ANTHROPIC_KEY_ENV} in the environment, or MANAGER_API_KEY for "
            "this project specifically. To run without a model instead, set "
            "MANAGER_PROVIDER=null (the default)."
        )

    from src.providers.anthropic_provider import AnthropicProvider

    model = (account.model if account is not None and account.model else None) or settings.model
    return AnthropicProvider(model=model, api_key=credential)


def _openai_credential(env: Mapping[str, str] | None = None) -> str | None:
    """OpenAI's own credential, and only OpenAI's.

    No ``Settings.api_key`` fallback here on purpose - that field is
    anthropic's (see ``_anthropic_credential`` above), and a second provider
    quietly accepting it would be exactly the credential leakage Phase 1.4
    exists to rule out.
    """
    source = os.environ if env is None else env
    key = (source.get(OPENAI_KEY_ENV) or "").strip()
    return key or None


def _build_openai(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> LLMProvider:
    """Build the OpenAI provider. Same shape and same reasoning as
    ``_build_anthropic``: credential checked before the vendor SDK is ever
    imported, so a missing key is reported here rather than as a brain that
    mysteriously has no opinions.
    """
    _check_account(account, "openai")
    credential = account.credential if account is not None else _openai_credential(env)
    if credential is None:
        raise ProviderConfigurationError(
            "The 'openai' provider is selected but no API key is configured. "
            f"Set {OPENAI_KEY_ENV} in the environment. To run without a "
            "model instead, set MANAGER_PROVIDER=null (the default)."
        )

    from src.providers.openai_provider import OpenAIProvider

    model = (account.model if account is not None and account.model else None) or settings.openai_model
    return OpenAIProvider(model=model, api_key=credential)


def _xai_credential(env: Mapping[str, str] | None = None) -> str | None:
    """xAI's own credential, and only xAI's - never OpenAI's, even though
    both providers happen to speak through the same SDK package."""
    source = os.environ if env is None else env
    key = (source.get(XAI_KEY_ENV) or "").strip()
    return key or None


def _build_xai(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> LLMProvider:
    """Build the xAI provider. Same shape as ``_build_openai`` - the SDK
    package the two share internally is an implementation detail neither
    this factory nor the credential check treats as a reason to merge them.
    """
    _check_account(account, "xai")
    credential = account.credential if account is not None else _xai_credential(env)
    if credential is None:
        raise ProviderConfigurationError(
            "The 'xai' provider is selected but no API key is configured. "
            f"Set {XAI_KEY_ENV} in the environment. To run without a model "
            "instead, set MANAGER_PROVIDER=null (the default)."
        )

    from src.providers.xai_provider import XAIProvider

    model = (account.model if account is not None and account.model else None) or settings.xai_model
    return XAIProvider(model=model, api_key=credential)


def _build_local(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> LLMProvider:
    """Build the local model provider. No credential exists or is asked for;
    a missing model file is reported when the model is first used, the same
    honest, lazy boundary every other provider keeps."""
    from src.providers.local_llm_provider import LocalLLMProvider

    model_path = (account.model if account is not None and account.model else None) or settings.local_model_path
    return LocalLLMProvider(
        model_path=model_path,
        vision_model_path=settings.local_vision_model_path,
        vision_projector_path=settings.local_vision_projector_path,
    )


# Values are factory callables rather than instances, so a provider is only
# constructed when it is actually selected. An entry that needs a credential
# therefore does nothing - and demands nothing - unless chosen. Each takes
# (settings, env, account) - account defaulted to None so a builder written
# before Phase 1.5 (or a test's own inline lambda) keeps working unchanged.
_BUILDERS: dict[
    str, Callable[[Settings, Mapping[str, str] | None, "ProviderAccount | None"], LLMProvider]
] = {
    "null": lambda settings, env, account=None: NullProvider(),
    "anthropic": _build_anthropic,
    "openai": _build_openai,
    "xai": _build_xai,
    # A small model on this machine - no key, no network at inference time.
    "local": _build_local,
}


def available_providers() -> list[str]:
    """Every provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_provider(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> LLMProvider:
    """Construct the provider named by ``settings.provider``.

    Selection is by name and by name only. A provider is never chosen because
    a key happens to be present or an SDK happens to be installed - the default
    stays null on a machine with both, and reaching a model always takes a
    deliberate MANAGER_PROVIDER=anthropic.

    ``env`` is injectable for the same reason Settings.from_env's is: tests
    should not have to mutate os.environ to describe a configuration.

    ``account`` (Phase 1.5), when given, names exactly which credential
    (and, optionally, which model) to use, *and which provider to build* -
    ``account.provider`` decides the builder, not ``settings.provider``.
    This is deliberate: it is what lets one caller build an Anthropic
    provider for the Manager and an independently-authorised OpenAI
    provider for a specialist from the *same* ``Settings`` object,
    without ``settings.provider`` having to name either of them - see
    ``src/composition.py::build_runtime``'s own ``specialist_accounts``
    parameter, built on exactly this. Nothing about that is a silent
    substitution: an account only ever reaches the one builder matching
    its own declared provider (``_check_account`` inside each builder is
    the defence-in-depth backstop), so "which credential is used" and
    "which vendor it authorises" can never come apart. Omitted, this
    reproduces the prior, environment-based lookup exactly, keyed by
    ``settings.provider`` as it always was - every existing caller is
    unaffected.

    Fails fast and loudly on an unknown name or a missing credential. A
    misconfiguration should stop the program at startup with a readable
    message, not surface later as a mysterious absence of behaviour.
    """
    provider_name = account.provider if account is not None else settings.provider
    try:
        builder = _BUILDERS[provider_name]
    except KeyError:
        raise ValueError(
            f"Unknown provider {provider_name!r}. "
            f"Available providers: {', '.join(available_providers())}"
        ) from None

    provider = builder(settings, env, account)
    logger.info(
        "provider ready: %s%s",
        provider.name,
        f" (account={account.describe()})" if account is not None else "",
    )
    return provider
