"""Application configuration.

One frozen object holding every tunable value, built once at startup and passed
down. The alternative - modules reading ``os.environ`` wherever they happen to
need something - scatters configuration across the codebase, makes tests fight
over global state, and hides what the application actually depends on.

Precedence, highest first:

    1. an explicit argument passed by the caller
    2. an environment variable
    3. the declared default

That ordering is what lets a test override one value without touching the
process environment, and lets deployment override without touching code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from typing import Mapping

# Prefixed so the orchestrator cannot collide with unrelated tools that also
# read LOG_LEVEL or PROVIDER from the environment.
ENV_PREFIX = "MANAGER_"

# The level names Python's logging module understands.
VALID_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})

_TRUE = frozenset({"true", "yes", "on", "1"})
_FALSE = frozenset({"false", "no", "off", "0", ""})


def _as_bool(value: str, name: str) -> bool:
    """Parse a boolean setting strictly.

    An unrecognised value raises rather than defaulting: silently reading
    "maybe" as True would be a way to turn on autonomy by accident.
    """
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false, got {value!r}")


def _as_positive_int(value: str, name: str) -> int:
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(f"{name} must be a whole number, got {value!r}") from None
    if number <= 0:
        raise ValueError(f"{name} must be positive, got {number}")
    return number


@dataclass(frozen=True)
class Settings:
    """Validated application settings.

    Frozen for the same reason Task is: configuration read at startup should
    not change under a running system. If a value is wrong, the program should
    fail loudly at startup rather than behave strangely an hour later.
    """

    log_level: str = "INFO"
    provider: str = "null"
    # How this installation hears and speaks. Selected by name, the same rule
    # `provider` above already lives under: never inferred from whether a
    # library happens to be importable or a key happens to be present. "none"
    # (NoSpeech, honestly UNAVAILABLE) is the only shipped option and the
    # default - see src/providers/speech_factory.py for why a second entry is
    # not invented here just to look complete.
    speech: str = "none"
    router: str = "capability"
    # Stage 14: where Alfred's current-information tool actually reaches
    # for real evidence, selected by name exactly as `provider` above
    # already is. "null" (no real source, honestly empty) is the default
    # for the same reason `provider` defaults to "null" - a real backend
    # is always a deliberate MANAGER_LIVE_INFO_PROVIDER=wikipedia, never
    # a side effect of upgrading, and the default keeps every existing
    # test hermetic (see conftest.py's own network-blocking fixture).
    # "wikipedia" needs no key and no signup - see
    # src/providers/wikipedia_live_info_provider.py.
    live_info_provider: str = "null"
    # Creative-media checkpoint: which real narration/image/video
    # backend the media_production tool is built from, selected by name
    # exactly as `live_info_provider` above already is.
    #   null     the default; no engine, media_production reports so honestly
    #   local    genuine local production - Kokoro narration, a local
    #            title-card image renderer, and FFmpeg composition - no
    #            vendor credential, no network call, ever
    #   ai_image genuine AI image generation (pollinations.ai - free,
    #            keyless) alongside the same local Kokoro narration and
    #            FFmpeg composition "local" already uses - see
    #            src/providers/media/image.py for why this vendor
    media: str = "null"
    # Pre-Stage-9 Campaign 8: which backend generates images, chosen on its
    # own axis. "media" keeps the media backend's own illustrator (the
    # default, unchanged behaviour); "null", "local" or "pollinations" choose
    # one explicitly, whatever the media backend is.
    image_generation: str = "media"
    # AI video-generation checkpoint: a genuinely independent axis from
    # `media` above - narration/image/composition ("local"/"ai_image")
    # is one choice, whether a real AI video-*generation* vendor is
    # configured is a separate one, exactly the way `provider` and
    # `specialist_brain` are two independent switches rather than one.
    #   null    the default; no vendor, media_production reports so honestly
    #   runway  a real, mature, credentialed provider - see
    #           src/providers/media/video_generation.py for why this one
    #   veo     a second, independently-chosen real vendor (Google's Veo,
    #           via the Gemini API) - added specifically to prove the
    #           VideoGenerationProvider abstraction is genuinely neutral,
    #           not shaped around Runway alone (see that module's own
    #           docstring)
    video_generation: str = "null"
    # Model identifier for each video-generation vendor, the identical
    # per-vendor-field discipline `openai_model`/`xai_model` already
    # establish for the Brain's own providers - one field per vendor
    # rather than a shared one, so neither name is ever a foreign
    # vendor's model string reaching the wrong builder.
    runway_model: str = "gen4_turbo"
    veo_model: str = "veo-3.1-generate-preview"
    # Video-*understanding* checkpoint: a third, genuinely independent
    # media axis from `media` (local production) and `video_generation`
    # (AI synthesis) - whether a real speech-transcription vendor is
    # configured, so clip_video can derive real, timestamped evidence
    # from a video's own actual audio rather than only from text the
    # user separately typed. Same "one deployment may want one without
    # the others" reasoning video_generation's own comment already
    # states.
    #   null    the default; no vendor, media_production reports so honestly
    #   whisper a real, credentialed vendor (OpenAI's Whisper API) - see
    #           src/providers/media/transcription.py for why this one
    video_understanding: str = "null"
    # Whisper's own model identifier - the identical per-vendor-field
    # discipline runway_model/veo_model already establish.
    whisper_model: str = "whisper-1"
    # Stage 15: where Alfred's governed communication tools actually
    # reach for a real phone/messaging connection, selected by name
    # exactly as `live_info_provider` above already is. "null" (no
    # authorized connection, honestly unavailable) is the only shipped
    # option and the default - this stage's own brief explicitly asks
    # for the honest null implementation rather than a real external
    # service added just to demonstrate the concept.
    communication_provider: str = "null"
    # Stage 18: where Alfred's knowledge tool reaches for the documents,
    # books and notes the *user themselves* supplied - selected by name
    # exactly as `live_info_provider` above already is, and kept a
    # separate axis from it because they are separate kinds of knowing
    # (see src/providers/knowledge.py's own docstring).
    #
    # "documents" rather than "null" is the default, and unlike
    # live_info_provider that is safe: it reads a local folder, touches no
    # network, needs no key and no signup, and an installation where the
    # folder is absent or empty gets an honestly empty result - the same
    # answer "null" would give. The choice matches memory's own default
    # for the same reason: the capability is useless switched off, and its
    # risk is nil.
    knowledge_provider: str = "documents"
    # Where those documents live. The user puts anything they want Alfred
    # to be able to quote in here - a book, a set of notes, a syllabus, a
    # transcript. Nothing in this project knows or cares what is in it.
    knowledge_dir: str = "data/knowledge"
    # Stage 12 closure: where a genuine milestone celebration's
    # presentation reaction actually comes from, selected by name for the
    # same replaceability every sibling provider above already has. Unlike
    # them, "default" is not an honestly-empty placeholder - it is the one
    # real implementation this project ships (see
    # src/providers/default_celebration_provider.py for why no "null"
    # variant exists), and it is also the default here for the identical
    # reason every other default is the safe, always-working choice.
    celebration_provider: str = "default"
    # Stage 12 closure: where Alfred's temporal-event engine reaches for
    # named, recurring calendar occasions (Christmas, Valentine's Day,
    # Easter) - selected by name for the same replaceability every
    # sibling provider above already has. "default" is a genuinely
    # computed set (see src/providers/default_calendar_event_provider.py),
    # not an empty placeholder - the same reasoning celebration_provider's
    # own default already follows.
    calendar_event_provider: str = "default"
    # Stage 16: where Alfred's governed connection tools reach for a
    # real external, authenticated service - selected by name for the
    # same replaceability every provider above already has. "null"
    # (honestly unavailable) is the default, the identical reasoning
    # communication_provider already follows: a connection is
    # consequential, so a real one is always a deliberate
    # MANAGER_CONNECTION_PROVIDER=gmail, never a side effect of upgrading.
    connection_provider: str = "null"
    # Stage 17: where an agent's presence gets its avatar profile from -
    # selected by name for the same replaceability every provider above
    # already has. "default" (a small, deterministic, no-network
    # catalog - see that module's own docstring) is what an
    # unconfigured installation gets, the same choice celebration_provider
    # already makes: nothing here carries external risk, so there is
    # nothing to be null about.
    avatar_provider: str = "default"
    # Universal Ecosystem checkpoint: where an agent's presence reaches
    # for its *scene* description (mood/lighting/backdrop), selected by
    # name for the same replaceability every provider above already
    # has, and kept a genuinely separate axis from avatar_provider - the
    # identical "two independent questions" reasoning
    # src/providers/environment.py's own docstring states: what an
    # agent looks like is not what the space around it looks like.
    # "default" (a small, deterministic, no-network catalog - see that
    # module's own docstring) is what an unconfigured installation
    # gets, the same choice avatar_provider's own default already makes.
    environment_provider: str = "default"
    # Reasoning is off by default, matching the router's conservative default.
    # "llm" is still safe: the brain receives whatever build_provider returned,
    # which is the null provider unless a provider was deliberately configured.
    brain: str = "null"
    # The specialists' own reasoning, configured separately from the Manager's.
    # Separate because they are separate capabilities: a deployment may want the
    # orchestrator to deliberate while its specialists stay deterministic, or
    # the reverse. One switch for both would make that impossible to express.
    specialist_brain: str = "null"
    # Where what an agent remembers actually lives.
    #
    #   persistent   records are written under memory_dir, so a rebuilt agent -
    #                the heartbeat builds one per tick - can recall what the
    #                last one learned. The default.
    #   ephemeral    records live in the process and die with it. Still fully
    #                supported, and the right choice for a test, a one-off
    #                inspection, or anywhere durability would be a surprise.
    #
    # This defaulted to "ephemeral" on the argument that durability decides
    # where a person's data sits and should never arrive as a side effect of an
    # upgrade. That argument is sound about *surprise* and turned out to be
    # wrong about the default, because it made forgetting the surprise instead:
    # an agent whose whole purpose is to notice things for somebody, running on
    # an hourly heartbeat, began every tick knowing nothing. Continuity was not
    # merely off - no memory file had ever been written.
    #
    # An agent that cannot remember is not a safer agent, it is a less useful
    # one, and the confidentiality question is answered by what the policy
    # admits rather than by whether the store survives a restart. Records still
    # pass MemoryPolicy first, still exclude secrets, and can still be removed:
    # forget() and clear() are part of the Memory contract precisely so
    # durability never means "kept forever whether you like it or not".
    memory: str = "persistent"
    memory_dir: str = "data/memory"
    # Which human calendar decides what "today" means. Storage stays UTC - a
    # MemoryRecord's created_at is never reinterpreted - and this only decides
    # which local day an instant fell on. 23:30 UTC is already tomorrow in
    # Lagos, and it is the person's day that matters when deciding what is
    # still current. Validated where it is resolved, not here: Settings does
    # not import the things it configures.
    timezone: str = "Africa/Lagos"
    model: str = "claude-opus-5"
    # Phase 1.4: each provider owns its own model configuration rather than
    # sharing the field above, which is anthropic's - a name like
    # "claude-opus-5" reaching the openai or xai builder would be a foreign
    # vendor's model string, not a sensible default for either. Two more
    # fields rather than turning `model` into a mapping, for the same reason
    # every other per-vendor setting above is its own field: from_env()
    # derives MANAGER_OPENAI_MODEL / MANAGER_XAI_MODEL from the field name
    # automatically, and each is independently overridable without touching
    # the others.
    openai_model: str = "gpt-5"
    xai_model: str = "grok-4"
    # A local model on this machine (MANAGER_PROVIDER=local) - llama.cpp and a
    # small GGUF file, no account and no network at inference time. Honestly a
    # local tier, never presented as equivalent to a vendor-backed model.
    local_model_path: str = "data/models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf"
    local_vision_model_path: str = "data/models/vision/moondream2-text-model-f16.gguf"
    local_vision_projector_path: str = "data/models/vision/moondream2-mmproj-f16.gguf"
    # Whether capabilities may consult the local model as an attributed
    # assistant for what Half Brain cannot do alone ("null" | "local").
    local_assist: str = "null"
    # Universal voice translation: the offline translation engine ("null" |
    # "argos"), chosen through the Control Center category translation_provider.
    translation: str = "null"
    # How a due reminder reaches the person when no page is open ("null" |
    # "windows"), chosen through the Control Center category
    # desktop_notification_provider; used by the heartbeat.
    desktop_notifications: str = "null"
    mailbox: str = "memory"
    profile_path: str = "USER.md"
    # Where the user's schedule document lives. Separate from profile_path
    # because they describe different things about the same person: one what
    # they can do, the other when they are around. A missing file is not an
    # error - most people will never write one.
    schedule_path: str = "SCHEDULE.md"
    identity_path: str = "IDENTITY.md"
    soul_path: str = "SOUL.md"
    # Where each specialist's own identity document lives, one file per
    # specialist, named by the technical key the code knows it by. Separate
    # from identity_path because these are different agents' documents, not
    # more of the Manager's: the Manager's identity is one file, and each
    # specialist owns its own. A missing directory is not an error - a
    # specialist with no document keeps the name its module declares.
    identities_dir: str = "identities"

    # Heartbeat. Disabled by default: autonomy is opt-in, never a side effect
    # of upgrading. Declared as str because from_env() reads environment
    # variables, which are always strings; __post_init__ coerces them - a raw
    # "false" would otherwise be truthy, which is exactly the kind of silent
    # failure a safety switch must not have.
    heartbeat_enabled: str = "false"
    heartbeat_interval_minutes: str = "60"
    heartbeat_fetch_limit: str = "25"
    state_path: str = "data/state/processed.json"
    reports_dir: str = "data/reports"
    # Where media_production writes real, produced narration/image/video
    # assets - configurable for the identical reason state_path/reports_dir
    # already are: a test must be able to redirect production writes to a
    # tmp_path rather than the real data/ directory.
    media_asset_dir: str = "data/media/assets"
    # Phase 3 (Knowledge, Research & Live Information): where
    # generate_document's real, saved output goes - configurable for the
    # identical reason media_asset_dir already is: a test must be able to
    # redirect production writes to a tmp_path rather than the real data/
    # directory.
    documents_dir: str = "data/documents/generated"

    # Multimodal workspace/artifact checkpoint: where the one
    # authoritative ArtifactStore (src/services/artifacts/store.py)
    # keeps its index and every artifact's real content/rendition
    # files - a genuinely separate concern from documents_dir above,
    # which is generate_document's own flat, disposable, non-versioned
    # text notes. An artifact has a stable identity and a real version
    # chain; a document_generation file does not.
    artifacts_dir: str = "data/artifacts"

    # Multimodal workspace/artifact checkpoint: which real engine
    # ArtifactProductionTool renders a document artifact's PDF export
    # with, selected by name exactly as `media` above already is.
    #   null        no engine, artifact creation still succeeds (the
    #               structured content is the artifact's real,
    #               canonical form) but no "pdf" rendition is produced
    #   simple_pdf  a real, local, deterministic renderer with zero
    #               external risk - see src/providers/documents/pdf.py
    #               for why this is the default rather than an opt-in
    #               "null", the same reasoning celebration_provider's
    #               own default already follows
    document_rendering: str = "simple_pdf"

    # Paths to credential files - NOT the credentials themselves. Both live
    # under data/, which is gitignored. Storing a path here means a secret
    # never enters configuration, logs, or a crash report.
    gmail_credentials_path: str = "data/credentials.json"
    gmail_token_path: str = "data/token.json"

    # A secret. `repr=False` keeps it out of the auto-generated __repr__, so a
    # Settings object printed in a traceback or debugger cannot leak the key.
    # describe() masks it separately for the same reason.
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        level = self.log_level.strip().upper()
        if level not in VALID_LOG_LEVELS:
            raise ValueError(
                f"Invalid log_level {self.log_level!r}. "
                f"Expected one of: {', '.join(sorted(VALID_LOG_LEVELS))}"
            )
        object.__setattr__(self, "log_level", level)

        provider = self.provider.strip().lower()
        if not provider:
            raise ValueError("Settings.provider must not be empty")
        object.__setattr__(self, "provider", provider)

        speech = self.speech.strip().lower()
        if not speech:
            raise ValueError("Settings.speech must not be empty")
        object.__setattr__(self, "speech", speech)

        router = self.router.strip().lower()
        if not router:
            raise ValueError("Settings.router must not be empty")
        object.__setattr__(self, "router", router)

        live_info_provider = self.live_info_provider.strip().lower()
        if not live_info_provider:
            raise ValueError("Settings.live_info_provider must not be empty")
        object.__setattr__(self, "live_info_provider", live_info_provider)

        media = self.media.strip().lower()
        if not media:
            raise ValueError("Settings.media must not be empty")
        object.__setattr__(self, "media", media)

        image_generation = self.image_generation.strip().lower()
        if not image_generation:
            raise ValueError("Settings.image_generation must not be empty")
        object.__setattr__(self, "image_generation", image_generation)

        video_generation = self.video_generation.strip().lower()
        if not video_generation:
            raise ValueError("Settings.video_generation must not be empty")
        object.__setattr__(self, "video_generation", video_generation)

        runway_model = self.runway_model.strip()
        if not runway_model:
            raise ValueError("Settings.runway_model must not be empty")
        object.__setattr__(self, "runway_model", runway_model)

        veo_model = self.veo_model.strip()
        if not veo_model:
            raise ValueError("Settings.veo_model must not be empty")
        object.__setattr__(self, "veo_model", veo_model)

        video_understanding = self.video_understanding.strip().lower()
        if not video_understanding:
            raise ValueError("Settings.video_understanding must not be empty")
        object.__setattr__(self, "video_understanding", video_understanding)

        translation = str(self.translation or "").strip().lower()
        if not translation:
            raise ValueError("Settings.translation must not be empty")
        object.__setattr__(self, "translation", translation)

        whisper_model = self.whisper_model.strip()
        if not whisper_model:
            raise ValueError("Settings.whisper_model must not be empty")
        object.__setattr__(self, "whisper_model", whisper_model)

        document_rendering = self.document_rendering.strip().lower()
        if not document_rendering:
            raise ValueError("Settings.document_rendering must not be empty")
        object.__setattr__(self, "document_rendering", document_rendering)

        communication_provider = self.communication_provider.strip().lower()
        if not communication_provider:
            raise ValueError("Settings.communication_provider must not be empty")
        object.__setattr__(self, "communication_provider", communication_provider)

        knowledge_provider = self.knowledge_provider.strip().lower()
        if not knowledge_provider:
            raise ValueError("Settings.knowledge_provider must not be empty")
        object.__setattr__(self, "knowledge_provider", knowledge_provider)

        knowledge_dir = self.knowledge_dir.strip()
        if not knowledge_dir:
            raise ValueError("Settings.knowledge_dir must not be empty")
        object.__setattr__(self, "knowledge_dir", knowledge_dir)

        celebration_provider = self.celebration_provider.strip().lower()
        if not celebration_provider:
            raise ValueError("Settings.celebration_provider must not be empty")
        object.__setattr__(self, "celebration_provider", celebration_provider)

        calendar_event_provider = self.calendar_event_provider.strip().lower()
        if not calendar_event_provider:
            raise ValueError("Settings.calendar_event_provider must not be empty")
        object.__setattr__(self, "calendar_event_provider", calendar_event_provider)

        connection_provider = self.connection_provider.strip().lower()
        if not connection_provider:
            raise ValueError("Settings.connection_provider must not be empty")
        object.__setattr__(self, "connection_provider", connection_provider)

        avatar_provider = self.avatar_provider.strip().lower()
        if not avatar_provider:
            raise ValueError("Settings.avatar_provider must not be empty")
        object.__setattr__(self, "avatar_provider", avatar_provider)

        environment_provider = self.environment_provider.strip().lower()
        if not environment_provider:
            raise ValueError("Settings.environment_provider must not be empty")
        object.__setattr__(self, "environment_provider", environment_provider)

        brain = self.brain.strip().lower()
        if not brain:
            raise ValueError("Settings.brain must not be empty")
        object.__setattr__(self, "brain", brain)

        specialist_brain = self.specialist_brain.strip().lower()
        if not specialist_brain:
            raise ValueError("Settings.specialist_brain must not be empty")
        object.__setattr__(self, "specialist_brain", specialist_brain)

        memory = self.memory.strip().lower()
        if not memory:
            raise ValueError("Settings.memory must not be empty")
        object.__setattr__(self, "memory", memory)

        memory_dir = self.memory_dir.strip()
        if not memory_dir:
            raise ValueError("Settings.memory_dir must not be empty")
        object.__setattr__(self, "memory_dir", memory_dir)

        zone = " ".join(self.timezone.split())
        if not zone:
            raise ValueError("Settings.timezone must not be empty")
        # Not lower-cased: IANA names are case-sensitive identifiers owned by
        # the tz database, the same reasoning that leaves `model` alone.
        object.__setattr__(self, "timezone", zone)

        mailbox = self.mailbox.strip().lower()
        if not mailbox:
            raise ValueError("Settings.mailbox must not be empty")
        object.__setattr__(self, "mailbox", mailbox)

        model = self.model.strip()
        if not model:
            raise ValueError("Settings.model must not be empty")
        # Not lowercased: model identifiers are opaque strings owned by the
        # provider, so normalizing their case could invent an invalid name.
        object.__setattr__(self, "model", model)

        openai_model = self.openai_model.strip()
        if not openai_model:
            raise ValueError("Settings.openai_model must not be empty")
        object.__setattr__(self, "openai_model", openai_model)

        xai_model = self.xai_model.strip()
        if not xai_model:
            raise ValueError("Settings.xai_model must not be empty")
        object.__setattr__(self, "xai_model", xai_model)

        # An empty or whitespace-only key is treated as "not supplied" rather
        # than as a key, so a blank environment variable cannot be mistaken for
        # a credential and produce a confusing auth failure later.
        api_key = (self.api_key or "").strip()
        object.__setattr__(self, "api_key", api_key or None)

        # Note what is deliberately NOT validated here: whether `provider` and
        # `router` name things that actually exist. Settings would have to
        # import those packages to know, coupling configuration to the things
        # it configures. Resolving names is each factory's job.

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None, **overrides: str | None) -> "Settings":
        """Build Settings from environment variables, with optional overrides.

        ``env`` is injectable so tests can pass a plain dict instead of mutating
        ``os.environ`` - global mutable state is a common source of tests that
        pass alone and fail together.
        """
        source = os.environ if env is None else env

        values: dict[str, str] = {}
        for field in fields(cls):
            env_value = source.get(f"{ENV_PREFIX}{field.name.upper()}")
            if env_value is not None:
                values[field.name] = env_value

        # Explicit arguments win over the environment. `None` means "not
        # specified", so it never overrides anything.
        values.update({k: v for k, v in overrides.items() if v is not None})

        return cls(**values)

    # ------------------------------------------------------------------
    # Typed accessors
    #
    # Environment variables are strings, so the raw fields are strings and the
    # coercion happens here, once, with validation. Reading `settings.
    # heartbeat_enabled` directly would be a bug waiting to happen: the string
    # "false" is truthy.
    # ------------------------------------------------------------------

    @property
    def heartbeat_is_enabled(self) -> bool:
        return _as_bool(self.heartbeat_enabled, "MANAGER_HEARTBEAT_ENABLED")

    @property
    def heartbeat_interval(self) -> int:
        return _as_positive_int(
            self.heartbeat_interval_minutes, "MANAGER_HEARTBEAT_INTERVAL_MINUTES"
        )

    @property
    def heartbeat_limit(self) -> int:
        return _as_positive_int(
            self.heartbeat_fetch_limit, "MANAGER_HEARTBEAT_FETCH_LIMIT"
        )

    @property
    def has_api_key(self) -> bool:
        """Whether a key was supplied, without exposing its value."""
        return self.api_key is not None

    def describe(self) -> str:
        """A short one-line summary, safe to log.

        The API key is reported as present or absent and never printed.
        Configuration routinely ends up in log files and crash reports, so a
        secret must never be rendered here - only the fact that one was set.
        """
        return (
            f"log_level={self.log_level} provider={self.provider} "
            f"speech={self.speech} "
            f"router={self.router} live_info_provider={self.live_info_provider} "
            f"media={self.media} video_generation={self.video_generation} "
            f"video_understanding={self.video_understanding} "
            f"communication_provider={self.communication_provider} "
            f"knowledge_provider={self.knowledge_provider} "
            f"celebration_provider={self.celebration_provider} "
            f"calendar_event_provider={self.calendar_event_provider} "
            f"connection_provider={self.connection_provider} "
            f"avatar_provider={self.avatar_provider} "
            f"environment_provider={self.environment_provider} "
            f"brain={self.brain} "
            f"specialist_brain={self.specialist_brain} memory={self.memory} "
            f"timezone={self.timezone} model={self.model} "
            f"openai_model={self.openai_model} xai_model={self.xai_model} "
            f"mailbox={self.mailbox} "
            f"api_key={'set' if self.has_api_key else 'unset'}"
        )
