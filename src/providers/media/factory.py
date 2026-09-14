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
from src.providers.media.transcription import (
    NullTranscriptionProvider,
    TranscriptionProvider,
    WhisperTranscriptionProvider,
)
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

# Video-*understanding* checkpoint: Whisper's own, dedicated credential -
# never the openai_provider LLM key this project may also have
# configured, even though both happen to be issued by OpenAI. The
# identical per-*axis*, not per-*vendor*, isolation RUNWAY_KEY_ENV/
# VEO_KEY_ENV already establish (see this module's own docstring),
# applied to a transcription vendor that happens to share a company
# with an entirely unrelated axis.
WHISPER_KEY_ENV = "WHISPER_API_KEY"


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
    # Video-*understanding* checkpoint: real speech-to-text over a
    # video's own, locally-extracted audio - genuinely different work
    # from clipper's mechanical cut, and from video_generator's
    # synthesis. Vendor-backed like video_generator (a real credential
    # may be required), so it gets its own independent axis
    # (settings.video_understanding) rather than being folded into
    # `media` or `video_generation` - the same "one deployment may want
    # one without the other" reasoning this class's own docstring
    # already states for every other collaborator here.
    transcriber: TranscriptionProvider = field(default_factory=NullTranscriptionProvider)

    @property
    def name(self) -> str:
        return (
            f"{self.narrator.name}+{self.illustrator.name}+"
            f"{self.composer.name}+{self.video_generator.name}+"
            f"{self.clipper.name}+{self.transcriber.name}"
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


def _whisper_credential(env: Mapping[str, str] | None = None) -> str | None:
    """Whisper's own credential, and only Whisper's - never the LLM
    provider's ``OPENAI_API_KEY``, even when both happen to be set."""
    source = os.environ if env is None else env
    key = (source.get(WHISPER_KEY_ENV) or "").strip()
    return key or None


def _build_video_understanding(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    account: ProviderAccount | None = None,
) -> TranscriptionProvider:
    """Construct the speech-transcription provider named by ``account``
    (when given) or, otherwise, ``settings.video_understanding`` - the
    identical "account picks the vendor, else Settings does" rule
    ``_build_video_generation`` already establishes, applied to this
    genuinely separate axis.

    Fails fast on a missing credential, before any vendor SDK or
    network call - the identical discipline every other real provider
    builder in this project already holds itself to.
    """
    provider_name = account.provider if account is not None else settings.video_understanding

    if provider_name == "null":
        return NullTranscriptionProvider()

    if provider_name == "windows":
        # Pre-Stage-9 Campaign 3: offline recognition through Windows' own
        # speech engine - no vendor, no key, no network. Honestly refused on
        # any other platform rather than pretending to exist there.
        from src.providers.media.local_transcription import WindowsSpeechTranscriptionProvider

        if not WindowsSpeechTranscriptionProvider.available():
            raise ProviderConfigurationError(
                "The 'windows' video-understanding provider uses Windows' own speech "
                "recogniser and is only available on Windows. Set "
                "MANAGER_VIDEO_UNDERSTANDING=null or whisper instead."
            )
        return WindowsSpeechTranscriptionProvider()

    if provider_name == "whisper":
        credential = account.credential if account is not None else _whisper_credential(env)
        if credential is None:
            raise ProviderConfigurationError(
                "The 'whisper' video-understanding provider is selected but no "
                f"API key is configured. Set {WHISPER_KEY_ENV} in the "
                "environment. To run without one instead, set "
                "MANAGER_VIDEO_UNDERSTANDING=null (the default)."
            )
        model = (account.model if account is not None and account.model else None) or settings.whisper_model
        return WhisperTranscriptionProvider(api_key=credential, model=model)

    raise ValueError(
        f"Unknown video-understanding provider {provider_name!r}. "
        f"Available: {', '.join(available_video_understanding_backends())}"
    )


def available_video_understanding_backends() -> list[str]:
    """Every speech-transcription backend name that can currently be
    configured - "null" (honestly unavailable) plus one real,
    credentialed vendor, the identical shape
    ``available_video_generation_backends`` already takes."""
    return ["null", "whisper", "windows"]


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


def _build_null(
    settings: Settings, video_generator: VideoGenerationProvider, transcriber: TranscriptionProvider
) -> MediaTools:
    return MediaTools(
        narrator=NullNarrationProvider(),
        illustrator=NullImageProvider(),
        composer=NullVideoComposer(),
        video_generator=video_generator,
        clipper=NullVideoClipper(),
        transcriber=transcriber,
    )


def _build_local(
    settings: Settings, video_generator: VideoGenerationProvider, transcriber: TranscriptionProvider
) -> MediaTools:
    return MediaTools(
        narrator=KokoroNarrationProvider(DEFAULT_KOKORO_MODEL_PATH, DEFAULT_KOKORO_VOICES_PATH),
        illustrator=LocalTitleCardImageProvider(),
        composer=FFmpegVideoComposer(),
        video_generator=video_generator,
        clipper=FFmpegVideoClipper(),
        transcriber=transcriber,
    )


def _build_ai_image(
    settings: Settings, video_generator: VideoGenerationProvider, transcriber: TranscriptionProvider
) -> MediaTools:
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
        transcriber=transcriber,
    )


# Values are factory callables rather than instances, so nothing is
# constructed - no model loaded, no binary located, no network reached -
# unless actually selected, the identical reasoning every other factory
# here already states.
_BUILDERS: dict[str, Callable[[Settings, VideoGenerationProvider, TranscriptionProvider], MediaTools]] = {
    "null": _build_null,
    "local": _build_local,
    "ai_image": _build_ai_image,
}


def available_image_generation_backends() -> list[str]:
    """Every image-generation backend that can be chosen on its own axis -
    "media" (the media backend's own illustrator), none, the local renderer,
    or a real AI image vendor."""
    return ["media", "null", "local", "pollinations"]


def _build_image_generation(settings: Settings) -> "ImageProvider | None":
    """The illustrator chosen by ``settings.image_generation``, or None to
    keep the media backend's own. Construction is inert - nothing is reached
    until an image is actually requested."""
    name = settings.image_generation
    if name == "media":
        return None
    if name == "null":
        return NullImageProvider()
    if name == "local":
        return LocalTitleCardImageProvider()
    if name == "pollinations":
        return PollinationsImageProvider()
    raise ValueError(
        f"Unknown image-generation backend {name!r}. "
        f"Available: {', '.join(available_image_generation_backends())}"
    )


def available_media_backends() -> list[str]:
    """Every media backend name that can currently be configured."""
    return sorted(_BUILDERS)


def build_media(
    settings: Settings,
    env: Mapping[str, str] | None = None,
    video_generation_account: ProviderAccount | None = None,
    video_understanding_account: ProviderAccount | None = None,
) -> MediaTools:
    """Construct the media stack named by ``settings.media``, with its
    own AI video-generation provider built independently from
    ``settings.video_generation`` (or ``video_generation_account``, when
    given) and its own speech-transcription provider built independently
    from ``settings.video_understanding`` (or ``video_understanding_account``,
    when given) - the identical account-overrides-Settings seam
    ``src/providers/factory.py::build_provider`` already established for
    the Brain's own providers, applied to two more, genuinely independent
    axes.

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
    transcriber = _build_video_understanding(settings, env, video_understanding_account)
    tools = builder(settings, video_generator, transcriber)
    illustrator = _build_image_generation(settings)
    if illustrator is not None:
        # Pre-Stage-9 Campaign 8: only the image collaborator changes.
        from dataclasses import replace as _replace

        tools = _replace(tools, illustrator=illustrator)
    logger.info("media backend ready: %s", tools.name)
    return tools
