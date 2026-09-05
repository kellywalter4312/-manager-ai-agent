"""Turns a configured speech provider *name* into a speech provider *instance*.

The same pattern as ``src/providers/factory.py`` (which does exactly this for
``LLMProvider``), applied to speech: a small registry mapping a string to
something that builds the real thing, so adding an engine later means adding
one entry here, not editing ``composition.py`` or anything under
``src/channels/`` or ``src/manager/``.

Before this module, ``composition.py`` constructed ``NoSpeech()`` directly -
correct while there was exactly one adapter, and its own comment said so
plainly: "No factory and no setting, because there is exactly one adapter and
a registry of one is ceremony... A second adapter turns this line into a
factory call and nothing above it changes." This module is that factory call,
existing the moment a second adapter's *shape* needs somewhere to be selected
from - not because a second adapter is shipped here.

**A missing credential is a configuration error, never a silent absence of
opinion.** ``SpeechConfigurationError`` exists so that fact is reported at
selection time rather than disguising itself as a speaker that mysteriously
never produces audio three layers downstream - the same discipline
``ProviderConfigurationError`` already holds LLM selection to. ``windows``
(see ``src/providers/windows_speech_provider.py``) needed no such check
beyond a platform guard: it reaches a real, local, already-installed engine,
no key, no network. ``elevenlabs`` (see
``src/providers/elevenlabs_speech_provider.py``) is the first entry here
that does need one, and ``_elevenlabs_credential`` is checked before that
module is even imported, so choosing it without a key fails immediately and
plainly rather than as a mute agent nobody can explain.

``none`` (``NoSpeech``) remains the default: choosing real speech is still
a deliberate ``MANAGER_SPEECH=windows``, ``MANAGER_SPEECH=elevenlabs`` or
``MANAGER_SPEECH=kokoro``, never something a machine happening to be
Windows, a key happening to be present, or a model file happening to sit
on disk, opts a deployment into by itself - the same "selection is by
name, never inferred" rule ``build_provider`` already lives under.
"""

from __future__ import annotations

import os
import platform
from typing import Callable, Mapping

from src.channels.speech import NoSpeech, SpeechAdapter
from src.config.settings import Settings
from src.services.credentials.account import ProviderAccount
from src.utils.logging import get_logger

logger = get_logger(__name__)

# The environment variable the ElevenLabs SDK reads for itself, named here for
# the same reason ANTHROPIC_KEY_ENV is named in src/providers/factory.py: it
# lets this factory tell whether a credential exists without importing the
# vendor SDK just to ask.
ELEVENLABS_KEY_ENV = "ELEVENLABS_API_KEY"


class SpeechConfigurationError(RuntimeError):
    """Raised when a speech provider is selected but cannot be configured.

    The speech-side sibling of ``src.providers.factory.ProviderConfigurationError``
    - a misconfiguration, reported at startup, distinct from an engine that
    fails or reports UNAVAILABLE at run time. A missing credential must not
    surface later as a microphone that mysteriously never works.
    """


def _build_windows(
    settings: Settings, env: Mapping[str, str] | None, account: "ProviderAccount | None" = None
) -> SpeechAdapter:
    """Build the Windows System.Speech adapter, importing its module only
    when selected - keeps ``subprocess`` and the engine's own module off the
    import path for every deployment using the default ``none``.

    Checked *before* that import: a structural, deterministic fact
    (``platform.system()``), never availability - availability is evidence
    from an actual call, and this project already learned once
    (``src/providers/factory.py``'s own ``_anthropic_credential``) that a
    fast, honest configuration error beats a mysterious runtime one.
    """
    if platform.system() != "Windows":
        raise SpeechConfigurationError(
            "The 'windows' speech provider is selected but this is not a "
            f"Windows machine (platform.system() == {platform.system()!r}). "
            "This engine reaches System.Speech via powershell.exe, which only "
            "exists on Windows. To run without a speech engine instead, set "
            "MANAGER_SPEECH=none (the default)."
        )

    from src.providers.windows_speech_provider import WindowsSpeechAdapter

    return WindowsSpeechAdapter()


def _build_kokoro(
    settings: Settings, env: Mapping[str, str] | None, account: "ProviderAccount | None" = None
) -> SpeechAdapter:
    """Build the Kokoro local TTS adapter, importing its module - and the
    ``kokoro_onnx`` package it imports - only when selected, for the same
    reason ``_build_windows`` and ``_build_elevenlabs`` defer their own
    imports.

    No credential to check: Kokoro is a local model file, not an account,
    so there is nothing here that fails the way a missing API key does.
    """
    from src.providers.kokoro_speech_provider import KokoroSpeechAdapter

    return KokoroSpeechAdapter()


def _elevenlabs_credential(env: Mapping[str, str] | None) -> str | None:
    """Whether an ElevenLabs key is reachable. The same shape as
    ``src.providers.factory._anthropic_credential``, minus the project's own
    override variable: nothing in ``Settings`` reserves a field for this
    vendor's key, because ``api_key`` already belongs to the LLM provider and
    a second vendor needing a second secret does not get to share it."""
    source = os.environ if env is None else env
    key = (source.get(ELEVENLABS_KEY_ENV) or "").strip()
    return key or None


def _build_elevenlabs(
    settings: Settings, env: Mapping[str, str] | None, account: "ProviderAccount | None" = None
) -> SpeechAdapter:
    """Build the ElevenLabs cloud TTS adapter, importing its module - and the
    vendor SDK it imports - only when selected, for the same reason
    ``_build_windows`` defers its own import.

    The credential is checked first, exactly as ``_build_anthropic`` checks
    its own: a missing key is a configuration error reported here, never a
    speaker that mysteriously never produces audio.

    ``account`` (Universal Ecosystem checkpoint) is the identical seam
    ``src/providers/factory.py::build_provider`` and
    ``src/providers/media/factory.py::build_media`` already give the
    Brain's and video-generation's own credentials, applied here so
    voice becomes a genuine account-managed axis too rather than the
    one remaining provider still limited to "whatever the environment
    currently says". Every existing caller that never passes one gets
    exactly this module's prior, environment-based behaviour, unchanged.
    """
    if account is not None and account.provider != "elevenlabs":
        raise SpeechConfigurationError(
            f"a '{account.provider}' account was supplied, but the "
            "'elevenlabs' speech provider was selected - an account never "
            "authorises a different provider than the one it names."
        )
    key = account.credential if account is not None else _elevenlabs_credential(env)
    if key is None:
        raise SpeechConfigurationError(
            "The 'elevenlabs' speech provider is selected but no API key is "
            f"configured. Set {ELEVENLABS_KEY_ENV} in the environment. To run "
            "without this provider instead, set MANAGER_SPEECH=none (the "
            "default) or MANAGER_SPEECH=windows."
        )

    from src.providers.elevenlabs_speech_provider import ElevenLabsSpeechAdapter

    return ElevenLabsSpeechAdapter(api_key=key)


# Values are factory callables rather than instances, so a provider is only
# constructed when it is actually selected - the same reason
# src.providers.factory keeps LLM builders lazy. Each takes
# (settings, env, account) - account defaulted to None so a builder that
# ignores it (every non-credentialed engine) keeps working unchanged.
_BUILDERS: dict[
    str, Callable[[Settings, Mapping[str, str] | None, "ProviderAccount | None"], SpeechAdapter]
] = {
    "none": lambda settings, env, account=None: NoSpeech(),
    "windows": _build_windows,
    "elevenlabs": _build_elevenlabs,
    "kokoro": _build_kokoro,
}


def available_speech_providers() -> list[str]:
    """Every speech provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_speech(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: "ProviderAccount | None" = None,
) -> SpeechAdapter:
    """Construct the speech adapter named by ``settings.speech``.

    Selection is by name and by name only - never inferred from whether a
    library happens to be importable or a key happens to be present, the same
    rule ``build_provider`` already lives under. ``env`` is injectable for the
    same reason ``Settings.from_env``'s is: a test should not have to mutate
    ``os.environ`` to describe a configuration.

    ``account`` (Universal Ecosystem checkpoint), when given, names exactly
    which credential this construction should use for a credentialed engine
    (today, only ``elevenlabs``) - the identical optional, additive seam
    ``build_provider``/``build_media`` already accept. Omitted, every
    existing caller reproduces this function's prior behaviour exactly.

    Fails fast and loudly on an unknown name, exactly as ``build_provider``
    does - a misconfiguration should stop the program at startup with a
    readable message, not surface later as a channel that silently never
    listens.
    """
    try:
        builder = _BUILDERS[settings.speech]
    except KeyError:
        raise ValueError(
            f"Unknown speech provider {settings.speech!r}. "
            f"Available speech providers: {', '.join(available_speech_providers())}"
        ) from None

    speech = builder(settings, env, account)
    logger.info("speech provider ready: %s", speech.name)
    return speech
