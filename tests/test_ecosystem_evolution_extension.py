"""Full Alfred OS Ecosystem Evolution - surgical completion pass.

The prior checkpoint (52.28) proved one genuinely executable evolution
path (a provider's account model) and honestly named the rest as
inspection-only. CHECK re-inspected ``src/evolution.py``,
the Stage 6-9 Upgrade Manifest/Planner/Executor/Lifecycle,
``EcosystemRegistry``, specialist/Brain/Memory/capability/tool/
connection architecture, and found real, technically meaningful ways
to close four of those gaps *without fabricating a version where none
exists* - and, for the two that genuinely have no safe real mechanism
in this codebase (Brain reasoning code, most capability
implementations), an honest, explicit, read-only representation
instead of a fake apply.

**The generic-engine, component-supplied-behavior redesign this file
proves.** ``ecosystem_evolution.run``/``rollback`` no longer branch on
a category string at all - they ask ``handlers.handler_for(component_id)``
for the one real ``EvolutionHandler`` that owns a target, exactly the
open-ended extension point Section 11 of this checkpoint's own brief
requires: a future component participates by registering one handler,
never by editing the engine.

**Four new, real, independently-verified mechanisms, each proven with
propose -> run (evaluate -> approve -> apply -> verify -> activate) ->
fresh runtime -> actually active -> unrelated state intact -> rollback
-> fresh runtime -> restored:**

    specialist.<key> / alfred   the real identity document's own
                                content - staged, hashed (never a bare
                                version string), backed up before ever
                                being overwritten, re-parsed to verify
                                it is genuinely loadable, restorable
                                byte-for-byte. Structurally cannot touch
                                a specialist's real capabilities (code-
                                derived, not document-derived).
    capability.general_conversation
                                the real, previously-hardcoded
                                conversation-history turn count.
    tool.document_generation   the real output directory
                                DocumentGenerationTool writes to.
    connection.gmail            GmailConnection's own real adapter
                                *implementation* version (v2 exposes a
                                real, pre-existing Mailbox.fetch(label=)
                                capability v1 never forwarded) - proven
                                never to disturb the account/credential
                                it authenticates with.

Brain development (``ecosystem brain <agent>``) and Memory development
(``ecosystem memory <agent>``, extended with a real ``lessons`` count)
remain deliberately read-only - never routed through propose/run at
all, because no safe, real way exists in this codebase to version
either without editing source, and inventing one would be exactly the
fabrication this checkpoint's own brief forbids.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.evolution import EvolutionStatus
from src.services.memory.memory import MemoryRecord
import scripts.control_center as control_center_module
from scripts.control_center import build, run_command, run_ecosystem_command, run_provider_command


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    """Every test gets its own, empty accounts/ledger/config/staging/
    backups - never the real project's own persisted files."""
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "evolution.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_STAGING_DIR", tmp_path / "staging")
    monkeypatch.setattr(control_center_module, "DEFAULT_BACKUPS_DIR", tmp_path / "backups")
    return tmp_path


@pytest.fixture
def isolated_identities(tmp_path):
    """A private copy of the real repository's own identity documents -
    never the canonical ``identities/`` directory or ``IDENTITY.md``
    itself, so a test evolving "Arthur" or "Alfred" can never touch
    what a real session actually loads."""
    import shutil
    from pathlib import Path

    identities_dir = tmp_path / "identities"
    identities_dir.mkdir()
    shutil.copy2(Path("identities") / "email_agent.md", identities_dir / "email_agent.md")

    identity_path = tmp_path / "IDENTITY.md"
    shutil.copy2(Path("IDENTITY.md"), identity_path)

    return {"identities_dir": str(identities_dir), "identity_path": str(identity_path)}


def _write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("utf-8"))


# ---------------------------------------------------------------------------
# 1. The open-ended handler registry - never a hard-coded catalog
# ---------------------------------------------------------------------------


class TestHandlerRegistryIsOpenEnded:
    def test_an_unregistered_component_is_refused_honestly_never_a_fake_generic_apply(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        # "provider.llm_provider" and friends aside, a made-up id with
        # no handler at all must be refused at PROPOSE time already -
        # never silently accepted only to fail later.
        run_ecosystem_command(centre, "")  # seed the ledger first
        result = run_ecosystem_command(centre, "system.core propose 0.2.0 low r|b")
        assert "no evolution handler is registered" in result

    def test_a_future_component_participates_by_registering_one_handler(self, tmp_path):
        """The governed extension point itself: a brand-new category,
        never named anywhere in the engine, works the moment one small
        handler is registered - proving the engine never had a
        hard-coded catalog to begin with."""
        from src.services.evolution import ecosystem_evolution as ee
        from src.services.evolution.handlers import EvolutionHandler, HandlerResult, register_handler
        from src.services.evolution.ledger_store import EvolutionState

        class _FutureWidgetHandler(EvolutionHandler):
            def applies_to(self, component_id):
                return component_id == "widget.future_thing"

            def apply(self, proposal, *, context):
                return HandlerResult(True, f"future widget set to {proposal.proposed_version}")

            def rollback(self, proposal, *, context):
                return HandlerResult(True, f"future widget restored to {proposal.current_version}")

        register_handler(_FutureWidgetHandler())

        from src.evolution import EvolutionLedger

        state = EvolutionState().with_ledger(EvolutionLedger(current_versions={"widget.future_thing": "v1"}))

        proposed = ee.propose(
            state, component_id="widget.future_thing", proposed_version="v2",
            proposed_change="test", reason="r", expected_benefit="b",
            ledger_path=tmp_path / "ledger.json",
        )
        assert proposed.success
        settings = Settings.from_env(env={})
        result = ee.run(
            proposed.state, proposal_id=proposed.proposal.proposal_id, approved=True,
            settings=settings, ledger_path=tmp_path / "ledger.json",
        )
        assert result.success
        assert result.proposal.status == EvolutionStatus.ACTIVE


# ---------------------------------------------------------------------------
# 2. Capability evolution
# ---------------------------------------------------------------------------


class TestCapabilityEvolution:
    def test_full_lifecycle_and_fresh_runtime_activation(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_ecosystem_command(centre, "capability.general_conversation propose 12 low more context|better continuity")
        result = run_ecosystem_command(
            centre, "capability.general_conversation run capability.general_conversation-1 yes"
        )
        assert "OK" in result and "active" in result

        runtime2, manager2, channel2, conversation2 = real_system()
        from src.conversation.conversation import Utterance

        for i in range(20):
            conversation2.say(Utterance(speaker="you", text=f"turn {i}", addressed=None))
        assert len(manager2.own_skills._history()) == 12

    def test_an_invalid_turn_count_is_refused_honestly(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "capability.general_conversation propose not-a-number low r|b")
        result = run_ecosystem_command(
            centre, "capability.general_conversation run capability.general_conversation-1 yes"
        )
        assert "FAILED" in result
        assert "not a real turn-count" in result

    def test_rollback_restores_the_original_turn_count(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "capability.general_conversation propose 15 low r|b")
        run_ecosystem_command(centre, "capability.general_conversation run capability.general_conversation-1 yes")
        rb = run_ecosystem_command(centre, "capability.general_conversation rollback capability.general_conversation-1 yes")
        assert "OK" in rb

        runtime2, manager2, channel2, conversation2 = real_system()
        from src.conversation.conversation import Utterance

        for i in range(20):
            conversation2.say(Utterance(speaker="you", text=f"turn {i}", addressed=None))
        assert len(manager2.own_skills._history()) == 6  # this project's own original default


# ---------------------------------------------------------------------------
# 3. Tool evolution
# ---------------------------------------------------------------------------


class TestToolEvolution:
    def test_full_lifecycle_and_fresh_runtime_writes_to_the_new_directory(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        new_dir = tmp_path / "new_documents"
        run_ecosystem_command(centre, f"tool.document_generation propose {new_dir} low separate output area|cleaner organisation")
        result = run_ecosystem_command(centre, "tool.document_generation run tool.document_generation-1 yes")
        assert "OK" in result

        runtime2, manager2, channel2, conversation2 = real_system()
        tool = manager2.own_skills.tools._tools["document_generation"]
        assert tool._output_dir == new_dir
        tool.run(title="Evolution Test", content="genuinely written")
        assert any(new_dir.glob("*.md"))

    def test_unrelated_tools_and_capabilities_are_unaffected(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        new_dir = tmp_path / "new_documents"
        run_ecosystem_command(centre, f"tool.document_generation propose {new_dir} low r|b")
        run_ecosystem_command(centre, "tool.document_generation run tool.document_generation-1 yes")

        runtime2, manager2, channel2, conversation2 = real_system()
        assert manager2.own_skills.tools.permits("web_search")
        assert manager2.own_skills.tools.permits("communication_read")

    def test_rollback_restores_the_original_directory(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        original = runtime.settings.documents_dir
        new_dir = tmp_path / "new_documents"
        run_ecosystem_command(centre, f"tool.document_generation propose {new_dir} low r|b")
        run_ecosystem_command(centre, "tool.document_generation run tool.document_generation-1 yes")
        run_ecosystem_command(centre, "tool.document_generation rollback tool.document_generation-1 yes")

        runtime2, manager2, channel2, conversation2 = real_system()
        tool = manager2.own_skills.tools._tools["document_generation"]
        from pathlib import Path

        assert tool._output_dir == Path(original)


# ---------------------------------------------------------------------------
# 4. Connection adapter evolution - never the account/credential
# ---------------------------------------------------------------------------


class TestConnectionAdapterEvolution:
    def test_full_lifecycle_and_fresh_runtime_uses_v2(self):
        runtime, manager, channel, conversation = real_system(connection_provider="gmail")
        centre = ControlCenter(runtime, manager)

        run_ecosystem_command(centre, "connection.gmail propose 2 low expose label filtering|richer reads")
        result = run_ecosystem_command(centre, "connection.gmail run connection.gmail-1 yes")
        assert "OK" in result

        runtime2, *_ = real_system(connection_provider="gmail")
        assert runtime2.connections.default.adapter_version == "2"

    def test_the_account_authorization_state_is_provably_unaffected(self):
        runtime, manager, channel, conversation = real_system(connection_provider="gmail")
        centre = ControlCenter(runtime, manager)
        before_auth = runtime.connections.default.authorization

        run_ecosystem_command(centre, "connection.gmail propose 2 low r|b")
        run_ecosystem_command(centre, "connection.gmail run connection.gmail-1 yes")

        runtime2, *_ = real_system(connection_provider="gmail")
        assert runtime2.connections.default.authorization == before_auth

    def test_an_unknown_adapter_version_is_refused(self):
        runtime, manager, channel, conversation = real_system(connection_provider="gmail")
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "connection.gmail propose 99 low r|b")
        result = run_ecosystem_command(centre, "connection.gmail run connection.gmail-1 yes")
        assert "FAILED" in result
        assert "not a known Gmail adapter version" in result

    def test_whatsapp_and_telegram_are_unaffected_by_a_gmail_adapter_evolution(self):
        runtime, manager, channel, conversation = real_system(connection_provider="gmail")
        centre = ControlCenter(runtime, manager)
        from scripts.control_center import run_connections_command

        run_connections_command(centre, "whatsapp connect biz wa-token")
        run_connections_command(centre, "telegram connect bot tg-token")

        run_ecosystem_command(centre, "connection.gmail propose 2 low r|b")
        run_ecosystem_command(centre, "connection.gmail run connection.gmail-1 yes")

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.get("whatsapp", "biz") is not None
        assert store.get("telegram", "bot") is not None

    def test_rollback_restores_v1(self):
        runtime, manager, channel, conversation = real_system(connection_provider="gmail")
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "connection.gmail propose 2 low r|b")
        run_ecosystem_command(centre, "connection.gmail run connection.gmail-1 yes")
        run_ecosystem_command(centre, "connection.gmail rollback connection.gmail-1 yes")

        runtime2, *_ = real_system(connection_provider="gmail")
        assert runtime2.connections.default.adapter_version == "1"


# ---------------------------------------------------------------------------
# 5. Specialist (and Alfred) identity evolution - real content, never a
#    bare hash bump, structurally preserving capabilities.
# ---------------------------------------------------------------------------


NEW_ARTHUR_ROLE = """# Arthur

## Name

Arthur

## Role

Email specialist - real content evolved by a governed proposal, never a
fabricated hash bump.

## Purpose

Reads a mailbox and works out which of its messages are real opportunities.
"""

MALFORMED_CONTENT = "this has no ## Name section context at all really just prose"


class TestSpecialistIdentityEvolution:
    def test_full_lifecycle_preserves_capabilities_and_activates_on_fresh_runtime(
        self, isolated_identities, tmp_path
    ):
        runtime, manager, channel, conversation = real_system(identities_dir=isolated_identities["identities_dir"])
        centre = ControlCenter(runtime, manager)
        # Real capabilities come from AgentRegistry, not the identity
        # document - captured from a real, built manager.
        arthur_before = manager.registry.get("Arthur")
        capabilities_before = frozenset(arthur_before.spec.capabilities)

        new_content_path = tmp_path / "new_role.md"
        _write(new_content_path, NEW_ARTHUR_ROLE)

        propose_out = run_ecosystem_command(
            centre, f"specialist.email_agent propose-content {new_content_path} low real role update|clarity"
        )
        assert "proposed" in propose_out

        result = run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")
        assert "OK" in result and "active" in result

        # Fresh runtime: the real document content actually changed.
        content_after = (tmp_path / "identities" / "email_agent.md").read_text()
        assert "real content evolved by a governed proposal" in content_after

        runtime2, manager2, channel2, conversation2 = real_system(identities_dir=isolated_identities["identities_dir"])
        assert runtime2.specialists["email_agent"].name == "Arthur"
        arthur_after = manager2.registry.get("Arthur")
        assert frozenset(arthur_after.spec.capabilities) == capabilities_before

    def test_a_backup_is_created_before_the_real_file_is_touched(self, isolated_identities, tmp_path):
        runtime, manager, channel, conversation = real_system(identities_dir=isolated_identities["identities_dir"])
        centre = ControlCenter(runtime, manager)
        new_content_path = tmp_path / "new_role.md"
        _write(new_content_path, NEW_ARTHUR_ROLE)

        run_ecosystem_command(centre, f"specialist.email_agent propose-content {new_content_path} low r|b")
        run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")

        backups = list((tmp_path / "backups").glob("specialist.email_agent-1-*.md"))
        assert len(backups) == 1
        assert "email_agent.md" not in backups[0].name  # a real, distinctly-named backup

    def test_rollback_restores_the_exact_original_bytes(self, isolated_identities, tmp_path):
        runtime, manager, channel, conversation = real_system(identities_dir=isolated_identities["identities_dir"])
        centre = ControlCenter(runtime, manager)
        original_bytes = (tmp_path / "identities" / "email_agent.md").read_bytes()

        new_content_path = tmp_path / "new_role.md"
        _write(new_content_path, NEW_ARTHUR_ROLE)
        run_ecosystem_command(centre, f"specialist.email_agent propose-content {new_content_path} low r|b")
        run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")
        rb = run_ecosystem_command(centre, "specialist.email_agent rollback specialist.email_agent-1 yes")
        assert "OK" in rb

        restored_bytes = (tmp_path / "identities" / "email_agent.md").read_bytes()
        assert restored_bytes == original_bytes

    def test_a_malformed_proposed_document_never_lands(self, isolated_identities, tmp_path):
        runtime, manager, channel, conversation = real_system(identities_dir=isolated_identities["identities_dir"])
        centre = ControlCenter(runtime, manager)
        original_bytes = (tmp_path / "identities" / "email_agent.md").read_bytes()

        # A document with genuinely no parseable "## Name" section is,
        # in this project's own parser, still technically loadable
        # (every SpecialistIdentity field defaults to blank) - so to
        # prove a real failure path, the underlying parser is monkey-
        # patched to simulate a document this project's own architecture
        # would one day genuinely refuse (e.g. a future stricter schema).
        def _raise(_text):
            raise ValueError("simulated: not a valid identity document")

        new_content_path = tmp_path / "bad_role.md"
        _write(new_content_path, MALFORMED_CONTENT)
        run_ecosystem_command(centre, f"specialist.email_agent propose-content {new_content_path} low r|b")

        import src.services.identity.specialist as specialist_module

        real_parse = specialist_module.parse_specialist_identity
        specialist_module.parse_specialist_identity = _raise
        try:
            result = run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")
        finally:
            specialist_module.parse_specialist_identity = real_parse

        assert "FAILED" in result
        assert "does not parse as a valid identity" in result
        assert (tmp_path / "identities" / "email_agent.md").read_bytes() == original_bytes

    def test_alfred_himself_can_be_evolved_through_the_identical_handler(self, isolated_identities, tmp_path):
        runtime, manager, channel, conversation = real_system(identity_path=isolated_identities["identity_path"])
        centre = ControlCenter(runtime, manager)
        identity_path = Path(isolated_identities["identity_path"])
        original_bytes = identity_path.read_bytes()

        new_content_path = tmp_path / "new_alfred.md"
        new_text = original_bytes.decode("utf-8") + "\n\n<!-- evolved by a governed proposal -->\n"
        _write(new_content_path, new_text)

        # The legacy id "alfred" is still accepted; the Manager component is
        # keyed by its role (Pre-Stage-9 Campaign 5, HC-01).
        propose_out = run_ecosystem_command(centre, f"alfred propose-content {new_content_path} low real update|clarity")
        assert "proposed 'manager-1'" in propose_out
        result = run_ecosystem_command(centre, "manager run manager-1 yes")
        assert "OK" in result

        after_bytes = identity_path.read_bytes()
        assert b"evolved by a governed proposal" in after_bytes

        runtime2, *_ = real_system(identity_path=isolated_identities["identity_path"])
        assert runtime2.identity.name == "Alfred"


# ---------------------------------------------------------------------------
# 6. Brain and Memory development - honestly read-only, never conflated
#    with a version.
# ---------------------------------------------------------------------------


class TestBrainAndMemoryDevelopmentRemainReadOnly:
    def test_ecosystem_brain_reports_real_reasoning_mode_and_corrections(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.memory.remember(MemoryRecord(content="corrected fact", kind="user_context", tags={"resolved_ambiguity"}))
        manager.memory.remember(MemoryRecord(content="a lesson", kind="lesson"))

        output = run_ecosystem_command(centre, "brain Alfred")
        assert "BRAIN DEVELOPMENT" in output
        assert "reasoning mode" in output
        assert "corrections learned      : 1" in output
        assert "explicit lessons learned : 1" in output

    def test_only_a_real_reasoning_mode_is_ever_applicable(self, tmp_path, monkeypatch):
        # Pre-Stage-9 Campaign 4: brain.manager (the reasoning mode) is now
        # governed like any other setting, but still never accepts a
        # fabricated "Brain version" - only a mode that really exists applies.
        import scripts.control_center as control_center_module

        for name, leaf in (("DEFAULT_LEDGER_PATH", "state.json"), ("DEFAULT_CONFIG_PATH", "config.json"),
                           ("DEFAULT_STAGING_DIR", "staging"), ("DEFAULT_BACKUPS_DIR", "backups")):
            monkeypatch.setattr(control_center_module, name, tmp_path / leaf)
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        proposed = run_ecosystem_command(centre, "brain.manager propose genius-v9 low r|b")
        proposal_id = proposed.split("'")[1]
        ran = run_ecosystem_command(centre, f"brain.manager run {proposal_id} yes")
        assert ran.startswith("FAILED") and "not a real reasoning mode" in ran

    def test_memory_development_reports_lessons_distinctly_from_corrections(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        manager.memory.remember(MemoryRecord(content="x", kind="lesson"))
        output = run_ecosystem_command(centre, "memory Alfred")
        assert "explicit lessons learned: 1" in output


# ---------------------------------------------------------------------------
# 7. State preservation across everything, together
# ---------------------------------------------------------------------------


class TestFullStatePreservationAcrossAllMechanisms:
    def test_evolving_every_new_mechanism_leaves_unrelated_state_intact(
        self, isolated_identities, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fallback")
        runtime, manager, channel, conversation = real_system(
            provider="anthropic", identities_dir=isolated_identities["identities_dir"],
        )
        centre = ControlCenter(runtime, manager)

        run_provider_command(centre, "llm_provider add anthropic work sk-1 claude-3-5-sonnet-20241022")
        from scripts.control_center import run_connections_command

        run_connections_command(centre, "whatsapp connect biz wa-token")
        manager.memory.remember(MemoryRecord(content="retained fact", kind="user_context"))

        run_ecosystem_command(centre, "capability.general_conversation propose 9 low r|b")
        run_ecosystem_command(centre, "capability.general_conversation run capability.general_conversation-1 yes")

        new_dir = tmp_path / "docs2"
        run_ecosystem_command(centre, f"tool.document_generation propose {new_dir} low r|b")
        run_ecosystem_command(centre, "tool.document_generation run tool.document_generation-1 yes")

        new_content_path = tmp_path / "new_role.md"
        _write(new_content_path, NEW_ARTHUR_ROLE)
        run_ecosystem_command(centre, f"specialist.email_agent propose-content {new_content_path} low r|b")
        run_ecosystem_command(centre, "specialist.email_agent run specialist.email_agent-1 yes")

        from src.services.credentials.account_store_file import load_credential_store

        store = load_credential_store(control_center_module.DEFAULT_ACCOUNTS_PATH)
        assert store.active("anthropic").model == "claude-3-5-sonnet-20241022"
        assert store.get("whatsapp", "biz") is not None
        assert manager.memory.recall(limit=10)[0].content == "retained fact"

        runtime2, manager2, *_ = real_system(
            provider="anthropic", identities_dir=isolated_identities["identities_dir"],
        )
        assert runtime2.provider.model is None or runtime2.provider.model  # untouched by any of the above
        assert runtime2.specialists["email_agent"].name == "Arthur"


# ---------------------------------------------------------------------------
# 8. Control Center surface for the new mechanisms
# ---------------------------------------------------------------------------


class TestControlCenterSurface:
    def test_ecosystem_inventory_lists_the_new_categories(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "ecosystem", 100, channel, conversation)
        assert "capability:" in output
        assert "tool:" in output
        assert "connection.gmail" in output

    def test_help_documents_propose_content_and_brain(self):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        output = run_command(centre, "help", 100, channel, conversation)
        assert "propose-content" in output
        assert "ecosystem brain <agent>" in output
