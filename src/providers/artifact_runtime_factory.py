"""Turns a configured artifact-runtime *name* into a provider *instance* - the
identical small registry ``environment_factory.py``/``avatar_factory.py``
already are.

"browser" is what an unconfigured installation gets, for the same reason
``avatar_provider`` defaults to a real catalog rather than an empty placeholder:
the real browser is already on this machine, constructing the provider starts
nothing, and a page is only ever opened when someone actually asks for an
artifact to be run. An installation with no browser is not misreported - the
provider says so honestly at the moment of asking (see
``BrowserArtifactRuntime.run``), which is the answer a person needs and cannot
get from a runtime that was silently replaced by nothing at startup.
"""

from __future__ import annotations

from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.artifact_runtime import ArtifactRuntimeProvider
from src.providers.browser_artifact_runtime import BrowserArtifactRuntime
from src.providers.null_artifact_runtime import NullArtifactRuntime
from src.utils.logging import get_logger

logger = get_logger(__name__)

_BUILDERS: dict[str, Callable[[Settings], ArtifactRuntimeProvider]] = {
    "browser": lambda settings: BrowserArtifactRuntime(),
    "null": lambda settings: NullArtifactRuntime(),
}


def available_artifact_runtimes() -> list[str]:
    """Every artifact-runtime name that can currently be configured."""
    return sorted(_BUILDERS)


def build_artifact_runtime(
    settings: Settings, env: Mapping[str, str] | None = None
) -> ArtifactRuntimeProvider:
    """Construct the runtime named by ``settings.artifact_runtime``.

    Fails fast and loudly on an unknown name, the same rule every other factory
    in this project already follows.
    """
    try:
        builder = _BUILDERS[settings.artifact_runtime]
    except KeyError:
        raise ValueError(
            f"Unknown artifact runtime {settings.artifact_runtime!r}. "
            f"Available: {', '.join(available_artifact_runtimes())}"
        ) from None

    provider = builder(settings)
    logger.info("artifact runtime ready: %s (%s)", provider.name, provider.describes)
    return provider
