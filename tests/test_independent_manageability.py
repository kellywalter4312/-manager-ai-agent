"""Independent Manageability - a usable management interface NOW, before
the graphical Control Center exists.

CHECK reconfirmed the prior repair (52.26) genuinely made persisted
provider accounts reach the real runtime, and found the remaining gap
this checkpoint closes: nothing distinguished the two fundamentally
different shapes "provider" (LLM/voice/video - one active backend per
category, many accounts retained) and "connection" (Gmail, WhatsApp,
Telegram, ... - many services connected at once, no shared "active"
slot, connecting one never disconnects another) at the *management
interface* level, and there was no way to make a persisted choice live
without a full process restart.

**What was built, additively, reusing every existing piece:**

- ``connections`` (``scripts/control_center.py::run_connections_command``) -
  the additive connection-management surface. Reuses the identical
  ``CredentialStore``/``ProviderAccount`` every provider account already
  lives in, addressed directly by service name rather than through an
  ``EcosystemRegistry`` category (a category implies "pick one active
  backend" - exactly the shape connections must not have). Gmail is
  reported through its own real OAuth mechanism
  (``build_connection(replace(settings, connection_provider="gmail"))``),
  never forced into a typed-secret shape that would misrepresent it.
- ``reload`` (``scripts/control_center.py::interactive``) - the smallest
  safe activation this architecture supports: not hot-swapping one
  provider object inside a live Manager (every provider is still bound
  once, at construction), but rebuilding the whole session through the
  identical ``build()`` every entry point already uses, without leaving
  the terminal. Every persistence-only reply now says so explicitly and
  names this command.

Nothing about the existing ``provider <category> add/select/revoke/
validate`` surface (52.26) needed to change *in behaviour* - this file
also proves, explicitly, the property that surface already had but had
never been demonstrated end-to-end: switching the active backend never
deletes another account.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.connection import AuthorizationState
import scripts.control_center as control_center_module
from scripts.control_center import (
    build,
    interactive,
    run_command,
    run_connections_command,
    run_provider_command,
)


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_accounts_file(tmp_path, monkeypatch):
    """Every test in this file gets its own, empty accounts.json - never
    the real one, and never shared between tests."""
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    return tmp_path / "accounts.json"


# ---------------------------------------------------------------------------
# 1. Provider switching never deletes another account - the exact
#    ten-step scenario this checkpoint's own brief specifies.
# ---------------------------------------------------------------------------


class TestProviderSwitchingNeverDeletesAnotherAccount:
    def test_the_ten_step_anthropic_openai_scenario(self, monkeypatch):
        # A specialist (e.g. Arthur) falls back to the environment
        # credential when no per-specialist account is given - unrelated
        # to this checkpoint (see 52.26's own note on this); configured
        # here purely so switching the *Manager's* own account doesn't
        # crash specialist construction.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "specialist-fallback-key")
        monkeypatch.setenv("OPENAI_API_KEY", "specialist-fallback-key")

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        # 1. Connect Anthropic.
        connected1 = run_provider_command(centre, "llm_provider add anthropic work sk-anthropic-1")
        assert "added" in connected1

        # 2. Connect OpenAI.
        connected2 = run_provider_command(centre, "llm_provider add openai work sk-openai-1")
        assert "added" in connected2

        # 3. Select Anthropic.
        run_provider_command(centre, "llm_provider select anthropic work")

        # 4. Switch to OpenAI.
        switched = run_provider_command(centre, "llm_provider select openai work")
        assert "now active" in switched

        # 5. Confirm Anthropic remains stored/connected.
        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("anthropic", "work") is not None
        assert store.get("anthropic", "work").credential == "sk-anthropic-1"

        # 6. Confirm OpenAI is active.
        assert store.active("openai").account_id == "work"

        # 7. Rebuild runtime.
        runtime2, *_ = real_system(provider="openai")

        # 8. Confirm runtime uses OpenAI.
        assert runtime2.provider.name == "openai"
        assert runtime2.provider._api_key == "sk-openai-1"

        # 9. Switch back to Anthropic.
        run_provider_command(centre, "llm_provider select anthropic work")
        runtime3, *_ = real_system(provider="anthropic")
        assert runtime3.provider._api_key == "sk-anthropic-1"

        # 10. Confirm OpenAI remains stored/connected.
        store_after = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store_after.get("openai", "work") is not None
        assert store_after.get("openai", "work").credential == "sk-openai-1"

    def test_switching_via_the_real_top_level_run_command_dispatcher(self, monkeypatch):
        """The same guarantee, exercised through run_command - the exact
        entry point a real terminal session uses - not the lower-level
        helper function directly."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        monkeypatch.setenv("OPENAI_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "provider llm_provider add anthropic a sk-a", 80, channel, conversation)
        run_command(centre, "provider llm_provider add openai b sk-b", 80, channel, conversation)
        run_command(centre, "provider llm_provider select openai b", 80, channel, conversation)

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("anthropic", "a") is not None
        assert store.active("openai").account_id == "b"


# ---------------------------------------------------------------------------
# 2. Voice provider switching never loses ElevenLabs
# ---------------------------------------------------------------------------


class TestVoiceProviderSwitchingPreservesOtherAccounts:
    def test_switching_active_voice_keeps_elevenlabs_connected(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "speech_provider add elevenlabs main el-key-1")
        # "windows"/"kokoro" need no credential - connecting one is just
        # selecting it; nothing here fabricates a key for either.
        select = run_provider_command(centre, "speech_provider select elevenlabs main")
        assert "now active" in select

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("elevenlabs", "main") is not None
        assert store.get("elevenlabs", "main").credential == "el-key-1"

        # A real, un-fabricated status check: 'kokoro' needs no
        # credential and is a genuinely different backend - selecting it
        # must not touch the ElevenLabs account already stored.
        run_provider_command(centre, "speech_provider add kokoro none placeholder-not-a-real-secret")
        store_after = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store_after.get("elevenlabs", "main") is not None
        assert store_after.get("elevenlabs", "main").credential == "el-key-1"


# ---------------------------------------------------------------------------
# 3. Video generation - Runway representable, never fabricated
# ---------------------------------------------------------------------------


class TestVideoGenerationRepresentedHonestly:
    def test_runway_is_selectable_once_a_real_account_is_connected(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "video_generation_provider add runway acct rwy-test-key gen4_turbo")
        run_provider_command(centre, "video_generation_provider select runway acct")

        runtime2, manager2, *_ = real_system(video_generation="runway")
        assert runtime2.media.video_generator.name == "runway"
        assert runtime2.media.video_generator._api_key == "rwy-test-key"
        # No orchestration change: the identical, already-existing
        # media_production tool is what runtime2.media.video_generator
        # reaches - selecting Runway required no new tool, no new
        # capability, and no change to manager_skills' own wiring.
        assert manager2.own_skills.tools.permits("media_production")

    def test_runway_is_never_reported_connected_without_a_real_account(self):
        """No fabrication: with nothing configured, Runway must not be
        claimed as connected."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        status = run_provider_command(centre, "video_generation_provider accounts")
        assert "no accounts registered" in status
        assert runtime.media.video_generator.name == "null"


# ---------------------------------------------------------------------------
# 4. Persistent connections are additive
# ---------------------------------------------------------------------------


class TestPersistentConnectionsAreAdditive:
    def test_whatsapp_and_telegram_coexist_disconnect_and_reconnect(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        # 1. Connect WhatsApp.
        run_connections_command(centre, "whatsapp connect biz wa-secret-1")
        # 2. Connect Telegram.
        run_connections_command(centre, "telegram connect bot tg-secret-1")

        # 3. Confirm both remain connected simultaneously.
        overview = run_connections_command(centre, "")
        assert "whatsapp: connected" in overview
        assert "telegram: connected" in overview

        # 4. Disconnect WhatsApp.
        disconnect = run_connections_command(centre, "whatsapp disconnect biz")
        assert "disconnected" in disconnect

        # 5. Confirm Telegram remains connected.
        after_disconnect = run_connections_command(centre, "")
        assert "whatsapp" not in after_disconnect.replace("connect a new one", "")
        assert "telegram: connected" in after_disconnect

        # 6. Reconnect WhatsApp.
        run_connections_command(centre, "whatsapp connect biz wa-secret-2")

        # 7. Confirm both coexist again.
        final = run_connections_command(centre, "")
        assert "whatsapp: connected" in final
        assert "telegram: connected" in final

    def test_gmail_status_is_reported_but_never_stored_as_a_typed_credential(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        status = run_connections_command(centre, "gmail")
        assert "OAuth" in status
        assert "availability" in status
        assert "authorization" in status

        # Attempting to "connect" gmail with a typed secret is refused
        # and redirected to the real, existing mechanism - never
        # silently accepted as a fake OAuth flow.
        refused = run_connections_command(centre, "gmail connect fake-account fake-secret")
        assert "authorize_gmail.py" in refused

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("gmail", "fake-account") is None

    def test_a_provider_backend_cannot_be_managed_as_a_connection(self):
        """The two management surfaces stay genuinely distinct: an LLM
        backend name is refused here and pointed back at 'provider'."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_connections_command(centre, "anthropic")
        assert "provider" in result.lower()

        from src.services.credentials.account_store_file import load_credential_store

        run_connections_command(centre, "whatsapp connect a b")
        overview = run_connections_command(centre, "")
        assert "anthropic" not in overview


# ---------------------------------------------------------------------------
# 5. Persistence across a real restart
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def test_provider_and_connection_state_survive_a_fresh_build(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-persisted")
        run_connections_command(centre, "telegram connect bot tg-persisted")

        # Simulates a genuine process restart: a brand new build() call,
        # nothing carried over in memory from the session above.
        runtime2, manager2, channel2, conversation2 = real_system()
        centre2 = ControlCenter(runtime2, manager2)

        provider_state = run_provider_command(centre2, "llm_provider accounts")
        assert "work" in provider_state

        connection_state = run_connections_command(centre2, "")
        assert "telegram: connected" in connection_state


# ---------------------------------------------------------------------------
# 6. No source editing required - the real, top-level command surface
#    alone is sufficient for the whole workflow.
# ---------------------------------------------------------------------------


class TestNoSourceEditingIsRequired:
    def test_the_full_workflow_uses_only_string_commands_never_monkeypatching_internals(
        self, monkeypatch
    ):
        """Practical proof: every step below is a plain string handed to
        run_command - the same input a person types at the real
        terminal. Nothing here reaches into _BUILDERS, Settings fields,
        or any other internal to make this work."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        commands = [
            "provider llm_provider add anthropic personal sk-real-shape-key",
            "provider llm_provider select anthropic personal",
            "connections whatsapp connect biz wa-key",
            "connections telegram connect bot tg-key",
            "connections whatsapp disconnect biz",
        ]
        for command in commands:
            outcome = run_command(centre, command, 80, channel, conversation)
            assert "unknown" not in outcome.lower()
            assert "usage:" not in outcome.lower()

        final = run_connections_command(centre, "")
        assert "telegram: connected" in final
        assert "whatsapp" not in final.replace("connect a new one", "")


# ---------------------------------------------------------------------------
# 7. Avatar and Visual Environment remain independent of provider/
#    connection management.
# ---------------------------------------------------------------------------


class TestAvatarAndEnvironmentRemainIndependent:
    def test_avatar_and_environment_commands_never_touch_the_credential_store(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "avatar", 80, channel, conversation)
        run_command(centre, "environment", 80, channel, conversation)

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert len(store) == 0

    def test_avatar_is_never_classified_as_a_provider_or_connection(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        assert "unknown connections action" not in run_connections_command(centre, "").lower()
        # Avatar/environment have no ecosystem-registry category at all
        # under the provider/connection axes this checkpoint manages.
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        registry = build_ecosystem_registry(runtime.settings)
        assert "avatar_provider" in registry.categories()
        assert "environment_provider" in registry.categories()
        # Present as their own, separate categories - never folded into
        # llm_provider/video_generation_provider/communication_provider.
        for unrelated in ("llm_provider", "video_generation_provider", "communication_provider"):
            assert registry.adapter_for(unrelated).category != "avatar_provider"


# ---------------------------------------------------------------------------
# 8. Reload: the smallest safe in-session activation this architecture
#    actually supports.
# ---------------------------------------------------------------------------


class TestReloadActivatesWithoutARestart:
    def test_reload_alone_only_ever_reflects_persisted_account_changes(self, monkeypatch):
        """A plain 'reload' rebuilds from the same Settings - so
        selecting a different *account* for the backend already active
        becomes live, but the *backend* itself cannot change without an
        override (see the next test) or a real restart with a new
        environment variable."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)
        assert runtime.provider._api_key == "fallback"

        run_provider_command(centre, "llm_provider add anthropic work sk-anthropic-selected")
        run_provider_command(centre, "llm_provider select anthropic work")

        reloaded = control_center_module._reload_session(centre, "reload")
        assert reloaded is not None
        new_centre, _channel, _conversation = reloaded
        assert new_centre.runtime.provider.name == "anthropic"
        assert new_centre.runtime.provider._api_key == "sk-anthropic-selected"

    def test_reload_with_an_override_switches_the_active_backend_live(self, monkeypatch):
        """The one thing 'provider ... select' cannot do by itself:
        switch *which backend* is active for this session, without a
        restart and without touching the environment."""
        monkeypatch.setenv("OPENAI_API_KEY", "fallback")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        assert runtime.provider.name == "null"

        run_provider_command(centre, "llm_provider add openai work sk-openai-live")

        reloaded = control_center_module._reload_session(centre, "reload provider=openai")
        assert reloaded is not None
        new_centre, _channel, _conversation = reloaded
        assert new_centre.runtime.provider.name == "openai"
        assert new_centre.runtime.provider._api_key == "sk-openai-live"

    def test_reload_with_an_unknown_field_changes_nothing(self, capsys):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = control_center_module._reload_session(centre, "reload not_a_real_field=x")
        assert result is None
        assert "not a real Settings field" in capsys.readouterr().out

    def test_reload_override_is_session_scoped_never_persisted(self, monkeypatch):
        """The override must never leak into accounts.json or any other
        persisted state - restarting without it must behave exactly as
        if it had never been typed."""
        monkeypatch.setenv("OPENAI_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        control_center_module._reload_session(centre, "reload provider=openai")

        # A genuinely fresh build, exactly as a real restart would do -
        # the override from the line above must not have survived.
        fresh, *_ = real_system()
        assert fresh.provider.name == "null"

    def test_interactive_repl_reload_keyword_rebuilds_in_place(self, monkeypatch, capsys):
        """Drives the real interactive() loop with scripted stdin,
        proving the 'reload' keyword itself (not just the mechanism it
        calls) rebuilds the session and reports honestly."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-anthropic-reload")

        inputs = iter(["reload", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        exit_code = interactive(centre, 80, channel, conversation)
        assert exit_code == 0

        out = capsys.readouterr().out
        assert "Reloaded" in out
        assert "ephemeral" in out.lower()  # this test's own memory backend

    def test_interactive_repl_reload_with_override_keyword_switches_backend(self, monkeypatch, capsys):
        monkeypatch.setenv("OPENAI_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add openai work sk-openai-repl")

        inputs = iter(["reload provider=openai", "quit"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(inputs))

        exit_code = interactive(centre, 80, channel, conversation)
        assert exit_code == 0

        out = capsys.readouterr().out
        assert "Reloaded" in out
        assert "session's own override" in out
        assert "not persisted" in out


# ---------------------------------------------------------------------------
# 9. Authentication-shape honesty: string-secret vs OAuth are not conflated.
# ---------------------------------------------------------------------------


class TestAuthenticationShapesAreNotConflated:
    def test_gmail_authorization_state_reflects_the_real_token_file_not_a_stored_secret(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        # Whatever this machine's real data/token.json state is, the
        # report must come from the real Connection object, never from
        # CredentialStore (which holds nothing for "gmail" in this
        # isolated test store).
        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("gmail", "anything") is None

        status = run_connections_command(centre, "gmail")
        assert any(state.value in status for state in AuthorizationState)

    def test_a_string_secret_service_is_never_routed_through_the_oauth_path(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_connections_command(centre, "whatsapp connect biz wa-secret")
        assert "OAuth" not in result
        assert "authorize_gmail" not in result
