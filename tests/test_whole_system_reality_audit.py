"""Whole-System Reality Audit — Full Brain / Half Brain + Control Center.

CHECK reconfirmed the entire system built across every prior checkpoint
this project's history records - Brain/Full Brain/Half Brain, LLM
providers, ProviderAccount/CredentialStore, the Universal Ecosystem
registry, Memory, Knowledge/Research/Live Information, Files (PDF/text/
spreadsheet/presentation), image analysis, media generation (narration/
image/video), Avatar, the Objective Loop, and the Control Center's own
terminal REPL - all genuinely real, reachable, and already covered by
their own prior checkpoints' focused tests. None of it was rebuilt.

**The one genuine orphan feature this audit found and fixed**:
`EnvironmentProvider`/`environment_provider` (built at the Universal
Ecosystem checkpoint) was a real, registered provider *category* with
no per-agent selection or persistence at all - exactly the gap
`AvatarProvider` itself had before Stage 17's own avatar-preference
work closed it. A user could see the environment *backends* available
(`provider environment_provider`) but had no way to actually choose
one for a given agent, and no such choice would have persisted even if
invented ad hoc. Closed by mirroring the avatar pattern exactly:
`environment_preference_for`/`set_environment_preference`/
`environment_for` (`src/services/identity/presence.py`), a new
`environment` terminal command (`scripts/control_center.py`), and a new
`POST /environment` HTTP route (`scripts/serve_ui.py`) - one governed
write path, three ways to reach it (two now; `avatar` already had two).

This file proves the fix, and reconfirms - through real, direct calls
into the actual production surfaces, not new unit-test doubles standing
in for them - that provider switching, credential isolation, and
Half-Brain honesty hold across categories this audit specifically
re-examined.
"""

from __future__ import annotations

import threading

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.providers.default_environment_provider import DefaultEnvironmentProvider
from src.services.identity.presence import (
    ENVIRONMENT_PREFERENCE_FACT_TYPE,
    environment_for,
    environment_preference_for,
    set_environment_preference,
)
from src.services.memory.ephemeral import EphemeralMemory
import scripts.control_center as control_center_module
from scripts.control_center import build, run_command, run_provider_command
from scripts.exchange import exchange
from scripts.serve_ui import LiveSystem, route_environment


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


# ---------------------------------------------------------------------------
# 1. The environment-preference mechanism itself
# ---------------------------------------------------------------------------


class TestEnvironmentPreferenceMechanism:
    def test_nothing_chosen_yet_falls_back_to_the_deterministic_default(self):
        provider = DefaultEnvironmentProvider()
        memory = EphemeralMemory()
        resolved = environment_for("Alfred", provider=provider, recall=memory.recall)
        assert resolved == provider.default_for("Alfred")

    def test_a_valid_choice_is_stored_and_resolved_thereafter(self):
        provider = DefaultEnvironmentProvider()
        memory = EphemeralMemory()
        ok = set_environment_preference(
            "Alfred", "technical_bay", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert ok
        assert environment_preference_for("Alfred", recall=memory.recall) == "technical_bay"
        assert environment_for("Alfred", provider=provider, recall=memory.recall).environment_id == "technical_bay"

    def test_an_invalid_choice_writes_nothing(self):
        provider = DefaultEnvironmentProvider()
        memory = EphemeralMemory()
        ok = set_environment_preference(
            "Alfred", "not_a_real_environment", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert not ok
        assert environment_preference_for("Alfred", recall=memory.recall) is None

    def test_a_second_choice_replaces_the_first_never_accumulating(self):
        provider = DefaultEnvironmentProvider()
        memory = EphemeralMemory()
        set_environment_preference(
            "Alfred", "quiet_study", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        set_environment_preference(
            "Alfred", "technical_bay", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        matches = memory.recall(kind="user_context", tags={ENVIRONMENT_PREFERENCE_FACT_TYPE})
        assert len(matches) == 1
        assert environment_preference_for("Alfred", recall=memory.recall) == "technical_bay"

    def test_two_agents_have_independent_environment_preferences(self):
        provider = DefaultEnvironmentProvider()
        alfred_memory = EphemeralMemory()
        arthur_memory = EphemeralMemory()
        set_environment_preference(
            "Alfred", "quiet_study", provider=provider,
            recall=alfred_memory.recall, remember=alfred_memory.remember, forget=alfred_memory.forget,
        )
        set_environment_preference(
            "Arthur", "technical_bay", provider=provider,
            recall=arthur_memory.recall, remember=arthur_memory.remember, forget=arthur_memory.forget,
        )
        assert environment_preference_for("Alfred", recall=alfred_memory.recall) == "quiet_study"
        assert environment_preference_for("Arthur", recall=arthur_memory.recall) == "technical_bay"

    def test_environment_choice_never_touches_avatar_or_digital_human_style(self):
        """The independence this audit's own Section 8 asks for,
        applied to a non-provider axis: choosing an environment must
        not appear as an avatar or profile-style record."""
        from src.providers.avatar import AvatarProfile
        from src.services.identity.presence import (
            avatar_preference_for,
            digital_human_style_for,
        )

        provider = DefaultEnvironmentProvider()
        memory = EphemeralMemory()
        set_environment_preference(
            "Alfred", "technical_bay", provider=provider,
            recall=memory.recall, remember=memory.remember, forget=memory.forget,
        )
        assert avatar_preference_for("Alfred", recall=memory.recall) is None
        assert all(v == "" for v in digital_human_style_for("Alfred", recall=memory.recall).values())


# ---------------------------------------------------------------------------
# 2. Reached through the real Control Center terminal command
# ---------------------------------------------------------------------------


class TestEnvironmentControlCenterCommand:
    def test_listing_shows_every_agents_real_current_environment(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "environment", 100, channel, conversation)
        assert "Alfred" in output and "Arthur" in output

    def test_choosing_and_relisting_reflects_the_real_change(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "environment Alfred technical_bay", 100, channel, conversation)
        assert "now technical_bay" in result

        listing = run_command(centre, "environment Alfred", 100, channel, conversation)
        assert "Technical Bay (technical_bay)" in listing

    def test_an_invalid_choice_is_refused_with_real_alternatives_named(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "environment Alfred not_a_real_environment", 100, channel, conversation)
        assert "no such environment" in result
        assert "quiet_study" in result

    def test_an_unregistered_agent_name_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_command(centre, "environment Nobody quiet_study", 100, channel, conversation)
        assert "no agent called" in result

    def test_the_choice_survives_a_genuine_restart(self, tmp_path):
        settings = Settings.from_env(env={}, memory="persistent", memory_dir=str(tmp_path))
        runtime, manager, channel, conversation = build(settings)
        centre = ControlCenter(runtime, manager)
        run_command(centre, "environment Alfred quiet_study", 100, channel, conversation)

        # A genuine restart: a brand-new process build against the same file.
        runtime2, manager2, channel2, conversation2 = build(settings)
        centre2 = ControlCenter(runtime2, manager2)
        listing = run_command(centre2, "environment Alfred", 100, channel2, conversation2)
        assert "Quiet Study (quiet_study)" in listing


# ---------------------------------------------------------------------------
# 3. Reached through the real HTTP route
# ---------------------------------------------------------------------------


class TestEnvironmentHttpRoute:
    def _system(self, runtime, manager, channel, conversation):
        centre = ControlCenter(runtime, manager)
        return LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=centre, lock=threading.Lock(),
        )

    def test_a_valid_choice_returns_200_and_persists(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_environment("Alfred", "open_workspace", system)
        assert status == 200
        assert b"open_workspace" in body

        memory = next(s for s in [manager] if True)  # Alfred is the Manager itself
        from scripts.exchange import memory_for

        stored = memory_for(manager, "Alfred")
        assert environment_preference_for("Alfred", recall=stored.recall) == "open_workspace"

    def test_an_invalid_choice_returns_400(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_environment("Alfred", "not-a-real-one", system)
        assert status == 400
        assert b"no such environment" in body

    def test_an_unknown_agent_returns_404(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, body, _ = route_environment("Nobody", "quiet_study", system)
        assert status == 404

    def test_a_blank_field_returns_400_before_touching_memory(self):
        runtime, manager, channel, conversation = real_system()
        system = self._system(runtime, manager, channel, conversation)
        status, _, _ = route_environment("", "quiet_study", system)
        assert status == 400
        status2, _, _ = route_environment("Alfred", "", system)
        assert status2 == 400


# ---------------------------------------------------------------------------
# 4. Spot-reconfirmation: Half Brain honesty across old and new
#    capabilities, and provider/credential isolation through the real
#    Control Center - not re-testing what prior checkpoints already
#    proved, only reconfirming the boundary holds together as one system.
# ---------------------------------------------------------------------------


class TestHalfBrainAcrossOldAndNewCapabilities:
    def test_deterministic_capabilities_still_work_with_no_brain_configured(self, tmp_path):
        pytest.importorskip(
            "kokoro_onnx",
            reason="kokoro_onnx is not installed; local narration cannot be verified",
        )
        runtime, manager, channel, conversation = real_system(
            media="local", media_asset_dir=str(tmp_path), artifacts_dir=str(tmp_path / "artifacts")
        )
        turn = exchange(channel, conversation, "my mom's birthday is March 24")
        assert turn.succeeded

        turn2 = exchange(channel, conversation, "narrate this: hello world")
        assert turn2.succeeded
        assert "Narration produced" in turn2.outcome

    def test_reasoning_dependent_capabilities_refuse_honestly_with_no_brain(self):
        runtime, manager, channel, conversation = real_system()
        turn = exchange(channel, conversation, "what is the capital of France")
        assert not turn.succeeded
        assert "no reasoning" in turn.outcome.lower() or "no language model" in turn.outcome.lower()


class TestProviderAndCredentialIsolationThroughControlCenter:
    def test_video_generation_accounts_stay_isolated_by_provider_through_the_real_command_surface(
        self, tmp_path, monkeypatch
    ):
        # Universal provider/connection management repair: accounts are
        # now persisted keyed by *backend* ("runway"/"veo"), never by
        # *category* ("video_generation_provider") - the key
        # build_media's own _build_video_generation reads
        # (account.provider decides runway vs veo). The old
        # category-keyed convention this test pinned could never have
        # actually reached a real construction.
        accounts_path = tmp_path / "accounts.json"
        monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", accounts_path)

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "video_generation_provider add runway runway-acct rwy-key gen4_turbo")
        run_provider_command(centre, "video_generation_provider add veo veo-acct veo-key veo-3.1-generate-preview")

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(accounts_path)
        assert store.get("runway", "runway-acct").model == "gen4_turbo"
        assert store.get("veo", "veo-acct").model == "veo-3.1-generate-preview"
        assert store.get("runway", "runway-acct").credential == "rwy-key"
        assert store.get("veo", "veo-acct").credential == "veo-key"

        run_provider_command(centre, "video_generation_provider select runway runway-acct")
        assert load_credential_store(accounts_path).active("runway").account_id == "runway-acct"

        run_provider_command(centre, "video_generation_provider revoke veo veo-acct")
        after = load_credential_store(accounts_path)
        assert after.get("veo", "veo-acct") is None
        assert after.get("runway", "runway-acct") is not None
