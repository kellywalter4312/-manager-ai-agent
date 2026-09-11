"""Closes the one remaining Stage 16 gap: a real, active, authorized
connection was never *reachable* through Alfred's own natural-language
path, because nothing in the real composition root ever granted a
``ConnectionActionTool`` for any of its operations - see
``scripts/control_center.py``'s own ``_connection_tools`` docstring for
the full "why".

**Phase 6 (real-world services & transactions) follow-up.** A
consequential operation's tool is now genuinely granted too (the
identical unconditional grant ``CommunicationSendTool``/``CallTool``
already have) - what stayed exactly where it was is the safety
boundary itself: ``connected_action`` never executes a consequential
operation on the strength of one remark alone, it raises a real,
resolvable ``PendingConfirmation`` first (the identical mechanism a
send/call already goes through), and only a genuinely clear "yes" on
the very next remark reaches real execution. Control Center's own
``services <name> execute`` remains a second, independent, equally
governed typed-command path to the identical execution - administrative
and audited by its own confirm_token - never replaced by this one.

Every test here builds the real production system through
``scripts.control_center.build`` (the identical function
``scripts/serve_ui.py`` itself calls) - never a manually-injected
``ToolSet`` standing in for it - so a pass here is proof the real
wiring works, not proof a test double works.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

import scripts.control_center as control_center_module
import src.services.connections.service_registry as service_registry_module
import src.services.credentials.account_store_file as account_store_file_module
from scripts.control_center import build, run_command
from src.agents.tools.connection import tool_name_for
from src.config.settings import Settings
from src.control_center.observer import ControlCenter
from src.core.contracts import Task

def _intent_task(words: str) -> Task:
    """A connected-account request that also carries real intent beyond
    the bare routing marker - the words after the colon are what
    ``_select_operation`` (src/manager/capabilities.py) actually matches
    against each operation's own declared name."""
    return Task(description=f"check my connected account: {words}")


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


@pytest.fixture(autouse=True)
def _isolated_paths(tmp_path, monkeypatch):
    """Isolates every file this checkpoint's path touches - including,
    critically, the two ``connection_factory.py`` reads *directly off
    their owning module* (``service_registry_module``/
    ``account_store_file_module``) rather than through
    ``control_center_module``'s own copied name, so a service declared
    through Control Center is genuinely what a freshly-built system
    selects as its active connection."""
    monkeypatch.setattr(control_center_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_LEDGER_PATH", tmp_path / "ledger.json")
    monkeypatch.setattr(control_center_module, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    monkeypatch.setattr(service_registry_module, "DEFAULT_SERVICES_PATH", tmp_path / "services.json")
    monkeypatch.setattr(account_store_file_module, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
    return tmp_path / "action_log.json"


def _declare_and_credential(action_log_path, *, operations: str, credential: "str | None" = None):
    """Declares 'fixturesvc' through the real, confirmed-at-the-
    terminal-layer Control Center command, then (optionally) connects a
    real credential - exactly the two real writes the live manual
    verification performs by hand through curl."""
    runtime, manager, channel, conversation = real_system()
    centre = ControlCenter(runtime, manager)
    run_command(
        centre, f"services fixturesvc declare http://127.0.0.1:9199 {operations}",
        80, channel, conversation, action_log_path=action_log_path,
    )
    if credential is not None:
        run_command(
            centre, f"connections fixturesvc connect testaccount {credential}",
            80, channel, conversation, action_log_path=action_log_path,
        )


class TestASafeOperationIsReachableThroughNaturalLanguage:
    """The actual gap this checkpoint closes: a real, selected,
    authorized connection's safe operation is now genuinely reachable
    through ordinary conversation - never through a manually-injected
    test tool."""

    def test_reading_a_declared_services_safe_operation_through_plain_conversation(self, _isolated_paths, monkeypatch):
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
        )

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"items": [], "count": 0}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        # The "restart with the provider selected" step of the live
        # verification, reproduced here as "build a second real system"
        # - connection_provider is read once, at build time, exactly as
        # scripts/control_center.py's own docstring already documents.
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        # "read the item" genuinely picks read_item over its sibling
        # write_item (this checkpoint's own fix) - not an accident of
        # declaration order.
        result = manager.handle_task(_intent_task("read the item"))

        assert result.succeeded
        assert result.payload["connection"] == "fixturesvc"
        assert result.payload["operation"] == "read_item"
        assert "count" in result.output

    def test_the_safe_operations_tool_is_genuinely_granted_by_the_real_composition_root(self, _isolated_paths):
        """Not inferred from a successful call above - read directly off
        the real ManagerAgent's own granted ToolSet."""
        _declare_and_credential(_isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth")
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        assert manager.own_skills.tools.permits(tool_name_for("fixturesvc", "read_item"))

    def test_an_unauthorized_safe_operation_is_still_refused_even_though_the_tool_is_granted(self, _isolated_paths):
        """Granting the Tool is not the same as authorizing it - the
        Tool exists (a GET needs no confirmation to be *reachable*), but
        connected_action's own operation-level authorization check still
        runs first and still refuses honestly when no credential is on
        file for an operation that needs one. Proven here with the
        auth-required operation named so the request's own words clearly
        pick it out over its sibling."""
        _declare_and_credential(
            _isolated_paths, operations="checked_read:GET:/checked:auth,write_item:POST:/item:auth",
            credential=None,
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        result = manager.handle_task(_intent_task("checked read please"))

        assert not result.succeeded
        assert "not authorized" in result.error.lower()


class TestTheConsequentialBoundaryIsNowAGovernedConfirmationNotARefusal:
    """Phase 6 (real-world services & transactions): closing the "tool
    not installed" gap did not become a way to reach a consequential
    operation on the strength of one remark alone - it became reachable
    through the identical explicit-confirmation gate every other
    consequential action in this project already requires."""

    def test_a_consequential_operation_is_granted_as_a_tool_exactly_like_communication_send_already_is(self, _isolated_paths):
        """write_item's tool now IS constructed by the real composition
        root - the identical unconditional grant
        CommunicationSendTool/CallTool already have. What keeps this
        safe is not the tool's absence any more; it is
        connected_action's own confirm-before-execute gate, proven by
        the rest of this class."""
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        assert manager.own_skills.tools.permits(tool_name_for("fixturesvc", "write_item"))
        assert manager.own_skills.tools.permits(tool_name_for("fixturesvc", "read_item"))

    def test_a_matched_consequential_operation_raises_a_confirmation_not_an_immediate_execution(self, _isolated_paths):
        """The user's own words clearly pick out write_item (declared
        *second* here, so this is genuinely about intent, not
        declaration order) - selection genuinely identifies it, and
        instead of executing it (or refusing outright), the real,
        production-built system describes it back and waits for an
        explicit yes/no. The real fixture is never reached here (no
        urlopen monkeypatch exists in this test, so any real attempt
        would raise rather than fail gracefully) - proof nothing ran."""
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        result = manager.handle_task(_intent_task("write the item"))

        assert result.succeeded
        assert result.payload["awaiting"] == "confirmation"
        assert result.payload["operation"] == "write_item"
        assert "(yes/no)" in result.output

    def test_confirming_it_on_the_very_next_remark_actually_runs_it_for_real(self, _isolated_paths, monkeypatch):
        """The other half of the proof above: a genuinely clear "yes"
        right after the pause makes the identical operation actually
        execute against the real (stubbed) service - never fabricated,
        never silently skipped."""
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"id": "item-1"}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        raised = manager.handle_task(_intent_task("write the item"))
        assert raised.succeeded and raised.payload["awaiting"] == "confirmation"

        confirmed = manager.handle_task(Task(description="yes"))
        assert confirmed.succeeded
        assert confirmed.payload["handled_by"] == "connected_action"
        assert confirmed.payload["operation"] == "write_item"
        assert "id" in confirmed.output

    def test_a_clear_no_cancels_it_without_ever_reaching_the_real_service(self, _isolated_paths):
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        raised = manager.handle_task(_intent_task("write the item"))
        assert raised.succeeded and raised.payload["awaiting"] == "confirmation"

        # No urlopen monkeypatch exists in this test at all - a real
        # attempt would raise rather than fail gracefully, so a clean
        # cancellation is the only way this can pass.
        cancelled = manager.handle_task(Task(description="no, don't"))
        assert cancelled.succeeded
        assert cancelled.payload["confirmed"] is False

    def test_the_control_center_confirm_token_path_is_a_second_independent_way_to_run_it(self, _isolated_paths, monkeypatch):
        """Never replaced: the identical, unchanged typed-command path,
        gated by its own real confirm_token dance ``route_command`` (the
        same HTTP layer scripts/serve_ui.py itself serves) already
        enforces for every administrative write, still works exactly as
        it always has, alongside the new conversational path proven
        above."""
        from scripts.serve_ui import route_command

        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"id": "item-1"}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        system = _live_system(runtime, manager, channel, conversation)
        command = "services fixturesvc execute write_item note=hello"

        status, pending_body, _ = route_command(command, None, system, action_log_path=_isolated_paths)
        assert status == 200
        assert json.loads(pending_body)["requires_confirmation"] is True

        token = json.loads(pending_body)["confirm_token"]
        status, confirmed_body, _ = route_command(command, token, system, action_log_path=_isolated_paths)
        assert status == 200
        confirmed = json.loads(confirmed_body)
        assert confirmed["requires_confirmation"] is False
        assert "succeeded" in confirmed["result"]


class TestDiscoveredParametersSurviveTheRealDeclareCommand:
    """Phase 6 (real-world services & transactions) follow-up gap,
    found by this checkpoint's own live verification, not by a unit
    test: ``services <name> discover`` correctly finds a real
    operation's own declared parameters, but its own suggested
    follow-up ``declare`` command - and ``declare``'s own parser - used
    to drop them silently, because the compact op-spec shorthand
    (``name:METHOD:path:auth|open``) had no way to carry them at all.
    For any consequential real-world operation that needs a real value
    (a booking's date, an order's item, a reservation's slot - which is
    virtually every one Phase 6 actually cares about), a service
    declared through Control Center's own typed command therefore lost
    the exact data the Data-flow-through-execution checkpoint exists
    to carry. Closed by extending the op-spec shorthand with an
    optional fifth field (see ``scripts/control_center.py``'s own
    ``_op_spec`` docstring for the exact encoding) - never a second
    declare path, never anything service-specific."""

    def test_a_discovered_parameter_survives_copy_pasting_the_suggested_declare_command(
        self, _isolated_paths, monkeypatch,
    ):
        import re

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_, *a, **k): return json.dumps({
                "description": "a generic reservable-slot fixture",
                "operations": [
                    {
                        "name": "reserve_slot", "method": "POST", "path": "/reserve",
                        "requires_auth": True,
                        "parameters": [{"name": "slot_id", "location": "body", "required": True}],
                    },
                ],
            }).encode()

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        discovered = run_command(
            centre, "services slotfixture discover http://127.0.0.1:9199", 200, channel, conversation,
        )
        # The exact suggested command a real person would copy and paste -
        # extracted from the real output, never reconstructed by hand,
        # so this test fails if that suggestion itself ever regresses.
        suggested = next(
            line.strip() for line in discovered.splitlines() if line.strip().startswith("services slotfixture declare")
        )
        assert "slot_id" in suggested, "the suggested command must carry the discovered parameter"

        result = run_command(centre, suggested, 200, channel, conversation)
        assert result.startswith("declared ")

        from src.services.connections.service_registry import load_service_registry

        registry = load_service_registry(control_center_module.DEFAULT_SERVICES_PATH)
        op = registry["slotfixture"].operation("reserve_slot")
        assert op is not None
        assert len(op.parameters) == 1
        assert op.parameters[0].name == "slot_id"
        assert op.parameters[0].location == "body"
        assert op.parameters[0].required is True

    def test_declare_still_accepts_the_original_four_field_shape_unchanged(self, _isolated_paths):
        """Backwards compatibility: an op-spec with no fifth field
        behaves exactly as it always has - every existing declared
        service, and every existing test that hand-types the short
        four-field shape, is unaffected."""
        runtime, manager, channel, conversation = real_system()
        centre = ControlCenter(runtime, manager)

        result = run_command(
            centre,
            "services plainfixture declare http://127.0.0.1:9199 ping:GET:/ping:open",
            200, channel, conversation,
        )
        assert result.startswith("declared ")

        from src.services.connections.service_registry import load_service_registry

        registry = load_service_registry(control_center_module.DEFAULT_SERVICES_PATH)
        op = registry["plainfixture"].operation("ping")
        assert op is not None
        assert op.parameters == ()


def _live_system(runtime, manager, channel, conversation):
    from scripts.serve_ui import LiveSystem
    import threading

    return LiveSystem(
        runtime=runtime, manager=manager, channel=channel, conversation=conversation,
        centre=ControlCenter(runtime, manager), lock=threading.Lock(),
    )
