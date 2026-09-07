"""Turns configured media-backend *names* into real provider instances.

The identical small-registry shape ``src/providers/factory.py`` and
``src/providers/live_info_factory.py`` already are, applied here to two
independent axes: which narration/image/composition trio backs
``media_production`` (``settings.media``), and, separately, which real
AI video-*generation* vendor it may also reach for
(``settings.video_generation``) - two switches, not one, for the
identical reason ``provider`` and ``specialist_brain`` already are:
a deployment may want one without the other.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Mapping

from src.config.settings import Settings
from src.providers.factory import ProviderConfigurationError
from src.providers.media.image import (
    ImageProvider,
    LocalTitleCardImageProvider,
    NullImageProvider,
    PollinationsImageProvider,
)
from src.providers.media.narration import KokoroNarrationProvider, NarrationProvider, NullNarrationProvider
from src.providers.media.video import FFmpegVideoComposer, NullVideoComposer, VideoComposer
from src.providers.media.video_clipping import FFmpegVideoClipper, NullVideoClipper, VideoClipper
from src.providers.media.video_generation import (
    GoogleVeoVideoProvider,
    NullVideoGenerationProvider,
    RunwayVideoProvider,
    VideoGenerationProvider,
)
from src.services.credentials.account import ProviderAccount
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_KOKORO_MODEL_PATH = "data/voices/kokoro/kokoro-v1.0.int8.onnx"
DEFAULT_KOKORO_VOICES_PATH = "data/voices/kokoro/voices.npz"

# Reads its own environment variable directly, never Settings.api_key -
# the identical isolation _anthropic_credential/_openai_credential/
# _xai_credential already hold each other to in
# src/providers/factory.py: a video-generation vendor's credential is
# never interchangeable with an LLM vendor's, even when, as here, both
# happen to be read by the same project.
RUNWAY_KEY_ENV = "RUNWAY_API_KEY"

# Google Veo's own, dedicated credential - never shared with a future
# "gemini" LLM provider's own key, even though both would happen to be
# issued by the same vendor: the identical per-provider isolation
# RUNWAY_KEY_ENV already holds itself to, applied to a second video
# vendor rather than assumed to be unnecessary because there is only
# one video vendor yet.
VEO_KEY_ENV = "VEO_API_KEY"


@dataclass(frozen=True)
class MediaTools:
    """The four real, independently-replaceable media collaborators -
    never one combined "video provider": Alfred's Brain may use any LLM
    vendor, narration may use Kokoro or a future vendor, images may use
    a local renderer or a real AI backend, video *generation* may use a
    real credentialed vendor or none, and composition is a local tool -
    each swappable without the others changing, the same independence
    this project already holds Brain/Memory/Voice to.
    """

    narrator: NarrationProvider
    illustrator: ImageProvider
    composer: VideoComposer
    video_generator: VideoGenerationProvider
    # Video *clipping* checkpoint: local, deterministic extraction of a
    # real clip from an already-existing video - never vendor-backed
    # like video_generator, so it has no credential axis of its own and
    # is wired the identical way `composer` already is (see
    # video_clipping.py's own docstring for why no provider-selection
    # axis is warranted here). Defaulted so every construction that
    # predates this field - none currently exist outside this factory -
    # would still get the honest NullVideoClipper() every other Null*
    # collaborator here already is.
    clipper: VideoClipper = field(default_factory=NullVideoClipper)

    @property
    def name(self) -> str:
        return (
            f"{self.narrator.name}+{self.illustrator.name}+"
            f"{self.composer.name}+{self.video_generator.name}+{self.clipper.name}"
        )


def _runway_credential(env: Mapping[str, str] | None = None) -> str | None:
    """Runway's own credential, and only Runway's."""
    source = os.environ if env is None else env
    key = (source.get(RUNWAY_KEY_ENV) or "").strip()
    return key or None


def _veo_credential(env: Mapping[str, str] | None = None) -> str | None:
    """Veo's own credential, and only Veo's - never Runway's, even
    though both are read by this same factory function."""
    source = os.environ if env is None else env
    key = (source.get(VEO_KEY_ENV) or "").strip()
    return key or None


def _build_video_generation(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> VideoGenerationProvider:
    """Construct the AI video-generation provider named by ``account``
    (when given - its own declared provider decides, the identical
    "account picks the vendor" rule ``src/providers/factory.py::
    build_provider`` already establishes for the Brain's own providers)
    or, otherwise, ``settings.video_generation``.

    Fails fast on a missing credential, before any vendor SDK or network
    call - the identical discipline every LLM provider builder already
    holds itself to, and for the identical reason: a misconfiguration
    must not be able to disguise itself as an absence of opinion.
    """
    provider_name = account.provider if account is not None else settings.video_generation

    if provider_name == "null":
        return NullVideoGenerationProvider()

    if provider_name == "runway":
        credential = account.credential if account is not None else _runway_credential(env)
        if credential is None:
            raise ProviderConfigurationError(
                "The 'runway' video-generation provider is selected but no "
                f"API key is configured. Set {RUNWAY_KEY_ENV} in the "
                "environment. To run without one instead, set "
                "MANAGER_VIDEO_GENERATION=null (the default)."
            )
        model = (account.model if account is not None and account.model else None) or settings.runway_model
        return RunwayVideoProvider(api_key=credential, model=model)

    if provider_name == "veo":
        credential = account.credential if account is not None else _veo_credential(env)
        if credential is None:
            raise ProviderConfigurationError(
                "The 'veo' video-generation provider is selected but no "
                f"API key is configured. Set {VEO_KEY_ENV} in the "
                "environment. To run without one instead, set "
                "MANAGER_VIDEO_GENERATION=null (the default)."
            )
        model = (account.model if account is not None and account.model else None) or settings.veo_model
        return GoogleVeoVideoProvider(api_key=credential, model=model)

    raise ValueError(
        f"Unknown video-generation provider {provider_name!r}. "
        f"Available: {', '.join(available_video_generation_backends())}"
    )


def available_video_generation_backends() -> list[str]:
    """Every AI video-generation backend name that can currently be
    configured. Two real, credentialed vendors plus "null" - never one,
    which is the concrete evidence this project's own provider boundary
    is genuinely neutral rather than shaped around whichever vendor was
    implemented first."""
    return ["null", "runway", "veo"]


def _build_null(settings: Settings, video_generator: VideoGenerationProvider) -> MediaTools:
    return MediaTools(
        narrator=NullNarrationProvider(),
        illustrator=NullImageProvider(),
        composer=NullVideoComposer(),
        video_generator=video_generator,
        clipper=NullVideoClipper(),
    )


def _build_local(settings: Settings, video_generator: VideoGenerationProvider) -> MediaTools:
    return MediaTools(
        narrator=KokoroNarrationProvider(DEFAULT_KOKORO_MODEL_PATH, DEFAULT_KOKORO_VOICES_PATH),
        illustrator=LocalTitleCardImageProvider(),
        composer=FFmpegVideoComposer(),
        video_generator=video_generator,
        clipper=FFmpegVideoClipper(),
    )


def _build_ai_image(settings: Settings, video_generator: VideoGenerationProvider) -> MediaTools:
    """The AI media-generation checkpoint's own backend: genuine AI
    image generation (pollinations.ai - see
    ``src/providers/media/image.py``'s own docstring for why this
    vendor, and this project's hardware check in ARCHITECTURE.md for
    why not a local diffusion model) alongside the identical, unchanged
    local narration and composition already proven real. Narration and
    composition do not become "more AI" by sitting next to a genuine AI
    image backend - each of the four collaborators here remains exactly
    as independently replaceable as it already was.
    """
    return MediaTools(
        narrator=KokoroNarrationProvider(DEFAULT_KOKORO_MODEL_PATH, DEFAULT_KOKORO_VOICES_PATH),
        illustrator=PollinationsImageProvider(),
        composer=FFmpegVideoComposer(),
        video_generator=video_generator,
        clipper=FFmpegVideoClipper(),
    )


# Values are factory callables rather than instances, so nothing is
# constructed - no model loaded, no binary located, no network reached -
# unless actually selected, the identical reasoning every other factory
# here already states.
_BUILDERS: dict[str, Callable[[Settings, VideoGenerationProvider], MediaTools]] = {
    "null": _build_null,
    "local": _build_local,
    "ai_image": _build_ai_image,
}


def available_media_backends() -> list[str]:
    """Every media backend name that can currently be configured."""
    return sorted(_BUILDERS)


def build_media(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    video_generation_account: ProviderAccount | None = None,
) -> MediaTools:
    """Construct the media stack named by ``settings.media``, with its
    own AI video-generation provider built independently from
    ``settings.video_generation`` (or ``video_generation_account``, when
    given - the identical account-overrides-Settings seam
    ``src/providers/factory.py::build_provider`` already established for
    the Brain's own providers).

    Fails fast and loudly on an unknown name, the same rule every other
    factory in this project already follows. Construction itself is
    inert - no model is loaded, no engine started, no network reached -
    until the returned tools are actually used, matching every
    provider's own lazy-import/lazy-connect discipline.
    """
    try:
        builder = _BUILDERS[settings.media]
    except KeyError:
        raise ValueError(
            f"Unknown media backend {settings.media!r}. "
            f"Available: {', '.join(available_media_backends())}"
        ) from None

    video_generator = _build_video_generation(settings, env, video_generation_account)
    tools = builder(settings, video_generator)
    logger.info("media backend ready: %s", tools.name)
    return tools
