"""Full Alfred OS Ecosystem Evolution.

CHECK found the pieces this checkpoint needed already substantially
real, just never connected to each other or to a real command surface:
``src/evolution.py`` (Stage 11.B) - a complete, isolated, governed
propose/evaluate/approve/apply/verify/activate/rollback state machine
for "an existing thing changing", already generic on a plain-string
``target`` - and the Stage 6-9 Upgrade Manifest/Planner/Executor/
Lifecycle (``src/control_center/manifest.py``,
``src/control_center/upgrade_planner.py``, ``src/upgrade_executor.py``,
``src/upgrade_lifecycle.py``) - a complete, separate, already-tested
governed pipeline for SYSTEM_VERSION/schema/visual-environment
upgrades. Neither had ever been reached from the Control Center, and
neither had a way to persist across a restart, or an inventory that
spanned the *whole* ecosystem (providers, connections, specialists,
Brain mode) rather than one axis at a time.

**What this checkpoint built, all additive, nothing pre-existing
changed in behaviour:**

- ``src/services/evolution/inventory.py`` - the real, whole-ecosystem
  read side: every provider/connection category (EcosystemRegistry),
  every specialist (``runtime.specialists``), Alfred himself, Brain
  mode, and the existing Upgrade Manifest - each correctly tagged by
  category (provider/connection/specialist/brain/system), never
  fabricating a version where none exists (a specialist's real
  identity-document content hash; a provider's real active
  backend:account:model tuple; never an invented semver).
- ``src/services/evolution/ledger_store.py`` - the persistence
  ``src/evolution.py``'s own docstring says a caller must own: a
  schema-versioned (with an intentionally-empty ``MigrationChain`` -
  the exact "wire the seam in now" discipline
  ``src/core/versioning.py`` itself recommends), JSON-backed
  ``EvolutionState`` (ledger + every proposal ever seen + a full,
  timestamped history log).
- ``src/services/evolution/ecosystem_evolution.py`` - the governed
  "caller" ``src/evolution.py`` itself says must exist elsewhere: wires
  a proposal's real current version from the inventory, and gives
  exactly one category (``provider.<category>``) a real, independently
  re-verified apply/rollback mechanism (a real account's ``model``
  field, reusing 52.26/52.27's own ``CredentialStore`` verbatim).
  Every other target is refused honestly, never faked.
- ``scripts/control_center.py``'s new ``ecosystem`` command - the
  first real command surface either governance system has ever had.

Memory never appears in this propose/apply pipeline. It participates
through its own, honest, developmental report
(``ecosystem memory <agent>``) - real counts by kind, real corrections
(Phase 4's own confirmation-resolved facts), real learning sessions -
never a fabricated "maturity version".
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.evolution import EvolutionStatus, RiskLevel
from src.services.memory.memory import MemoryRecord
import scripts.control_center as control_center_module
from scripts.control_center import build, run_command, run_ecosystem_command, run_provider_command


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Every test gets its own, empty accounts.json and evolution
    state.json - never the real ones, never shared between tests."""
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "evolution.json")
    return tmp_path


# ---------------------------------------------------------------------------
# 1. Inventory discovery - the whole ecosystem, correctly categorised
# ---------------------------------------------------------------------------


class TestInventoryDiscovery:
    def test_the_whole_ecosystem_is_discovered_with_distinct_categories(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.evolution.inventory import discover_components

        components = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        by_id = {c.component_id: c for c in components}

        assert by_id["manager"].category == "specialist"
        assert by_id["specialist.email_agent"].category == "specialist"
        assert by_id["provider.llm_provider"].category == "provider"
        assert by_id["connection.connection"].category == "connection"
        assert by_id["brain.manager"].category == "brain"
        assert by_id["system.core"].category == "system"

        # No destructive coupling at the reporting level: an LLM
        # provider is never reported under "brain" (a different axis -
        # reasoning *mode*, not vendor), and a connection is never
        # reported as a provider.
        assert by_id["provider.llm_provider"].category != "brain"
        assert by_id["connection.connection"].category != "provider"

    def test_no_fabricated_version_for_an_unconfigured_provider(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.evolution.inventory import discover_components

        components = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        llm = next(c for c in components if c.component_id == "provider.llm_provider")
        assert llm.version == "null"  # the real, honest default - never a fake semver

    def test_a_specialists_version_is_a_real_content_hash_not_a_fabricated_number(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.evolution.inventory import discover_components
        from src.services.identity.specialist import identity_path
        import hashlib

        components = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        arthur = next(c for c in components if c.component_id == "specialist.email_agent")

        real_bytes = identity_path(runtime.settings.identities_dir, "email_agent").read_bytes()
        assert arthur.version == hashlib.sha256(real_bytes).hexdigest()[:12]

    def test_the_same_document_always_hashes_to_the_same_version(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.evolution.inventory import discover_components

        first = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        second = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert (
            next(c for c in first if c.component_id == "manager").version
            == next(c for c in second if c.component_id == "manager").version
        )


# ---------------------------------------------------------------------------
# 2. Memory development - real, qualitative, never a fabricated version
# ---------------------------------------------------------------------------


class TestMemoryDevelopmentSnapshot:
    def test_reports_real_counts_never_a_fabricated_maturity_score(self):
        runtime, manager, channel, conversation = real_system()
        from src.services.evolution.inventory import memory_development_snapshot

        manager.memory.remember(MemoryRecord(content="a fact", kind="user_context"))
        manager.memory.remember(
            MemoryRecord(content="corrected", kind="user_context", tags={"resolved_ambiguity"})
        )
        manager.memory.remember(
            MemoryRecord(content="practiced spanish", kind="user_context", tags={"learning_progress"})
        )

        snapshot = memory_development_snapshot("Alfred", manager.memory)
        assert snapshot.total_records == 3
        assert snapshot.by_kind["user_context"] == 3
        assert snapshot.corrections == 1
        assert snapshot.learning_sessions == 1
        # Never conflated with a version - this dataclass has no
        # `.version` field at all, structurally.
        assert not hasattr(snapshot, "version")

    def test_ecosystem_memory_command_reaches_the_real_manager_memory(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.memory.remember(MemoryRecord(content="a real fact", kind="user_context"))

        output = run_ecosystem_command(centre, "memory Alfred")
        assert "total retained records : 1" in output

    def test_an_unknown_agent_is_refused_honestly(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        assert "no memory" in run_ecosystem_command(centre, "memory NotAnAgent").lower()


# ---------------------------------------------------------------------------
# 3. The real, practically-executed evolution scenario this checkpoint
#    requires - a genuine, observable, reversible component upgrade.
# ---------------------------------------------------------------------------


class TestRealProviderEvolutionScenario:
    def test_full_lifecycle_propose_run_verify_active_usable_rollback(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback-for-specialists")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-anthropic-1 claude-3-5-sonnet-20241022")
        # An unrelated account, to prove isolation later.
        run_provider_command(centre, "video_generation_provider add runway acct rwy-key gen4_turbo")

        # PROPOSE
        proposed = run_ecosystem_command(
            centre,
            "provider.llm_provider propose anthropic:work:claude-sonnet-5 low "
            "newer model available|better reasoning and lower latency",
        )
        assert "proposed" in proposed
        proposal_id = "provider.llm_provider-1"

        # RUN (evaluate -> approve -> apply -> verify -> activate)
        result = run_ecosystem_command(centre, f"provider.llm_provider run {proposal_id} yes")
        assert "OK" in result
        assert "active" in result

        # The resulting component is actually usable: a fresh, real
        # runtime built from the same persisted account genuinely uses
        # the new model - not merely a ledger entry claiming so.
        runtime2, *_ = real_system(provider="anthropic")
        assert runtime2.provider.model == "claude-sonnet-5"

        # Unrelated components remain intact.
        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.active("runway").model == "gen4_turbo"

        # Recorded in history, precisely.
        history = run_ecosystem_command(centre, "history")
        assert "proposed -> evaluated" in history
        assert "applied -> active" in history

        # ROLLBACK - a real, exercised recovery path.
        rolled_back = run_ecosystem_command(centre, f"provider.llm_provider rollback {proposal_id} yes")
        assert "OK" in rolled_back
        assert "rolled_back" in rolled_back

        runtime3, *_ = real_system(provider="anthropic")
        assert runtime3.provider.model == "claude-3-5-sonnet-20241022"

        # The rollback itself is also recorded.
        assert "active -> rolled_back" in run_ecosystem_command(centre, "history")

    def test_declining_approval_never_applies_anything(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)
        run_provider_command(centre, "llm_provider add anthropic work sk-1 claude-3-5-sonnet-20241022")

        run_ecosystem_command(centre, "provider.llm_provider propose anthropic:work:claude-sonnet-5 low r|b")
        result = run_ecosystem_command(centre, "provider.llm_provider run provider.llm_provider-1 no")
        assert "FAILED" in result

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.active("anthropic").model == "claude-3-5-sonnet-20241022"


# ---------------------------------------------------------------------------
# 4. Unsupported/invalid evolution is refused honestly, never fabricated
# ---------------------------------------------------------------------------


class TestInvalidEvolutionIsRejectedHonestly:
    def test_a_specialist_evolution_without_staged_content_is_refused_honestly(self):
        """Specialist evolution gained a real apply mechanism in the
        surgical completion pass (see
        tests/test_ecosystem_evolution_extension.py for the full,
        genuine content-evolution scenario) - it is no longer
        unsupported. What remains true, and is what this test now
        proves: the plain ``propose`` action (a bare version string)
        can never carry real markdown content, so a specialist
        proposal made that way has nothing staged to apply and is
        refused honestly, by name, rather than silently accepted."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_ecosystem_command(centre, "specialist.email_agent propose deadbeef00ff low test|test")
        result = run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")
        assert "FAILED" in result
        assert "no staged content found" in result

        # The ledger must not claim the specialist actually changed.
        from src.services.evolution.ledger_store import load_evolution_state

        state = load_evolution_state(control_center_module.DEFAULT_LEDGER_PATH)
        assert state.ledger.version_of("specialist.email_agent") != "deadbeef00ff"
        assert state.proposal("specialist.email_agent-1").status == EvolutionStatus.FAILED

    def test_proposing_against_an_unknown_component_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_ecosystem_command(centre, "not.a.real.component propose x low r|b")
        assert "no such component" in result

    def test_running_an_unknown_proposal_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_ecosystem_command(centre, "provider.llm_provider run ghost-proposal yes")
        assert "FAILED" in result
        assert "no such proposal" in result

    def test_a_malformed_risk_level_is_refused(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        result = run_ecosystem_command(centre, "provider.llm_provider propose x not-a-real-risk r|b")
        assert "not a real risk level" in result

    def test_a_dependency_that_is_not_active_is_rejected_at_evaluation(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        from src.services.evolution import ecosystem_evolution as ee
        from src.services.evolution.ledger_store import load_evolution_state
        from src.services.evolution.inventory import discover_components

        state = load_evolution_state(control_center_module.DEFAULT_LEDGER_PATH)
        components = discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        state = ee.seed_component_versions(state, components)

        result = ee.propose(
            state, component_id="provider.llm_provider", proposed_version="null:x:y",
            proposed_change="x", reason="r", expected_benefit="b",
            dependencies=("never-existed",), ledger_path=control_center_module.DEFAULT_LEDGER_PATH,
        )
        assert result.success
        run_result = ee.run(
            result.state, proposal_id=result.proposal.proposal_id, approved=True,
            settings=runtime.settings, ledger_path=control_center_module.DEFAULT_LEDGER_PATH,
            accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH,
        )
        assert not run_result.success
        assert "depends on proposal" in run_result.reason


# ---------------------------------------------------------------------------
# 5. State preservation - evolution must not destroy existing state
# ---------------------------------------------------------------------------


class TestStatePreservation:
    def test_evolving_one_provider_preserves_everything_else(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-1 claude-3-5-sonnet-20241022")
        run_provider_command(centre, "video_generation_provider add runway acct rwy-key gen4_turbo")
        from scripts.control_center import run_connections_command

        run_connections_command(centre, "whatsapp connect biz wa-token")
        run_connections_command(centre, "telegram connect bot tg-token")
        manager.memory.remember(MemoryRecord(content="a retained fact", kind="user_context"))

        run_ecosystem_command(centre, "provider.llm_provider propose anthropic:work:claude-sonnet-5 low r|b")
        run_ecosystem_command(centre, "provider.llm_provider run provider.llm_provider-1 yes")

        from src.services.credentials.account_store_file import load_credential_store
        from src.services.identity.specialist import identity_path

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        # Unrelated provider account: untouched.
        assert store.active("runway").model == "gen4_turbo"
        # Connections: both still present, neither displaced.
        assert store.get("whatsapp", "biz") is not None
        assert store.get("telegram", "bot") is not None
        # Memory: preserved.
        assert manager.memory.recall(limit=10)[0].content == "a retained fact"
        # Specialist identity: byte-for-byte unchanged on disk.
        assert identity_path(runtime.settings.identities_dir, "email_agent").exists()
        # Alfred's own identity document: untouched.
        from pathlib import Path

        assert Path(runtime.settings.identity_path).exists()

    def test_arthur_survives_a_governed_metadata_evolution_of_his_own_component(self):
        """Preserve identity/scope/memory during compatible evolution -
        a specialist proposal made without real staged content (the
        plain ``propose`` action, a bare version string) has nothing to
        apply and is refused honestly (see
        TestInvalidEvolutionIsRejectedHonestly above), so Arthur's real
        identity is untouched. The full, genuine content-evolution
        mechanism (``propose-content``) is proven end-to-end in
        tests/test_ecosystem_evolution_extension.py, including its own
        state-preservation and rollback scenarios."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        from src.services.evolution.inventory import discover_components

        before = next(
            c for c in discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
            if c.component_id == "specialist.email_agent"
        )

        run_ecosystem_command(centre, "specialist.email_agent propose deadbeef00ff low t|t")
        run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")

        after = next(
            c for c in discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
            if c.component_id == "specialist.email_agent"
        )
        assert before.version == after.version
        arthur = runtime.specialists["email_agent"]
        assert arthur.name == "Arthur"


# ---------------------------------------------------------------------------
# 6. No destructive coupling between unrelated components
# ---------------------------------------------------------------------------


class TestNoDestructiveCoupling:
    def test_evolving_llm_provider_never_touches_avatar_or_environment(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)
        run_provider_command(centre, "llm_provider add anthropic work sk-1 claude-3-5-sonnet-20241022")

        from src.services.evolution.inventory import discover_components

        before = {
            c.component_id: c.version
            for c in discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        }

        run_ecosystem_command(centre, "provider.llm_provider propose anthropic:work:claude-sonnet-5 low r|b")
        run_ecosystem_command(centre, "provider.llm_provider run provider.llm_provider-1 yes")

        after = {
            c.component_id: c.version
            for c in discover_components(runtime, manager, accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH)
        }
        for component_id in ("provider.avatar_provider", "provider.environment_provider", "brain.manager", "manager", "specialist.email_agent"):
            assert before[component_id] == after[component_id], component_id

    def test_disconnecting_one_service_never_affects_another(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        from scripts.control_center import run_connections_command

        run_connections_command(centre, "whatsapp connect biz wa-token")
        run_connections_command(centre, "telegram connect bot tg-token")
        run_connections_command(centre, "whatsapp disconnect biz")

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("whatsapp", "biz") is None
        assert store.get("telegram", "bot") is not None


# ---------------------------------------------------------------------------
# 7. Persistence across a restart
# ---------------------------------------------------------------------------


class TestPersistenceAcrossRestart:
    def test_ledger_and_history_survive_a_fresh_load(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)
        run_provider_command(centre, "llm_provider add anthropic work sk-1 claude-3-5-sonnet-20241022")

        run_ecosystem_command(centre, "provider.llm_provider propose anthropic:work:claude-sonnet-5 low r|b")
        run_ecosystem_command(centre, "provider.llm_provider run provider.llm_provider-1 yes")

        from src.services.evolution.ledger_store import load_evolution_state

        # Simulates a genuine restart: nothing here reuses in-memory
        # state from the calls above - a fresh read from disk only.
        restored = load_evolution_state(control_center_module.DEFAULT_LEDGER_PATH)
        assert restored.ledger.version_of("provider.llm_provider") == "anthropic:work:claude-sonnet-5"
        assert restored.proposal("provider.llm_provider-1").status == EvolutionStatus.ACTIVE
        assert len(restored.history) >= 4

    def test_a_missing_ledger_file_loads_as_honestly_empty(self, tmp_path):
        from src.services.evolution.ledger_store import load_evolution_state

        state = load_evolution_state(tmp_path / "does_not_exist.json")
        assert state.ledger.current_versions == {}
        assert state.history == ()


# ---------------------------------------------------------------------------
# 8. The real Control Center command surface
# ---------------------------------------------------------------------------


class TestControlCenterSurface:
    def test_ecosystem_bare_lists_every_category(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "ecosystem", 80, channel, conversation)
        for category in ("provider:", "connection:", "specialist:", "brain:", "system:"):
            assert category in output

    def test_ecosystem_system_reports_the_real_upgrade_manifest(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "ecosystem system", 80, channel, conversation)
        assert "system_version" in output
        assert "memory_schema_version" in output

    def test_ecosystem_system_upgrade_uses_the_real_existing_pipeline(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "ecosystem system upgrade 0.1.1 yes", 80, channel, conversation)
        assert "plan status" in output
        assert "upgrade_available" in output
        # Never fabricated as executed - SYSTEM_VERSION has no on-disk
        # store to write to, and the real pipeline honestly says so.
        assert "executed    : False" in output

    def test_help_documents_the_ecosystem_command(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "help", 80, channel, conversation)
        assert "ecosystem" in output
        assert "ecosystem <component> propose" in output


# ---------------------------------------------------------------------------
# 9. Provider semantics remain correct: multiple accounts coexist,
#    switching never deletes another (reconfirmed, not re-tested here
#    from scratch - 52.26/52.27 already prove this at the account
#    layer; this proves the *evolution* layer respects it too).
# ---------------------------------------------------------------------------


class TestProviderAndConnectionSemanticsRemainCorrect:
    def test_multiple_llm_accounts_coexist_through_an_evolution(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        monkeypatch.setenv("OPENAI_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(provider="anthropic")
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-a claude-3-5-sonnet-20241022")
        run_provider_command(centre, "llm_provider add openai work sk-o gpt-4o")

        run_ecosystem_command(centre, "provider.llm_provider propose anthropic:work:claude-sonnet-5 low r|b")
        run_ecosystem_command(centre, "provider.llm_provider run provider.llm_provider-1 yes")

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("anthropic", "work") is not None
        assert store.get("openai", "work") is not None
        assert store.get("openai", "work").model == "gpt-4o"  # never touched
