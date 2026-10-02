"""Assembles the ``EcosystemRegistry`` from every axis this project
already has - wrapping each existing ``available_*``/``build_*`` pair
with a ``GenericCategoryAdapter``, never reimplementing what those
functions already do. Adding a category here is one call to
``registry.register(...)``; nothing about ``EcosystemRegistry`` or
``GenericCategoryAdapter`` needs to change for it.

**Why this lives outside ``src/composition.py``.** ``composition.py``
builds the *runtime* - the things Alfred actually runs on. This module
builds a *read/validate* surface over the same configuration, for an
operator (a CLI, a future admin UI) to inspect and manage - a genuinely
different concern with a genuinely different lifetime (an operator
tool may want to validate a candidate ``Settings`` that is never
actually run), so it is its own module rather than another field on
``Runtime``.
"""

from __future__ import annotations

from dataclasses import replace

from src.config.settings import Settings
from src.manager.capabilities import available_capabilities
from src.providers.avatar_factory import available_avatar_providers, build_avatar_provider
from src.providers.calendar_events_factory import (
    available_calendar_event_providers,
    build_calendar_event_provider,
)
from src.providers.celebration_factory import available_celebration_providers, build_celebration_provider
from src.providers.communication_factory import (
    available_communication_providers,
    build_communication_provider,
)
from src.providers.connection_factory import available_connection_providers, build_connection
from src.providers.documents.factory import available_document_rendering_backends, build_document_rendering
from src.providers.artifact_runtime_factory import (
    available_artifact_runtimes,
    build_artifact_runtime,
)
from src.providers.environment_factory import available_environment_providers, build_environment_provider
from src.providers.factory import available_providers, build_provider, provider_requires_credential
from src.providers.knowledge_factory import available_knowledge_providers, build_knowledge_provider
from src.providers.live_info_factory import available_live_info_providers, build_live_info_provider
from src.providers.media.factory import (
    available_image_generation_backends,
    available_media_backends,
    available_video_generation_backends,
    available_video_understanding_backends,
    build_media,
    video_understanding_requires_credential,
)
from src.providers.speech_factory import available_speech_providers, build_speech, speech_requires_credential
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.memory.factory import available_memories, build_memory
from src.services.ecosystem.contracts import EcosystemRegistry, GenericCategoryAdapter

# The account_id every validation-only construction uses. Never
# persisted, never selected as anyone's real active account - a
# throwaway label for a throwaway ProviderAccount that exists only long
# enough for one build_*() call to consume its credential.
_VALIDATION_ACCOUNT_ID = "ecosystem-validate"


def _credentialed_account(
    provider: str, credential: "str | ProviderAccount | None"
) -> "ProviderAccount | None":
    """The throwaway account one ``build_*()`` call consumes - or the
    caller's own real account, passed straight through.

    ``validate`` only ever has a bare credential string, which is all
    construction needs. A live authorization check (closure campaign 3,
    2026-09-17) has the real stored ``ProviderAccount`` instead, and that
    carries more than the secret: an account may pin its own ``model``.
    Wrapping it in a synthetic account here would silently build against the
    default model and check something other than what the account describes,
    so a real account is used as given.
    """
    if credential is None:
        return None
    if isinstance(credential, ProviderAccount):
        return credential
    return ProviderAccount(provider=provider, account_id=_VALIDATION_ACCOUNT_ID, credential=credential)


def build_ecosystem_registry(settings: Settings) -> EcosystemRegistry:
    """Build a registry describing every category this project's
    provider ecosystem currently has, from ``settings`` alone - no
    ``Runtime`` required, so a candidate configuration can be inspected
    and validated before anything is actually run with it.

    Every ``build`` closure below calls the identical real factory
    function ``src/composition.py`` itself calls - ``validate()``
    genuinely constructs a candidate (real client/object construction,
    the same IMPLEMENTED/TEST-VERIFIED tier every provider's own
    construction-only test already relies on), never a live network
    call - honestly distinct from LIVE EXTERNAL VERIFIED, exactly as
    this checkpoint's own reality standard requires.
    """
    registry = EcosystemRegistry()

    registry.register(
        GenericCategoryAdapter(
            "llm_provider",
            list_backends=available_providers,
            active_key=lambda: settings.provider,
            build=lambda key, credential: build_provider(
                replace(settings, provider=key), account=_credentialed_account(key, credential)
            ),
            requires_credential=provider_requires_credential,
            dependents=("the Manager's own reasoning (ManagerBrain)", "every specialist's own brain"),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "video_generation_provider",
            list_backends=available_video_generation_backends,
            active_key=lambda: settings.video_generation,
            build=lambda key, credential: build_media(
                replace(settings, video_generation=key),
                video_generation_account=_credentialed_account(key, credential),
            ),
            requires_credential=lambda key: key != "null",
            dependents=("the media_production capability's generate_video operation",),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "video_understanding_provider",
            list_backends=available_video_understanding_backends,
            active_key=lambda: settings.video_understanding,
            build=lambda key, credential: build_media(
                replace(settings, video_understanding=key),
                video_understanding_account=_credentialed_account(key, credential),
            ),
            # "windows" and "local_whisper" run on this machine - no key to hold.
            requires_credential=video_understanding_requires_credential,
            dependents=(
                "the media_production capability's transcribe operation "
                "(feeding clip_video's real, audio-derived moment selection)",
            ),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "media",
            list_backends=available_media_backends,
            active_key=lambda: settings.media,
            build=lambda key, credential: build_media(replace(settings, media=key)),
            requires_credential=lambda key: False,
            dependents=("the media_production capability's narrate/illustrate/compose operations",),
        )
    )

    # The attributed local assistant (null | local) - never a reasoning brain.
    from src.providers.local_assistant import available_local_assist_backends

    registry.register(
        GenericCategoryAdapter(
            "local_assist_provider",
            list_backends=available_local_assist_backends,
            active_key=lambda: settings.local_assist,
            build=lambda key, credential: key,
            requires_credential=lambda key: False,
            dependents=("open conversation when no Brain answers (attributed local answers)",),
        )
    )

    # Desktop notifications: how a due reminder reaches the person with no page open.
    from src.providers.desktop_notifier import available_desktop_notification_backends, build_desktop_notifier

    registry.register(
        GenericCategoryAdapter(
            "desktop_notification_provider",
            list_backends=available_desktop_notification_backends,
            active_key=lambda: settings.desktop_notifications,
            build=lambda key, credential: build_desktop_notifier(replace(settings, desktop_notifications=key)),
            requires_credential=lambda key: False,
            dependents=("the heartbeat's due reminders when no page is open",),
        )
    )

    # Universal voice translation: the offline translation engine on its own axis.
    from src.providers.translation.factory import available_translation_backends, build_translator

    registry.register(
        GenericCategoryAdapter(
            "translation_provider",
            list_backends=available_translation_backends,
            active_key=lambda: settings.translation,
            build=lambda key, credential: build_translator(replace(settings, translation=key), strict=True),
            requires_credential=lambda key: False,
            dependents=(
                "the translate capability (offline, in Half Brain as well as Full Brain)",
                "live spoken and typed translation on the served page (POST /translate/speech, /translate/text)",
            ),
        )
    )

    # GM-03: structured perception - what is where in a still or a recording,
    # followed over time - on its own axis.
    from src.providers.perception.factory import available_perception_backends, build_perception

    registry.register(
        GenericCategoryAdapter(
            "perception_provider",
            list_backends=available_perception_backends,
            active_key=lambda: settings.perception,
            build=lambda key, credential: build_perception(replace(settings, perception=key)),
            requires_credential=lambda key: False,
            dependents=(
                "the perceive_media capability (a shared still or recording)",
                "watches that hand their frames to perception (a live camera)",
            ),
        )
    )

    # Pre-Stage-9 Campaign 8 (PRV-07): image generation on its own axis.
    registry.register(
        GenericCategoryAdapter(
            "image_generation_provider",
            list_backends=available_image_generation_backends,
            active_key=lambda: settings.image_generation,
            build=lambda key, credential: build_media(replace(settings, image_generation=key)),
            requires_credential=lambda key: False,
            dependents=("the media_production capability's illustrate operation", "create_diagram"),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "speech_provider",
            list_backends=available_speech_providers,
            active_key=lambda: settings.speech,
            build=lambda key, credential: build_speech(
                replace(settings, speech=key), account=_credentialed_account(key, credential)
            ),
            requires_credential=speech_requires_credential,
            dependents=("VoiceChannel's spoken delivery",),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "connection",
            list_backends=available_connection_providers,
            active_key=lambda: settings.connection_provider,
            build=lambda key, credential: build_connection(replace(settings, connection_provider=key)),
            # Deliberately never True: a connection's credential (Gmail's
            # OAuth token files today) is not a single string secret the
            # way a vendor API key is, so this category is validated by
            # real (lazy-connecting) construction alone, never by a
            # `credential` string passed through this registry - the
            # ecosystem layer knowing the difference between credential
            # *shapes* rather than forcing every category into one, per
            # this checkpoint's own explicit instruction.
            requires_credential=lambda key: False,
            dependents=("capabilities that reach connected_action (see src/manager/capabilities.py)",),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "avatar_provider",
            list_backends=available_avatar_providers,
            active_key=lambda: settings.avatar_provider,
            build=lambda key, credential: build_avatar_provider(replace(settings, avatar_provider=key)),
            requires_credential=lambda key: False,
            dependents=(
                "every agent's presence catalog and deterministic default "
                "(see src/services/identity/presence.py) - an agent's own chosen "
                "avatar_id/digital-human style, once set, is unaffected by this "
                "category's own provider, exactly as replacing an LLM leaves it "
                "unaffected",
            ),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "memory",
            list_backends=available_memories,
            active_key=lambda: settings.memory,
            build=lambda key, credential: build_memory(replace(settings, memory=key), "ecosystem-validate"),
            requires_credential=lambda key: False,
            dependents=(
                "every agent's own retained personal-context knowledge (see "
                "src/services/memory/) - switching this backend is provider "
                "replacement only; what any agent is allowed to remember "
                "remains each agent's own MemoryPolicy, entirely untouched by "
                "which storage backend is selected",
            ),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "environment_provider",
            list_backends=available_environment_providers,
            active_key=lambda: settings.environment_provider,
            build=lambda key, credential: build_environment_provider(
                replace(settings, environment_provider=key)
            ),
            requires_credential=lambda key: False,
            dependents=("every agent's presented scene/lighting description",),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            # Phase 11 closure: which engine runs a created artifact so that
            # what it actually did can be observed. A selectable axis like every
            # other, so the engine is replaceable through the ordinary governed
            # path and nothing has to be rewritten to change it.
            "artifact_runtime",
            list_backends=available_artifact_runtimes,
            active_key=lambda: settings.artifact_runtime,
            build=lambda key, credential: build_artifact_runtime(
                replace(settings, artifact_runtime=key)
            ),
            requires_credential=lambda key: False,
            dependents=("running a created artifact and reporting what it actually did",),
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "knowledge_provider",
            list_backends=available_knowledge_providers,
            active_key=lambda: settings.knowledge_provider,
            build=lambda key, credential: build_knowledge_provider(replace(settings, knowledge_provider=key)),
            requires_credential=lambda key: False,
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "live_info_provider",
            list_backends=available_live_info_providers,
            active_key=lambda: settings.live_info_provider,
            build=lambda key, credential: build_live_info_provider(
                replace(settings, live_info_provider=key)
            ),
            requires_credential=lambda key: False,
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "communication_provider",
            list_backends=available_communication_providers,
            active_key=lambda: settings.communication_provider,
            # Universal provider/connection management repair: this
            # `build` closure previously discarded `credential` entirely
            # (`build_communication_provider` had no parameter to give it
            # to). It is threaded through now, the identical
            # `_credentialed_account(key, credential)` seam
            # llm_provider/video_generation_provider/speech_provider
            # already use above - so `provider communication_provider
            # validate <key> <credential>` actually validates against
            # the credential given, never silently ignoring it.
            build=lambda key, credential: build_communication_provider(
                replace(settings, communication_provider=key),
                account=_credentialed_account(key, credential),
            ),
            # "null" is the only backend today and needs no credential;
            # any other name is a real, external backend and does - the
            # identical `key != "null"` rule llm_provider/
            # video_generation_provider already use, corrected from an
            # unconditional False that would have misreported every
            # future credentialed communication backend as free.
            requires_credential=lambda key: key != "null",
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "celebration_provider",
            list_backends=available_celebration_providers,
            active_key=lambda: settings.celebration_provider,
            build=lambda key, credential: build_celebration_provider(
                replace(settings, celebration_provider=key)
            ),
            requires_credential=lambda key: False,
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "calendar_event_provider",
            list_backends=available_calendar_event_providers,
            active_key=lambda: settings.calendar_event_provider,
            build=lambda key, credential: build_calendar_event_provider(
                replace(settings, calendar_event_provider=key)
            ),
            requires_credential=lambda key: False,
        )
    )

    registry.register(
        GenericCategoryAdapter(
            "document_rendering_provider",
            list_backends=available_document_rendering_backends,
            active_key=lambda: settings.document_rendering,
            build=lambda key, credential: build_document_rendering(
                replace(settings, document_rendering=key)
            ),
            # Neither backend needs a credential - "simple_pdf" is real,
            # local, deterministic rendering with zero external risk, the
            # identical reasoning celebration_provider/calendar_event_provider
            # above already state for their own real, credential-free
            # implementations.
            requires_credential=lambda key: False,
            dependents=("the artifact_production tool's document-artifact PDF export",),
        )
    )

    # Capabilities are not a "select one backend" axis - every listed
    # capability is simultaneously available, governed by
    # src/manager/capabilities.py's own routing, not by a single active
    # choice - so this adapter is discovery-only (no `build`, no
    # `active`), an honest reflection of what this category actually is
    # rather than a forced fit into the same shape as the others.
    registry.register(
        GenericCategoryAdapter(
            "capability",
            list_backends=available_capabilities,
            active_key=lambda: None,
            single_selection=False,
            selection_note="enable, disable, grant or revoke each one with 'capabilities'",
        )
    )

    return registry
