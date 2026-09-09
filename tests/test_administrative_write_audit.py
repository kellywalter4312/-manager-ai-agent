"""Administrative-write audit trail (2026-09-08).

The gap the previous Control Center repair pass left behind, found during
its own completeness review: provider/connections/capabilities/ecosystem
writes reachable through Control Center were never recorded anywhere
durable - only a conversational consequential action (see
scripts/exchange.py) ever reached src.services.audit.action_log. This
closes that gap by reusing the identical store, at the one real place
both a terminal session (run_command) and the web UI (route_command, which
calls run_command for the same reason it already reuses
run_provider_command/run_connections_command/etc.) share - never a second
audit architecture, and never a second classification of "what counts as
an administrative write" (classify_administrative_write is also the
confirmation gate's own classifier).

Every test here proves *behaviour*: a real entry genuinely appears (or
genuinely does not) in the real, reloadable store after a real command
runs through the real dispatcher - never construction of ActionLogEntry
in isolation.
"""

from __future__ import annotations

import pytest

from src.config.settings import Settings
from src.control_center.observer import ControlCenter
import scripts.control_center as control_center_module
from scripts.control_center import (
    build,
    classify_administrative_write,
    run_capability_registry_command,
    run_command,
)
from scripts.serve_ui import route_command
from src.services.audit.action_log import ActionLogEntry, load_action_log, record_action


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """Every test gets its own accounts/config/action-log/services files
    - never the real ones, and never shared between tests."""
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    return tmp_path / "action_log.json"


class TestTheClassifierCatchesFailuresABlacklistMissed:
    """The exact reason a whitelist was built instead of a blacklist -
    pinned as its own test class so a future edit cannot silently regress
    back to the more fragile design."""

    def test_a_misdirected_connections_call_is_not_reported_as_success(self, _isolated_paths):
        """'connections <provider-backend> connect' is refused before it
        ever reaches the connect logic - the refusal text never says
        FAILED/unknown/no such/usage."""
        centre = ControlCenter(*real_system()[:2])
        from scripts.control_center import run_connections_command

        text = run_connections_command(centre, "anthropic connect acc key")
        assert "is a provider backend" in text
        assert not control_center_module._administrative_write_succeeded("connections", text)

    def test_gmails_own_connect_refusal_is_not_reported_as_success(self, _isolated_paths):
        centre = ControlCenter(*real_system()[:2])
        from scripts.control_center import run_connections_command

        text = run_connections_command(centre, "gmail connect acc key")
        assert "authenticates via OAuth" in text
        assert not control_center_module._administrative_write_succeeded("connections", text)

    def test_a_structural_fallback_refusal_is_not_reported_as_success(self, _isolated_paths):
        centre = ControlCenter(*real_system()[:2])
        text = run_capability_registry_command(centre, "disable open_conversation")
        assert "cannot be disabled" in text
        assert not control_center_module._administrative_write_succeeded("capabilities", text)

    def test_real_successes_are_still_recognised(self, _isolated_paths):
        centre = ControlCenter(*real_system()[:2])
        added = control_center_module.run_provider_command(
            centre, "llm_provider add anthropic acc key"
        )
        assert control_center_module._administrative_write_succeeded("provider", added)


class TestConfirmedWritesAreRecorded:
    def test_a_successful_provider_add_is_recorded(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(
            centre, "provider llm_provider add anthropic acc key", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )

        entries = load_action_log(_isolated_paths)
        assert len(entries) == 1
        assert entries[0].capability == "admin.provider"
        assert entries[0].succeeded is True
        assert entries[0].actor == "operator"

    def test_a_failed_write_is_recorded_as_failed_not_success(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(
            centre, "capabilities disable open_conversation", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )

        entries = load_action_log(_isolated_paths)
        assert len(entries) == 1
        assert entries[0].succeeded is False
        assert entries[0].capability == "admin.capabilities"

    def test_each_of_the_four_administrative_verbs_is_recorded(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "capabilities disable small_talk", 80, channel, conversation, action_log_path=_isolated_paths)
        run_command(centre, "provider llm_provider add anthropic acc key", 80, channel, conversation, action_log_path=_isolated_paths)
        run_command(centre, "connections whatsapp connect acc token", 80, channel, conversation, action_log_path=_isolated_paths)
        run_command(centre, "ecosystem provider.llm_provider run does-not-exist yes", 80, channel, conversation, action_log_path=_isolated_paths)

        entries = load_action_log(_isolated_paths)
        capabilities_recorded = {e.capability for e in entries}
        assert capabilities_recorded == {
            "admin.capabilities", "admin.provider", "admin.connections", "admin.ecosystem",
        }

    def test_no_secret_ever_appears_in_a_recorded_entry(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        secret = "sk-super-secret-value-should-never-be-stored"

        run_command(
            centre, f"provider llm_provider add anthropic acc {secret}", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )

        raw = _isolated_paths.read_text(encoding="utf-8")
        assert secret not in raw


class TestUnconfirmedOrReadOnlyRequestsNeverCreateRecords:
    def test_a_read_only_verb_creates_no_record(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "status", 80, channel, conversation, action_log_path=_isolated_paths)
        run_command(centre, "capabilities", 80, channel, conversation, action_log_path=_isolated_paths)
        run_command(centre, "provider", 80, channel, conversation, action_log_path=_isolated_paths)

        assert load_action_log(_isolated_paths) == ()

    def test_the_pending_confirmation_step_over_http_creates_no_record(self, _isolated_paths):
        """The decisive case: route_command's first call (before a token
        is supplied) must never reach run_command's own recording point -
        proven here against the real HTTP route, not just run_command in
        isolation."""
        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)

        status, body, _ = route_command(
            "provider llm_provider add anthropic acc key", None, system, action_log_path=_isolated_paths
        )
        assert status == 200

        assert load_action_log(_isolated_paths) == ()

    def test_never_confirming_leaves_no_record_even_after_other_activity(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)

        route_command("provider llm_provider add anthropic acc key", None, system, action_log_path=_isolated_paths)
        route_command("status", None, system, action_log_path=_isolated_paths)

        assert load_action_log(_isolated_paths) == ()

    def test_confirming_creates_exactly_one_record_not_two(self, _isolated_paths):
        """No duplicate records: the pending call must contribute none,
        and the executing call must contribute exactly one."""
        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)
        command = "provider llm_provider add anthropic acc key"

        import json as jsonlib

        _, pending_body, _ = route_command(command, None, system, action_log_path=_isolated_paths)
        token = jsonlib.loads(pending_body)["confirm_token"]
        route_command(command, token, system, action_log_path=_isolated_paths)

        assert len(load_action_log(_isolated_paths)) == 1


class TestTheServicesVerbUsesTheIdenticalGovernanceNoParallelSystem:
    """Open-ended service discovery (2026-09-09): 'services declare'/
    'forget' reuse classify_administrative_write/_record_if_administrative_write
    exactly like the four verbs above - proven here the same way, not
    assumed from shared code alone."""

    def test_discovering_a_service_is_never_recorded(self, _isolated_paths, monkeypatch):
        """The DISCOVER/INSPECT/VALIDATE half added this checkpoint
        (services <name> discover <url>) writes nothing and needs no
        confirmation - it must create no audit entry, the identical
        read-only guarantee 'status'/'history' already hold."""
        import json
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_, *a, **k):
                return json.dumps({
                    "operations": [{"name": "ping", "method": "GET", "path": "/ping", "requires_auth": False}],
                }).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_command(
            centre, "services weatherstub discover http://127.0.0.1:8901",
            80, channel, conversation, action_log_path=_isolated_paths,
        )

        assert "DISCOVERED 'weatherstub'" in result
        assert load_action_log(_isolated_paths) == ()

        from src.services.connections.service_registry import load_service_registry
        assert load_service_registry(control_center_module.DEFAULT_SERVICES_PATH) == {}

    def test_a_failed_discovery_is_also_never_recorded(self, _isolated_paths, monkeypatch):
        import urllib.error
        import urllib.request

        def failing_urlopen(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_command(
            centre, "services weatherstub discover http://127.0.0.1:8901",
            80, channel, conversation, action_log_path=_isolated_paths,
        )

        assert "could not discover" in result
        assert load_action_log(_isolated_paths) == ()

    def test_declaring_a_service_is_recorded(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(
            centre, "services weatherstub declare http://127.0.0.1:9911 ping:GET:/ping:open",
            80, channel, conversation, action_log_path=_isolated_paths,
        )

        entries = load_action_log(_isolated_paths)
        assert len(entries) == 1
        assert entries[0].capability == "admin.services"
        assert entries[0].succeeded is True

    def test_forgetting_a_service_is_recorded(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(
            centre, "services weatherstub declare http://127.0.0.1:9911 ping:GET:/ping:open",
            80, channel, conversation, action_log_path=_isolated_paths,
        )
        run_command(centre, "services weatherstub forget", 80, channel, conversation, action_log_path=_isolated_paths)

        entries = load_action_log(_isolated_paths)
        assert [e.capability for e in entries] == ["admin.services", "admin.services"]
        assert entries[-1].succeeded is True

    def test_a_malformed_declare_is_recorded_as_failed_not_success(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(
            centre, "services badservice declare not-a-real-url ping:GET:/ping:open",
            80, channel, conversation, action_log_path=_isolated_paths,
        )

        entries = load_action_log(_isolated_paths)
        assert len(entries) == 1
        assert entries[0].succeeded is False

    def test_a_bare_services_list_is_read_only_and_creates_no_record(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        run_command(centre, "services", 80, channel, conversation, action_log_path=_isolated_paths)

        assert load_action_log(_isolated_paths) == ()

    def test_declaring_over_http_requires_confirmation_first(self, _isolated_paths):
        """The decisive case, the identical shape
        test_the_pending_confirmation_step_over_http_creates_no_record
        above already proves for 'provider add' - route_command's first
        call, with no token, must never reach run_command's own
        recording point for 'services declare' either."""
        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)

        status, body, _ = route_command(
            "services weatherstub declare http://127.0.0.1:9911 ping:GET:/ping:open",
            None, system, action_log_path=_isolated_paths,
        )
        assert status == 200
        assert load_action_log(_isolated_paths) == ()

    def test_confirming_declares_the_service_and_creates_exactly_one_record(self, _isolated_paths):
        import json as jsonlib

        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)
        command = "services weatherstub declare http://127.0.0.1:9911 ping:GET:/ping:open"

        _, pending_body, _ = route_command(command, None, system, action_log_path=_isolated_paths)
        token = jsonlib.loads(pending_body)["confirm_token"]
        route_command(command, token, system, action_log_path=_isolated_paths)

        assert len(load_action_log(_isolated_paths)) == 1

        from src.services.connections.service_registry import load_service_registry

        registry = load_service_registry(control_center_module.DEFAULT_SERVICES_PATH)
        assert "weatherstub" in registry


class TestOperationLevelExecutionGovernance:
    """Generalized real-world action execution (2026-09-09):
    'services <name> execute <operation>' reuses the identical
    classify_administrative_write/_record_if_administrative_write pair
    every other verb here already does - a GET operation needs no
    confirmation (the same shape 'provider validate'/'connections
    status' already have); anything else does, and is durably recorded
    exactly like declare/forget above."""

    def _declare(self, centre, channel, conversation, action_log_path, *, auth_op=True):
        auth = "auth" if auth_op else "open"
        run_command(
            centre,
            f"services weatherstub declare http://127.0.0.1:9911 "
            f"ping:GET:/ping:open,place_order:POST:/orders:{auth}",
            80, channel, conversation, action_log_path=action_log_path,
        )

    def test_a_get_execute_needs_no_confirmation_and_creates_no_record(self, _isolated_paths, monkeypatch):
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"ok": true}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        self._declare(centre, channel, conversation, _isolated_paths)

        result = run_command(
            centre, "services weatherstub execute ping", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )
        assert "succeeded" in result

        # Only the (confirmed) declare above was recorded - the GET
        # execute itself, needing no confirmation, creates no record,
        # the identical "real but inert read" shape a validate call
        # already has.
        entries = load_action_log(_isolated_paths)
        assert [e.capability for e in entries] == ["admin.services"]

    def test_a_post_execute_requires_confirmation_first(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        self._declare(centre, channel, conversation, _isolated_paths)
        before = len(load_action_log(_isolated_paths))  # the declare above legitimately recorded one
        system = _live_system(runtime, manager, channel, conversation)

        status, body, _ = route_command(
            "services weatherstub execute place_order", None, system, action_log_path=_isolated_paths,
        )
        assert status == 200

        import json as jsonlib
        assert jsonlib.loads(body)["requires_confirmation"] is True
        # The pending (unconfirmed) execute call itself contributed no
        # new record - the count is exactly what the declare above left it at.
        assert len(load_action_log(_isolated_paths)) == before

    def test_confirming_a_post_execute_runs_it_for_real_and_records_exactly_one_entry(self, _isolated_paths, monkeypatch):
        import json as jsonlib
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"id": "order-1"}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        self._declare(centre, channel, conversation, _isolated_paths, auth_op=False)  # open, so no credential needed
        system = _live_system(runtime, manager, channel, conversation)
        command = "services weatherstub execute place_order"

        _, pending_body, _ = route_command(command, None, system, action_log_path=_isolated_paths)
        token = jsonlib.loads(pending_body)["confirm_token"]
        status, body, _ = route_command(command, token, system, action_log_path=_isolated_paths)

        assert status == 200
        entries = load_action_log(_isolated_paths)
        # Exactly one execute entry, alongside the one declare entry
        # from _declare above - the pending call contributed none.
        assert [e.capability for e in entries] == ["admin.services", "admin.services"]
        assert entries[-1].succeeded is True

    def test_an_unauthorized_operation_is_never_executed_and_never_recorded_as_success(self, _isolated_paths):
        """place_order requires a credential here (auth_op=True,
        default) and none was ever connected - the confirmed execute
        must still fail honestly, not fabricate success, and the
        failure itself is durably recorded as a failure."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        self._declare(centre, channel, conversation, _isolated_paths)  # auth_op=True (default)
        system = _live_system(runtime, manager, channel, conversation)
        command = "services weatherstub execute place_order"

        import json as jsonlib
        _, pending_body, _ = route_command(command, None, system, action_log_path=_isolated_paths)
        token = jsonlib.loads(pending_body)["confirm_token"]
        route_command(command, token, system, action_log_path=_isolated_paths)

        entries = load_action_log(_isolated_paths)
        assert entries[-1].succeeded is False

    def test_a_safe_operation_is_never_blocked_by_a_siblings_missing_credential(self, _isolated_paths, monkeypatch):
        """The actual finding this checkpoint fixes, proven through the
        real Control Center command surface: 'ping' (no auth) must
        still execute even though 'place_order' (auth required,
        default) has no credential on file for the same service."""
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"ok": true}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        self._declare(centre, channel, conversation, _isolated_paths)  # place_order still needs auth

        result = run_command(
            centre, "services weatherstub execute ping", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )
        assert "succeeded" in result
        assert "not authorized" not in result


class TestPersistenceAcrossRestart:
    def test_a_recorded_entry_survives_a_fresh_load(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(
            centre, "provider llm_provider add anthropic acc key", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )

        # A genuinely fresh read, the same way restarting the process would.
        reloaded = load_action_log(_isolated_paths)
        assert len(reloaded) == 1
        assert reloaded[0].capability == "admin.provider"


class TestConversationalAuditingStillWorksUnchanged:
    """Repair item #4's own requirement: this addition must not disturb
    the pre-existing conversational consequential-action recording."""

    def test_a_manually_recorded_conversational_entry_and_an_administrative_entry_coexist(self, _isolated_paths):
        record_action(
            ActionLogEntry(
                timestamp="2026-09-08T00:00:00+00:00",
                actor="Alfred",
                capability="communication",
                succeeded=True,
            ),
            path=_isolated_paths,
        )
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_command(
            centre, "provider llm_provider add anthropic acc key", 80, channel, conversation,
            action_log_path=_isolated_paths,
        )

        entries = load_action_log(_isolated_paths)
        assert len(entries) == 2
        assert {e.capability for e in entries} == {"communication", "admin.provider"}
        assert {e.actor for e in entries} == {"Alfred", "operator"}


class TestClassifyAdministrativeWriteIsSharedNotDuplicated:
    def test_serve_ui_and_control_center_use_the_identical_function_object(self):
        import scripts.serve_ui as serve_ui_module

        assert serve_ui_module.classify_administrative_write is classify_administrative_write


class TestEcosystemRejectionIsIntentionallyLighterThanApproval:
    """Final acceptance sweep (2026-09-08): "ecosystem <component> run <id>
    no" was flagged as a possible gap - it changes persisted state but
    is not classify_administrative_write-gated or action_log-recorded
    the way "run <id> yes" is. Investigated rather than reflexively
    "fixed": ecosystem_evolution.run() calls approve_proposal(approved=
    False), which src/evolution.py's own approve_proposal returns as
    ApprovalResult(False, "not approved", proposal) - the *same*,
    unmodified proposal object, still status EVALUATED. run() then stops
    immediately, before apply_proposal/handler.apply() ever runs - the
    one call in the whole pipeline capable of a real, live side effect
    (see run()'s own docstring: EVALUATE -> APPROVE -> APPLY -> VERIFY ->
    ACTIVATE, "stops, honestly, at the first stage that does not
    succeed"). EvolutionStatus.REJECTED exists and is used - but only by
    evaluate_proposal(), for a proposal the *system* determines is
    invalid/unsafe (see src/evolution.py's evaluate_proposal), never by a
    human declining approval. A human's "no" is "not yet approved", not
    "rejected" - reversible, not terminal.

    So "run ... no" never touches a credential, a capability, a provider
    account, or any live configuration - the exact category
    classify_administrative_write/action_log exists to gate and record.
    What it DOES persist - a HistoryEntry via ecosystem_evolution._record
    - lands in the evolution ledger's own comprehensive, purpose-built
    history (the same one evaluate/approve/apply/verify/activate already
    write to on every call, "run ... no" included), visible through
    "ecosystem history" - so the decision is not silently lost, just not
    duplicated into the separate, deliberately narrower action_log this
    checkpoint's own repair scoped to *consequential* writes only. This
    is Category E (cosmetic/non-consequential) - verified by reading the
    actual mechanism, not assumed - and correctly left outside the
    administrative confirmation/audit gate; forcing it through that gate
    would misrepresent a no-op as a live-changing write.
    """

    def test_a_rejection_is_never_classified_as_an_administrative_write(self):
        requires_confirmation, _ = classify_administrative_write(
            "ecosystem capability.general_conversation run some-id no"
        )
        assert requires_confirmation is False

    def test_rejecting_creates_no_action_log_entry(self, _isolated_paths):
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        from scripts.control_center import run_ecosystem_command

        run_ecosystem_command(centre, "")  # seed the ledger
        run_ecosystem_command(
            centre, "capability.general_conversation propose 12 low reason|benefit"
        )
        run_command(
            centre,
            "ecosystem capability.general_conversation run capability.general_conversation-1 no",
            80, channel, conversation, action_log_path=_isolated_paths,
        )

        assert load_action_log(_isolated_paths) == ()

    def test_rejecting_stops_before_any_real_change_and_leaves_status_evaluated(self, _isolated_paths):
        """The decisive safety proof: a real ecosystem_evolution.run()
        call with approved=False, inspected directly rather than through
        the string-returning command wrapper, proves the proposal's own
        status never left EVALUATED (apply_proposal/handler.apply() -
        the only step capable of a live side effect - was never reached)."""
        from src.evolution import EvolutionStatus
        from src.services.evolution import ecosystem_evolution
        from src.services.evolution.ledger_store import load_evolution_state

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        from scripts.control_center import run_ecosystem_command

        run_ecosystem_command(centre, "")
        run_ecosystem_command(
            centre, "capability.general_conversation propose 12 low reason|benefit"
        )

        ledger_path = control_center_module.DEFAULT_LEDGER_PATH
        state = load_evolution_state(ledger_path)
        result = ecosystem_evolution.run(
            state, proposal_id="capability.general_conversation-1", approved=False,
            settings=centre.runtime.settings, ledger_path=ledger_path,
            accounts_path=control_center_module.DEFAULT_ACCOUNTS_PATH,
            config_path=control_center_module.DEFAULT_CONFIG_PATH,
        )
        assert result.success is False
        assert result.proposal.status == EvolutionStatus.EVALUATED

    def test_the_proposal_remains_evaluated_and_can_still_be_approved_later(self, _isolated_paths):
        """The safety property that makes leaving this ungated correct:
        'no' is reversible, not a lockout - a later 'yes' for the exact
        same proposal id still genuinely applies it."""
        from scripts.control_center import run_ecosystem_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "")
        run_ecosystem_command(
            centre, "capability.general_conversation propose 12 low reason|benefit"
        )

        rejected = run_ecosystem_command(
            centre, "capability.general_conversation run capability.general_conversation-1 no"
        )
        assert "not approved" in rejected

        approved = run_ecosystem_command(
            centre, "capability.general_conversation run capability.general_conversation-1 yes"
        )
        assert "OK" in approved

    def test_the_rejection_is_still_durably_recorded_in_the_evolution_ledgers_own_history(self, _isolated_paths):
        """Nothing is silently lost - the decision lives in the
        evolution module's own comprehensive history, the same place
        every other lifecycle transition (evaluate/approve/apply/verify/
        activate) already lands, just not duplicated into action_log."""
        from scripts.control_center import run_ecosystem_command

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)
        run_ecosystem_command(centre, "")
        run_ecosystem_command(
            centre, "capability.general_conversation propose 12 low reason|benefit"
        )
        run_ecosystem_command(
            centre, "capability.general_conversation run capability.general_conversation-1 no"
        )

        history = run_ecosystem_command(centre, "history capability.general_conversation")
        assert "not approved" in history


def _live_system(runtime, manager, channel, conversation):
    from scripts.serve_ui import LiveSystem
    from src.control_center.observer import ControlCenter as _CC
    import threading

    return LiveSystem(
        runtime=runtime, manager=manager, channel=channel, conversation=conversation,
        centre=_CC(runtime, manager), lock=threading.Lock(),
    )
