"""Closes the one remaining Stage 16 gap: a real, active, authorized
connection was never *reachable* through Alfred's own natural-language
path, because nothing in the real composition root ever granted a
``ConnectionActionTool`` for any of its operations - see
``scripts/control_center.py``'s own ``_safe_connection_tools``
docstring for the full "why".

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

        assert manager.own_skills.tools.permits(tool_name_for("read_item"))

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


class TestTheConsequentialBoundaryIsNotBypassedByTheNewGrant:
    """Task 4's own requirement: closing the "tool not installed" gap
    must never become a new way to reach a consequential operation
    through conversation."""

    def test_a_consequential_operation_is_never_granted_as_a_tool_at_all(self, _isolated_paths):
        """Defense in depth beyond connected_action's own refusal:
        write_item's tool is never even constructed by the real
        composition root, so no future caller of request.tools could
        reach it either, by accident or otherwise."""
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        assert not manager.own_skills.tools.permits(tool_name_for("write_item"))
        assert manager.own_skills.tools.permits(tool_name_for("read_item"))

    def test_a_matched_consequential_operation_is_refused_not_silently_executed(self, _isolated_paths):
        """The user's own words clearly pick out write_item (declared
        *second* here, so this is genuinely about intent, not
        declaration order) - selection genuinely identifies it (the
        refusal names 'connection_write_item', not some other tool or
        an ambiguity message), and it is still refused, now through the
        real, production-built system where write_item's Tool was never
        granted at all (see TestTheConsequentialBoundaryIsNotBypassedBy
        TheNewGrant's own sibling test) - a different, but equally
        honest, refusal than the manually-wired unit test in
        test_stage16_connections.py exercises for the same boundary."""
        _declare_and_credential(
            _isolated_paths, operations="read_item:GET:/item:open,write_item:POST:/item:auth",
            credential="genuine-secret-token",
        )
        runtime, manager, channel, conversation = real_system(connection_provider="fixturesvc")

        result = manager.handle_task(_intent_task("write the item"))

        assert not result.succeeded
        # Refused before ever being attempted - the real fixture was
        # never reached (no urlopen monkeypatch exists in this test, so
        # any real attempt would raise rather than fail gracefully).
        assert "connection_write_item" in result.error
        assert "not installed" in result.error.lower()

    def test_the_confirmed_control_center_path_remains_the_only_way_to_actually_run_it(self, _isolated_paths, monkeypatch):
        """The consequential operation genuinely still runs - just never
        through conversation, and only after the real confirm_token
        dance ``route_command`` (the same HTTP layer scripts/serve_ui.py
        itself serves) already enforces for every administrative write."""
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


def _live_system(runtime, manager, channel, conversation):
    from scripts.serve_ui import LiveSystem
    import threading

    return LiveSystem(
        runtime=runtime, manager=manager, channel=channel, conversation=conversation,
        centre=ControlCenter(runtime, manager), lock=threading.Lock(),
    )
