"""Turns a configured communication provider *name* into a provider
*instance*.

The identical small registry ``src/providers/live_info_factory.py``
already is, applied to phone/messaging connections instead of current-
information sources: a lookup table mapping a string to something that
builds the real thing, so adding a backend later means adding one entry
here, not editing composition.py or anything under ``src/manager/``.

Only one builder ships today - "null", the honest default. No real
external communication provider is wired in, per this stage's own
explicit instruction not to add a dependency or an external service just
to demonstrate the concept; the factory exists so a real one, when it is
deliberately added, is a data change here and a real implementation
module, never a rewrite of anything that calls this function.

**Universal provider/connection management repair.** This module
previously accepted no ``account`` at all - the one real asymmetry
CHECK found between this factory and its siblings
(``build_provider``/``build_media``/``build_speech``, every one of
which already accepts an optional ``ProviderAccount``). Closed here by
mirroring ``build_speech`` exactly: each builder now takes
``(settings, env, account)``, ``"null"`` ignores all three (nothing to
authenticate), and a future credentialed backend (WhatsApp, Telegram,
...) reads ``account.credential`` the identical way
``_build_elevenlabs`` already does - never a second credential
mechanism, never a per-service special case in this file's own
dispatch.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.communication import CommunicationProvider
from src.providers.null_communication_provider import NullCommunicationProvider
from src.services.credentials.account import ProviderAccount
from src.utils.logging import get_logger

logger = get_logger(__name__)

# Each builder takes (settings, env, account) - account defaulted to None
# so a builder that ignores it (today, every builder: "null" needs no
# credential) keeps working unchanged. The identical shape
# src/providers/speech_factory.py already uses for the same reason.
_BUILDERS: dict[
    str, Callable[[Settings, "Mapping[str, str] | None", "ProviderAccount | None"], CommunicationProvider]
] = {
    "null": lambda settings, env, account=None: NullCommunicationProvider(),
}


def available_communication_providers() -> list[str]:
    """Every communication provider name that can currently be configured."""
    return sorted(_BUILDERS)


def build_communication_provider(
    settings: Settings,
    env: "Mapping[str, str] | None" = None,
    account: "ProviderAccount | None" = None,
) -> CommunicationProvider:
    """Construct the communication provider named by
    ``settings.communication_provider``.

    ``env`` is accepted for signature symmetry with
    ``build_live_info_provider``/``build_speech``. ``account``, when
    given, names exactly which credential this construction should use
    for a credentialed backend - the identical optional, additive seam
    ``build_provider``/``build_media``/``build_speech`` already accept.
    No builder here needs one today (only "null" is registered), but a
    future one that does reads it exactly as ``_build_elevenlabs``
    already does. Omitted, every existing caller reproduces this
    function's prior behaviour exactly.

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.communication_provider]
    except KeyError:
        raise ValueError(
            f"Unknown communication provider {settings.communication_provider!r}. "
            f"Available: {', '.join(available_communication_providers())}"
        ) from None

    provider = builder(settings, env, account)
    logger.info("communication provider ready: %s", provider.name)
    return provider
