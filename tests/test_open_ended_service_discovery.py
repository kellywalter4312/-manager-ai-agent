"""Open-ended real-world service discovery + governed connection
establishment - the end-to-end proof.

Every other new test file proves one layer in isolation
(``ServiceDescriptor``, the registry, ``DeclaredConnection``). This one
proves the actual gap closed: a legitimate service Alfred was never
built knowing about becomes a real, usable, governed connection through
configuration alone - a name declared via Control Center, a credential
attached the identical already-generic way Gmail's own account already
is, and ``MANAGER_CONNECTION_PROVIDER=<name>`` selecting it - reaching
the *unmodified* ``connected_action`` capability with zero new Python
subclass written per service. Contrast this directly with
``tests/test_stage16_connections.py::TestFutureConnectionNeedsNoOrchestrationChange``,
which proves the same capability is generic *given* a hand-written
``Connection`` object; this file proves the connection itself no longer
needs to be hand-written at all.
"""

from __future__ import annotations

import urllib.error
import urllib.request

import pytest

from src.agents.tools.connection import ConnectionActionTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.providers import connection_factory
from src.providers.availability import AvailabilityState
from src.providers.connection import AuthorizationState
from src.providers.connection_factory import build_connection
from src.providers.declared_connection import DeclaredConnection
from src.providers.gmail_connection import GmailConnection
from src.providers.null_connection import NullConnection
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation
from src.services.connections import service_registry as service_registry_module
from src.services.connections.service_registry import declare_service
from src.services.credentials import account_store_file as account_store_file_module
from src.services.credentials.account import CredentialStore, ProviderAccount
from src.services.credentials.account_store_file import save_credential_store

CONNECTED_TASK = Task(description="check my connected account")


@pytest.fixture(autouse=True)
def _isolated_stores(tmp_path, monkeypatch):
    """Every test in this file runs against its own real files under
    tmp_path - genuine persistence, never shared with the real
    installation's own data/connections or data/credentials."""
    services_path = tmp_path / "services.json"
    accounts_path = tmp_path / "accounts.json"
    monkeypatch.setattr(service_registry_module, "DEFAULT_SERVICES_PATH", services_path)
    monkeypatch.setattr(account_store_file_module, "DEFAULT_ACCOUNTS_PATH", accounts_path)
    return services_path, accounts_path


def _weather_descriptor(requires_auth=False):
    return ServiceDescriptor(
        name="weatherstub", base_url="http://127.0.0.1:9911",
        operations=(ServiceOperation(name="ping", method="GET", path="/ping", requires_auth=requires_auth),),
    )


class TestTheFactoryBuildsARealConnectionFromDeclaredDataAlone:
    def test_gmail_and_null_are_completely_unaffected(self):
        """The fallback only ever engages for a name neither built-in
        builder recognises - Gmail and Null still resolve exactly as
        they always did, checked first."""
        from src.config.settings import Settings

        assert isinstance(build_connection(Settings.from_env(env={})), NullConnection)
        assert isinstance(
            build_connection(Settings.from_env(env={}, connection_provider="gmail", mailbox="memory")),
            GmailConnection,
        )

    def test_a_name_matching_neither_a_builder_nor_a_declared_service_still_fails_fast(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, connection_provider="never-declared-anything")
        with pytest.raises(ValueError):
            build_connection(settings)

    def test_a_declared_undeclared_service_becomes_a_real_declaredconnection(self):
        """The actual gap: no Connection subclass is written for
        'weatherstub' anywhere in this test - only data was declared."""
        from src.config.settings import Settings

        declare_service(_weather_descriptor(), service_registry_module.DEFAULT_SERVICES_PATH)
        settings = Settings.from_env(env={}, connection_provider="weatherstub")
        connection = build_connection(settings)

        assert isinstance(connection, DeclaredConnection)
        assert connection.name == "weatherstub"
        assert connection.supported_operations == ("ping",)

    def test_the_declared_connection_picks_up_whatever_credential_is_on_file_for_its_own_name(self):
        from src.config.settings import Settings

        declare_service(_weather_descriptor(requires_auth=True), service_registry_module.DEFAULT_SERVICES_PATH)
        store = CredentialStore()
        store.add(ProviderAccount(provider="weatherstub", account_id="default", credential="abc123"))
        save_credential_store(store, account_store_file_module.DEFAULT_ACCOUNTS_PATH)

        settings = Settings.from_env(env={}, connection_provider="weatherstub")
        connection = build_connection(settings)
        assert connection.authorization is AuthorizationState.AUTHORIZED

    def test_no_credential_on_file_means_honestly_unauthorized_never_a_fabricated_pass(self):
        from src.config.settings import Settings

        declare_service(_weather_descriptor(requires_auth=True), service_registry_module.DEFAULT_SERVICES_PATH)
        settings = Settings.from_env(env={}, connection_provider="weatherstub")
        connection = build_connection(settings)
        assert connection.authorization is AuthorizationState.UNAUTHORIZED

    def test_forgetting_a_declared_service_makes_its_name_fail_again(self):
        from src.config.settings import Settings
        from src.services.connections.service_registry import forget_service

        declare_service(_weather_descriptor(), service_registry_module.DEFAULT_SERVICES_PATH)
        forget_service("weatherstub", service_registry_module.DEFAULT_SERVICES_PATH)
        settings = Settings.from_env(env={}, connection_provider="weatherstub")
        with pytest.raises(ValueError):
            build_connection(settings)


class TestConnectedActionReachesADeclaredServiceThroughTheUnmodifiedCapability:
    """The full ecosystem path: a declared, credentialed service, built
    by connection_factory purely from its own name, invoked through the
    real, unchanged connected_action capability - never a hand-injected
    Connection object the way TestFutureConnectionNeedsNoOrchestrationChange
    proves the capability's own genericity in isolation."""

    def test_a_real_declared_service_answers_a_real_connected_action_request(self, monkeypatch):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        declare_service(_weather_descriptor(requires_auth=False), service_registry_module.DEFAULT_SERVICES_PATH)

        def fake_urlopen(request, timeout):
            class _Resp:
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
                def read(self_): return b'{"conditions": "clear"}'
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="ephemeral", connection_provider="weatherstub",
        )
        runtime = build_runtime(settings)
        connection = runtime.connection
        assert isinstance(connection, DeclaredConnection)

        tools = ToolSet([
            ConnectionActionTool(connection, operation="ping", description="Pings weatherstub.")
        ])
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
        manager.own_skills._connection = connection

        result = manager.handle_task(CONNECTED_TASK)

        assert result.succeeded
        assert result.payload["connection"] == "weatherstub"
        assert result.payload["operation"] == "ping"

    def test_an_unreachable_declared_service_is_reported_honestly_not_fabricated(self, monkeypatch):
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        declare_service(_weather_descriptor(requires_auth=False), service_registry_module.DEFAULT_SERVICES_PATH)

        def failing_urlopen(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="ephemeral", connection_provider="weatherstub",
        )
        runtime = build_runtime(settings)
        connection = runtime.connection
        tools = ToolSet([
            ConnectionActionTool(connection, operation="ping", description="Pings weatherstub.")
        ])
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
        manager.own_skills._connection = connection

        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded


class TestHalfBrainAndProviderIndependence:
    def test_the_declared_connection_path_makes_no_brain_call_at_all(self):
        """Half Brain proof: declaring, building, and authorizing a
        service touches no LLM/Brain construction anywhere."""
        source_files = (
            "src/providers/service_descriptor.py",
            "src/providers/service_discovery.py",
            "src/services/connections/service_registry.py",
            "src/providers/declared_connection.py",
        )
        from pathlib import Path

        for rel in source_files:
            text = Path(rel).read_text(encoding="utf-8")
            for forbidden in ("ManagerBrain(", "LLMManagerBrain(", "NullBrain(", "SpecialistBrain("):
                assert forbidden not in text, (rel, forbidden)

    def test_no_provider_name_is_hardcoded_in_the_new_files(self):
        """Provider independence: nothing in the discovery/connection
        layer names a specific LLM vendor."""
        from pathlib import Path

        source_files = (
            "src/providers/service_descriptor.py",
            "src/providers/service_discovery.py",
            "src/services/connections/service_registry.py",
            "src/providers/declared_connection.py",
        )
        for rel in source_files:
            text = Path(rel).read_text(encoding="utf-8").lower()
            for forbidden in ("anthropic", "openai", "\"xai\"", "'xai'"):
                assert forbidden not in text, (rel, forbidden)

    def test_no_arbitrary_code_execution_in_the_new_files(self):
        from pathlib import Path

        source_files = (
            "src/providers/service_descriptor.py",
            "src/providers/service_discovery.py",
            "src/services/connections/service_registry.py",
            "src/providers/declared_connection.py",
        )
        for rel in source_files:
            text = Path(rel).read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system(", "__import__("):
                assert forbidden not in text, (rel, forbidden)

    def test_no_service_catalogue_exists_anywhere_in_the_new_files(self):
        """No hard-coded company/website list - the whole point of this
        mechanism. A real check, not a name check: every real-world
        domain name this checkpoint's own brief explicitly named as an
        example must be genuinely absent from the shipped source."""
        from pathlib import Path

        source_files = (
            "src/providers/service_descriptor.py",
            "src/providers/service_discovery.py",
            "src/services/connections/service_registry.py",
            "src/providers/declared_connection.py",
            "src/providers/connection_factory.py",
        )
        forbidden_examples = (
            "restaurant", "flight", "salon", "taxi", "hotel", "airline",
            "jumia", "mary salon", "booking.com",
        )
        for rel in source_files:
            text = Path(rel).read_text(encoding="utf-8").lower()
            for word in forbidden_examples:
                assert word not in text, (rel, word)


class TestDeviceIndependence:
    def test_nothing_in_the_new_files_references_a_browser_session_or_frontend(self):
        from pathlib import Path

        source_files = (
            "src/providers/service_descriptor.py",
            "src/providers/service_discovery.py",
            "src/services/connections/service_registry.py",
            "src/providers/declared_connection.py",
        )
        for rel in source_files:
            text = Path(rel).read_text(encoding="utf-8").lower()
            for forbidden in ("navigator.", "getusermedia", "mediarecorder", "document."):
                assert forbidden not in text, (rel, forbidden)


class TestDiscoveryFlowsIntoTheExistingConnectionMechanismEndToEnd:
    """The actual point of this checkpoint, proven as one chain: an
    unknown service's own published metadata -> discover_service ->
    the exact same governed 'declare' the prior checkpoint already
    proved -> connection_factory -> the unmodified connected_action
    capability. Never a service hand-declared through the registry
    directly - every descriptor here originates from a real (monkey-
    patched-network) discovery call, the same distinction this
    checkpoint's own brief draws between 'discovery verified' and a
    manually-registered fixture."""

    def _serve_weatherstub(self, monkeypatch):
        import json
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_, *a, **k):
                return json.dumps({
                    "description": "a real discovery fixture",
                    "operations": [
                        {"name": "ping", "method": "GET", "path": "/ping", "requires_auth": False},
                    ],
                }).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    def test_a_discovered_descriptor_is_accepted_by_the_real_declare_governance(self, monkeypatch):
        """discover_service's own output round-trips through
        ServiceDescriptor construction identically to a hand-typed
        declare - proven by feeding its exact fields into declare_service,
        the same real, governed persistence call Control Center uses."""
        from src.providers.service_discovery import discover_service

        self._serve_weatherstub(monkeypatch)
        found = discover_service("weatherstub", "http://127.0.0.1:8901")

        declare_service(found.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)
        reloaded = load_service_registry_fresh()
        assert "weatherstub" in reloaded
        assert reloaded["weatherstub"].operation_names == ("ping",)

    def test_the_full_chain_discover_declare_select_connected_action(self, monkeypatch):
        """The complete lifecycle in one test: an unknown URL is
        discovered (not manually declared), the resulting descriptor is
        persisted through the real governed declare path, the factory
        builds a real connection from the config name alone, and the
        unmodified connected_action capability reaches it."""
        import urllib.request as urllib_request_module

        from src.agents.tools.connection import ConnectionActionTool
        from src.agents.tools.toolset import ToolSet
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.providers.service_discovery import discover_service
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        # Step 1: DISCOVER - a real (network-substituted) fetch against
        # an "unknown" URL this test never hand-declares anything for.
        self._serve_weatherstub(monkeypatch)
        found = discover_service("weatherstub", "http://127.0.0.1:8901")
        assert found.descriptor.name == "weatherstub"  # genuinely produced by discovery, not asserted a priori

        # Step 2: REPRESENT/persist - the identical governed declare_service
        # the real 'services <name> declare' Control Center verb calls.
        declare_service(found.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)

        # Step 3: CONNECT - connection_factory resolves the name alone.
        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="ephemeral", connection_provider="weatherstub",
        )
        runtime = build_runtime(settings)
        connection = runtime.connection
        assert isinstance(connection, DeclaredConnection)
        assert connection.name == "weatherstub"

        # Step 4: reach it through the real, unmodified capability - a
        # second, independent urlopen substitution proves execute()
        # itself performs a genuine call, not merely that discovery did.
        def fake_execute_urlopen(request, timeout):
            class _Resp:
                def __enter__(self_): return self_
                def __exit__(self_, *a): return False
                def read(self_): return b'{"conditions": "clear"}'
            return _Resp()
        monkeypatch.setattr(urllib_request_module, "urlopen", fake_execute_urlopen)

        tools = ToolSet([
            ConnectionActionTool(connection, operation="ping", description="Pings weatherstub.")
        ])
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
        manager.own_skills._connection = connection

        result = manager.handle_task(CONNECTED_TASK)
        assert result.succeeded
        assert result.payload == {"handled_by": "connected_action", "connection": "weatherstub", "operation": "ping"}

    def test_discovery_alone_never_persists_anything(self, monkeypatch):
        """The governance boundary, proven as behaviour: calling
        discover_service, with no declare_service call afterward, must
        leave the real registry file exactly as empty as it started."""
        from src.providers.service_discovery import discover_service

        self._serve_weatherstub(monkeypatch)
        discover_service("weatherstub", "http://127.0.0.1:8901")

        assert load_service_registry_fresh() == {}

    def test_a_second_discovery_of_the_same_service_is_idempotent(self, monkeypatch):
        """Duplicate discovery (a person re-running discover, or
        re-declaring after re-discovering) replaces, never
        accumulates - the identical semantics declare_service already
        guarantees for a hand-typed redeclare."""
        from src.providers.service_discovery import discover_service

        self._serve_weatherstub(monkeypatch)
        first = discover_service("weatherstub", "http://127.0.0.1:8901")
        declare_service(first.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)
        second = discover_service("weatherstub", "http://127.0.0.1:8901")
        declare_service(second.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)

        registry = load_service_registry_fresh()
        assert len(registry) == 1


def load_service_registry_fresh():
    from src.services.connections.service_registry import load_service_registry

    return load_service_registry(service_registry_module.DEFAULT_SERVICES_PATH)


class TestGenericOperationExecutionEndToEnd:
    """Operation-level authorization + generalized real-world action
    execution (2026-09-09) - the full lifecycle in one place: an
    industry-agnostic, deliberately generic test service (never a real
    company, never a named industry - 'genericsvc', 'read_item',
    'write_item' name nothing real) is discovered, not hand-declared;
    its safe read and its consequential write are correctly told apart
    from the service's own declared metadata alone; the write is
    refused without confirmation; both are exercised through the real,
    unmodified Control Center execution surface and the real,
    unmodified connected_action capability.
    """

    def _serve_generic_service(self, monkeypatch):
        import json
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_, *a, **k):
                return json.dumps({
                    "openapi": "3.0.0",
                    "info": {"title": "Generic Test Service"},
                    "paths": {
                        "/item": {
                            "get": {"operationId": "read_item", "summary": "harmless read"},
                            "post": {
                                "operationId": "write_item",
                                "summary": "consequential write",
                                "security": [{"apiKey": []}],
                            },
                        },
                    },
                }).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

    def test_the_generic_service_is_genuinely_discovered_not_hand_declared(self, monkeypatch):
        from src.providers.service_discovery import discover_service

        self._serve_generic_service(monkeypatch)
        found = discover_service("genericsvc", "http://127.0.0.1:8905")

        assert found.source_kind == "openapi"
        assert set(found.descriptor.operation_names) == {"read_item", "write_item"}
        assert found.descriptor.operation("read_item").requires_auth is False
        assert found.descriptor.operation("write_item").requires_auth is True

    def test_the_matching_operation_is_selected_by_intent_not_declaration_order(self, monkeypatch):
        """Intent-driven operation selection's own checkpoint: a request
        that genuinely asks to read reaches read_item, and a request
        that genuinely asks to write reaches (and is honestly refused
        for) write_item - never merely whichever was declared first."""
        from src.agents.tools.connection import ConnectionActionTool
        from src.agents.tools.toolset import ToolSet
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.providers.service_discovery import discover_service
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        self._serve_generic_service(monkeypatch)
        found = discover_service("genericsvc", "http://127.0.0.1:8905")
        declare_service(found.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="ephemeral", connection_provider="genericsvc",
        )
        runtime = build_runtime(settings)
        connection = runtime.connection
        assert isinstance(connection, DeclaredConnection)
        assert connection.supported_operations[0] == "read_item"  # the fixture's own declaration order

        tools = ToolSet([
            ConnectionActionTool(connection, operation=op, description=f"Performs {op}.")
            for op in connection.supported_operations
        ])
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
        manager.own_skills._connection = connection

        read_result = manager.handle_task(Task(description="check my connected account: read the item"))
        assert read_result.succeeded  # the safe read, genuinely executed
        assert read_result.payload["operation"] == "read_item"

        write_result = manager.handle_task(Task(description="check my connected account: write the item"))
        assert not write_result.succeeded
        assert "confirmation" in write_result.error.lower()
        assert "control center" in write_result.error.lower()

    def test_the_write_operation_succeeds_once_genuinely_authorized(self, monkeypatch):
        """The other half of the pair: write_item is never reachable
        through conversation (proven above), but *is* genuinely
        executable once a real credential exists for it - discovered,
        declared, credentialed, operation-authorized, and executed, all
        through the real provider/connection layer. The confirm_token/
        audit-log dance this same call goes through when reached via
        Control Center is separately, thoroughly proven in
        tests/test_administrative_write_audit.py -
        TestOperationLevelExecutionGovernance; this test's own job is
        the discovery-to-execution chain itself, not re-proving that
        gate a second time.
        """
        self._serve_generic_service(monkeypatch)

        from src.providers.service_discovery import discover_service

        found = discover_service("genericsvc", "http://127.0.0.1:8905")
        declare_service(found.descriptor, service_registry_module.DEFAULT_SERVICES_PATH)

        store = CredentialStore()
        store.add(ProviderAccount(provider="genericsvc", account_id="default", credential="genuine-token"))
        save_credential_store(store, account_store_file_module.DEFAULT_ACCOUNTS_PATH)

        registry = load_service_registry_fresh()
        descriptor = registry["genericsvc"]
        account = store.active("genericsvc")
        connection = DeclaredConnection(descriptor, account)

        assert connection.authorization_for("write_item") is AuthorizationState.AUTHORIZED
        assert connection.requires_confirmation_for("write_item") is True

        import json
        import urllib.request as urllib_request_module

        class _WriteResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return json.dumps({"id": "created-1"}).encode()

        monkeypatch.setattr(urllib_request_module, "urlopen", lambda *a, **k: _WriteResponse())

        result = connection.execute("write_item", note="a real generic write")
        assert result.ok
        assert "created-1" in result.detail

    def test_a_second_generic_service_with_a_different_shape_needs_no_new_code(self, monkeypatch):
        """Open-endedness: a completely different, unrelated generic
        service - different name, different operation names, different
        paths - works through the identical mechanism with zero new
        branches, proving nothing about *this specific* service was
        ever assumed."""
        import json
        import urllib.request

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_, *a, **k):
                return json.dumps({
                    "openapi": "3.0.0",
                    "info": {"title": "A Completely Different Generic Service"},
                    "paths": {
                        "/status": {"get": {"operationId": "check_status"}},
                        "/entries": {"post": {"operationId": "submit_entry", "security": [{"basic": []}]}},
                    },
                }).encode("utf-8")

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        from src.providers.service_discovery import discover_service

        found = discover_service("othergenericsvc", "http://127.0.0.1:8906")
        assert set(found.descriptor.operation_names) == {"check_status", "submit_entry"}
        assert found.descriptor.operation("check_status").requires_auth is False
        assert found.descriptor.operation("submit_entry").requires_auth is True
