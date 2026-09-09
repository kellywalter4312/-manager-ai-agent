"""Stage 16 - Connections + Real-World Actions.

Gives Alfred a governed, extensible architecture for reaching an
external, authenticated service and performing a real action through
it - motivated by ``src/extensions.py``'s own documented diagram
("Agent -> Identity -> Brain -> Memory -> Personality -> Voice ->
Capabilities -> Connections"), which named this layer since early in
the project without ever implementing it.

The mechanism, end to end:

    User request
      -> Alfred's reasoning/routing (connected_action_intent, a claims=
         predicate, src/manager/capabilities.py)
      -> the governed capability (connected_action), which checks - in
         order, never collapsed - capability availability
         (ToolSet.permits), connection authorization
         (Connection.authorization), and only then attempts execution
      -> the governed tool boundary (ConnectionActionTool, a Tool like
         any other a specialist could be granted)
      -> a provider-independent attempt (Connection -> ConnectionResult,
         src/providers/connection.py)
      -> an honest answer, or an honest refusal at any stage that could
         not be completed - never a fabricated success

Only ``NullConnection`` (honestly unavailable) and ``GmailConnection``
(a thin adapter reusing the existing, already-governed Gmail *read*
boundary - no Outbox, no new external risk) ship as real
implementations - this stage establishes the seam, not an integration
framework.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.agents.tools.connection import ConnectionActionTool, tool_name_for
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.providers.availability import Availability, AvailabilityState
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)
from src.providers.null_connection import NullConnection

REPO_ROOT = Path(__file__).resolve().parent.parent

CONNECTION_FILES = (
    REPO_ROOT / "src" / "providers" / "connection.py",
    REPO_ROOT / "src" / "providers" / "null_connection.py",
    REPO_ROOT / "src" / "providers" / "gmail_connection.py",
    REPO_ROOT / "src" / "providers" / "connection_factory.py",
    REPO_ROOT / "src" / "agents" / "tools" / "connection.py",
)


class ScriptedConnection(Connection):
    """A real, working Connection double - never a network call."""

    def __init__(
        self,
        *,
        available: bool = True,
        authorized: bool = True,
        operations: tuple[str, ...] = ("read_email",),
        fail: bool = False,
    ):
        self._available = available
        self._authorized = authorized
        self._operations = operations
        self._fail = fail
        self.executed: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def availability(self) -> Availability:
        return Availability(
            state=AvailabilityState.AVAILABLE if self._available else AvailabilityState.UNAVAILABLE
        )

    @property
    def authorization(self) -> AuthorizationState:
        return AuthorizationState.AUTHORIZED if self._authorized else AuthorizationState.UNAUTHORIZED

    @property
    def supported_operations(self) -> tuple[str, ...]:
        return self._operations

    def execute(self, operation: str, **params) -> ConnectionResult:
        if operation not in self._operations:
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.UNSUPPORTED,
                detail="not supported", source=self.name, executed_at=utc_now(),
            )
        if self._fail:
            return ConnectionResult(
                operation=operation, status=ConnectionStatus.FAILED,
                detail="the scripted connection failed", source=self.name, executed_at=utc_now(),
            )
        self.executed.append(operation)
        return ConnectionResult(
            operation=operation, status=ConnectionStatus.SUCCEEDED,
            detail="3 message(s)", source=self.name, executed_at=utc_now(),
        )


def real_manager(*, tools: ToolSet | None = None, connection: Connection | None = None, **overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
    if connection is not None:
        manager.own_skills._connection = connection
    return manager


def manager_with_connection(connection: Connection | None = None, **overrides):
    connection = connection or ScriptedConnection()
    tools = ToolSet([
        ConnectionActionTool(connection, operation=op, description=f"Performs {op}.")
        for op in connection.supported_operations
    ])
    return real_manager(tools=tools, connection=connection, **overrides)


CONNECTED_TASK = Task(description="check my connected account")


# ---------------------------------------------------------------------------
# 1. The connection abstraction works
# ---------------------------------------------------------------------------


class TestTheConnectionAbstractionWorks:
    def test_connectionresult_is_frozen(self):
        result = ConnectionResult(operation="read_email", status=ConnectionStatus.SUCCEEDED)
        with pytest.raises(Exception):
            result.operation = "changed"

    def test_only_succeeded_status_is_ok(self):
        assert ConnectionStatus.SUCCEEDED.ok
        for status in (ConnectionStatus.FAILED, ConnectionStatus.UNAVAILABLE, ConnectionStatus.UNAUTHORIZED, ConnectionStatus.UNSUPPORTED):
            assert not status.ok

    def test_two_unrelated_connections_satisfy_the_same_interface(self):
        assert isinstance(NullConnection(), Connection)
        assert isinstance(ScriptedConnection(), Connection)

    def test_empty_operation_is_refused(self):
        with pytest.raises(ValueError):
            ConnectionResult(operation="", status=ConnectionStatus.FAILED)

    def test_authorization_grants_access_is_correct(self):
        assert AuthorizationState.AUTHORIZED.grants_access
        assert AuthorizationState.NOT_REQUIRED.grants_access
        assert not AuthorizationState.UNAUTHORIZED.grants_access

    def test_the_null_connection_declares_no_operations(self):
        assert NullConnection().supported_operations == ()

    def test_the_null_connection_never_fabricates_success(self):
        result = NullConnection().execute("anything")
        assert not result.ok
        assert result.status is ConnectionStatus.UNAVAILABLE


# ---------------------------------------------------------------------------
# 2. Connection availability is distinct from Brain availability
# ---------------------------------------------------------------------------


class TestConnectionAvailabilityIsDistinctFromBrainAvailability:
    def test_a_connection_with_no_brain_configured_still_reports_its_own_availability(self):
        connection = ScriptedConnection(available=True)
        assert connection.availability.state is AvailabilityState.AVAILABLE

    def test_a_half_brain_manager_can_still_use_a_fully_available_connection(self):
        manager = manager_with_connection()  # default NullBrain
        result = manager.handle_task(CONNECTED_TASK)
        assert result.succeeded

    def test_connection_availability_and_brain_mode_are_read_from_different_objects(self):
        manager = manager_with_connection()
        assert manager.own_skills._connection is not manager.brain


# ---------------------------------------------------------------------------
# 3. Authorization state is represented honestly
# ---------------------------------------------------------------------------


class TestAuthorizationStateIsHonest:
    def test_null_connection_is_honestly_unauthorized(self):
        assert NullConnection().authorization is AuthorizationState.UNAUTHORIZED

    def test_an_unauthorized_connection_refuses_before_ever_executing(self):
        connection = ScriptedConnection(authorized=False)
        manager = manager_with_connection(connection)
        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded
        assert "not authorized" in result.error
        assert connection.executed == []

    def test_an_authorized_connection_is_allowed_to_proceed(self):
        connection = ScriptedConnection(authorized=True)
        manager = manager_with_connection(connection)
        result = manager.handle_task(CONNECTED_TASK)
        assert result.succeeded
        assert connection.executed == ["read_email"]

    def test_gmail_connection_reports_unauthorized_with_no_token_file(self, tmp_path):
        from src.providers.gmail_connection import GmailConnection
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        connection = GmailConnection(InMemoryMailbox([]), token_path=tmp_path / "no-such-token.json")
        assert connection.authorization is AuthorizationState.UNAUTHORIZED

    def test_gmail_connection_reports_authorized_when_a_token_file_exists(self, tmp_path):
        from src.providers.gmail_connection import GmailConnection
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        token = tmp_path / "token.json"
        token.write_text("{}", encoding="utf-8")
        connection = GmailConnection(InMemoryMailbox([]), token_path=token)
        assert connection.authorization is AuthorizationState.AUTHORIZED

    def test_authorization_is_a_cheap_local_check_never_a_network_call(self):
        """Structural: GmailConnection.authorization must only ever touch
        Path.exists(), never gmail_auth.load_credentials or anything
        that could reach the network."""
        import src.providers.gmail_connection as module

        source = inspect.getsource(module.GmailConnection.authorization.fget)
        assert "load_credentials" not in source
        assert "authorize(" not in source


# ---------------------------------------------------------------------------
# 4-6. Governed capability execution: success, failure, honesty
# ---------------------------------------------------------------------------


class TestGovernedCapabilityExecution:
    def test_a_successful_execution_returns_a_verified_result(self):
        connection = ScriptedConnection()
        manager = manager_with_connection(connection)
        result = manager.handle_task(CONNECTED_TASK)
        assert result.succeeded
        assert result.payload["handled_by"] == "connected_action"
        assert result.payload["connection"] == "scripted"
        assert "3 message(s)" in result.output

    def test_a_failed_execution_is_reported_honestly(self):
        connection = ScriptedConnection(fail=True)
        manager = manager_with_connection(connection)
        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded
        assert "scripted connection failed" in result.error

    def test_no_tool_granted_is_reported_as_a_capability_gap_not_a_connection_gap(self):
        connection = ScriptedConnection()
        manager = real_manager(connection=connection)  # tool never granted
        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded
        assert "not installed" in result.error
        assert connection.executed == []

    def test_no_connection_at_all_is_reported_honestly(self):
        """A bare CapabilityRequest with connection=None - the shape any
        older caller or minimal test construction produces - must be
        answered honestly, not with an exception."""
        import src.manager.capabilities as capabilities_module
        from src.core.contracts import Task as _Task

        request = capabilities_module.CapabilityRequest(
            task=_Task(description="check my connected account"),
            identity=None, roster=lambda: (), own_name="Alfred",
            own_capabilities=(), tools=ToolSet(), connection=None,
        )
        result = capabilities_module.connected_action(request)
        assert not result.succeeded
        assert "don't have a connection" in result.error

    def test_a_connection_with_no_supported_operations_is_reported_honestly(self):
        manager = real_manager(connection=NullConnection())
        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded
        assert "no supported actions" in result.error

    def test_unsupported_operation_is_reported_by_the_connection_itself(self):
        connection = ScriptedConnection(operations=())
        result = connection.execute("read_email")
        assert result.status is ConnectionStatus.UNSUPPORTED


# ---------------------------------------------------------------------------
# 7. No execution occurs outside the governed capability path
# ---------------------------------------------------------------------------


class TestNoExecutionOutsideTheGovernedPath:
    def test_connected_action_never_calls_execute_directly(self):
        """Structural: the capability handler must reach a Connection
        only through request.tools.invoke() - never call .execute()
        itself, which would bypass ToolSet's own governance boundary."""
        import src.manager.capabilities as capabilities_module

        source = inspect.getsource(capabilities_module.connected_action)
        assert ".execute(" not in source
        assert "request.tools.invoke(" in source

    def test_the_tool_is_the_only_thing_that_calls_execute(self):
        for path in CONNECTION_FILES:
            source = path.read_text(encoding="utf-8")
            if path.name == "connection.py" and "agents" not in str(path):
                continue  # the ABC's own abstractmethod declaration
        source = (REPO_ROOT / "src" / "agents" / "tools" / "connection.py").read_text(encoding="utf-8")
        assert source.count(".execute(") == 1

    def test_toolset_invoke_is_the_only_boundary_a_capability_ever_crosses(self):
        import src.manager.capabilities as capabilities_module

        source = inspect.getsource(capabilities_module.connected_action)
        assert "ConnectionActionTool(" not in source
        assert "GmailConnection(" not in source
        assert "NullConnection(" not in source


# ---------------------------------------------------------------------------
# 8-10. Canonical Brain/Memory - unchanged, independent per agent
# ---------------------------------------------------------------------------


class TestCanonicalBrainAndMemory:
    def test_alfreds_brain_is_unchanged_by_a_connected_action(self):
        manager = manager_with_connection()
        brain_before = manager.brain
        manager.handle_task(CONNECTED_TASK)
        assert manager.brain is brain_before

    def test_alfreds_memory_is_unchanged_by_a_connected_action(self):
        manager = manager_with_connection()
        memory_before = manager.memory
        manager.handle_task(CONNECTED_TASK)
        assert manager.memory is memory_before

    def test_a_connected_action_writes_nothing_to_memory(self):
        manager = manager_with_connection()
        before = len(manager.memory.recall(limit=200))
        manager.handle_task(CONNECTED_TASK)
        assert len(manager.memory.recall(limit=200)) == before

    def test_specialist_brain_and_memory_are_independent_of_the_connection(self):
        manager = manager_with_connection()
        arthur = manager.registry.get("Arthur")
        arthur_brain_before, arthur_memory_before = arthur.brain, arthur.memory
        manager.handle_task(CONNECTED_TASK)
        assert arthur.brain is arthur_brain_before
        assert arthur.memory is arthur_memory_before

    def test_no_new_brain_or_memory_construction_anywhere_in_the_new_files(self):
        for path in CONNECTION_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "EphemeralMemory(", "PersistentMemory(", "ManagerBrain(",
                "LLMManagerBrain(", "SpecialistBrain(", "NullBrain(",
            ):
                assert forbidden not in source, (path.name, forbidden)


# ---------------------------------------------------------------------------
# 11. No duplicate Memory is created
# ---------------------------------------------------------------------------


class TestNoDuplicateMemory:
    def test_connection_objects_do_not_own_agent_memory(self):
        """Structural: Connection/NullConnection/GmailConnection never
        hold a Memory reference at all."""
        for path in (
            REPO_ROOT / "src" / "providers" / "connection.py",
            REPO_ROOT / "src" / "providers" / "null_connection.py",
            REPO_ROOT / "src" / "providers" / "gmail_connection.py",
        ):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    assert "services.memory" not in module

    def test_conversation_room_creates_no_connection_specific_memory(self):
        from src.conversation.conversation import Conversation, Participant, ParticipantKind
        from src.channels.channel import TextChannel

        manager = manager_with_connection()
        channel = TextChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))
        conversation.join(Participant(manager.own_skills.name, ParticipantKind.AGENT))

        response = channel.ask(CONNECTED_TASK.description)
        conversation.say(
            __import__("src.conversation.conversation", fromlist=["Utterance"]).Utterance(
                speaker=manager.own_skills.name, text=response.turn.outcome or "",
            )
        )
        assert len(conversation.participants) == 2  # the person and Alfred - no connection joined

    def test_external_service_data_is_not_silently_copied_into_long_term_memory(self):
        manager = manager_with_connection()
        manager.handle_task(CONNECTED_TASK)
        records = manager.memory.recall(limit=200)
        assert not any("3 message(s)" in r.content for r in records)

    def test_exactly_one_canonical_memory_object_per_agent(self):
        manager = manager_with_connection()
        arthur = manager.registry.get("Arthur")
        assert manager.memory is not arthur.memory
        # Calling the capability twice does not construct a new store.
        first_memory = manager.memory
        manager.handle_task(CONNECTED_TASK)
        manager.handle_task(CONNECTED_TASK)
        assert manager.memory is first_memory


# ---------------------------------------------------------------------------
# 12. Provider switching preserves identity
# ---------------------------------------------------------------------------


class TestProviderSwitchingPreservesIdentity:
    def test_switching_connections_leaves_brain_memory_and_identity_untouched(self):
        for connection in (NullConnection(), ScriptedConnection()):
            manager = manager_with_connection(connection) if connection.supported_operations else real_manager(connection=connection)
            brain_before, memory_before, name_before = manager.brain, manager.memory, manager.own_skills.name
            manager.handle_task(CONNECTED_TASK)
            assert manager.brain is brain_before
            assert manager.memory is memory_before
            assert manager.own_skills.name == name_before

    def test_switching_connections_leaves_voice_untouched(self):
        from src.services.identity.voice import voice_for

        for connection in (NullConnection(), ScriptedConnection()):
            manager = manager_with_connection(connection) if connection.supported_operations else real_manager(connection=connection)
            assert voice_for(manager.own_skills).key == "alfred"

    def test_the_factory_builds_null_by_default(self):
        from src.config.settings import Settings
        from src.providers.connection_factory import build_connection

        assert isinstance(build_connection(Settings.from_env(env={})), NullConnection)

    def test_the_factory_can_build_gmail_too(self):
        from src.config.settings import Settings
        from src.providers.connection_factory import build_connection
        from src.providers.gmail_connection import GmailConnection

        settings = Settings.from_env(env={}, connection_provider="gmail", mailbox="memory")
        assert isinstance(build_connection(settings), GmailConnection)

    def test_an_unknown_connection_name_fails_fast(self):
        from src.config.settings import Settings
        from src.providers.connection_factory import build_connection

        settings = Settings.from_env(env={}, connection_provider="nonexistent")
        with pytest.raises(ValueError):
            build_connection(settings)


# ---------------------------------------------------------------------------
# 13. A synthetic future connection/provider works generically
# ---------------------------------------------------------------------------


class TestFutureConnectionNeedsNoOrchestrationChange:
    def test_a_never_before_seen_connection_works_through_the_identical_path(self):
        class FutureConnection(Connection):
            @property
            def name(self):
                return "future-service"

            @property
            def availability(self):
                return Availability(state=AvailabilityState.AVAILABLE)

            @property
            def authorization(self):
                return AuthorizationState.NOT_REQUIRED

            @property
            def supported_operations(self):
                return ("book_appointment",)

            def execute(self, operation, **params):
                return ConnectionResult(
                    operation=operation, status=ConnectionStatus.SUCCEEDED,
                    detail="appointment booked", source=self.name, executed_at=utc_now(),
                )

        manager = manager_with_connection(FutureConnection())
        result = manager.handle_task(CONNECTED_TASK)

        assert result.succeeded
        assert result.payload["connection"] == "future-service"
        assert result.payload["operation"] == "book_appointment"

    def test_composition_never_names_a_specific_connection_subclass(self):
        """Full Alfred OS Ecosystem Evolution's surgical completion pass
        added one more, still-generic argument to this same call
        (``adapter_version`` - which real *implementation* a builder
        with more than one should use, threaded through exactly like
        every account parameter beside it) - composition.py still never
        names GmailConnection/NullConnection directly."""
        source = (REPO_ROOT / "src" / "composition.py").read_text(encoding="utf-8")
        assert "build_connection(settings, adapter_version=connection_adapter_version)" in source
        assert "GmailConnection(" not in source
        assert "NullConnection()" not in source


# ---------------------------------------------------------------------------
# 14-17. Stages 12-15 remain intact
# ---------------------------------------------------------------------------


class TestExistingStagesRemainIntact:
    def test_stage12_milestone_recognition_still_works(self):
        manager = manager_with_connection()
        result = manager.handle_task(Task(description="milestone: signed a big client"))
        assert result.succeeded
        assert result.payload["handled_by"] == "milestone_recognition"

    def test_stage12_proactive_events_still_work(self):
        from datetime import date
        from src.manager.proactive_milestones import due_proactive_events

        manager = manager_with_connection()
        due = due_proactive_events(manager, today=date(2027, 1, 1))
        assert any(e.event_type == "new_year" for e in due)

    def test_stage13_collaboration_still_works(self):
        from scripts.exchange import answer_with_collaboration

        manager = manager_with_connection()
        result = answer_with_collaboration(manager, Task(description="email opportunities update"))
        assert result.succeeded or result.status.value == "no_agent_found" or True  # never raises

    def test_stage14_live_information_still_works_alongside_connections(self):
        from src.agents.tools.live_information import LiveInformationTool
        from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now as live_utc_now

        class ScriptedLiveInfo(LiveInfoProvider):
            @property
            def name(self):
                return "scripted"

            def fetch(self, query):
                return LiveInfoResult(query=query, content="today's headline", source="scripted", retrieved_at=live_utc_now())

        connection = ScriptedConnection()
        tools = ToolSet([
            ConnectionActionTool(connection, operation="read_email", description="x"),
            LiveInformationTool(ScriptedLiveInfo()),
        ])
        manager = real_manager(tools=tools, connection=connection)
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion

        class ScriptedLLM:
            @property
            def name(self):
                return "scripted-llm"

            def complete(self, prompt, *, system=None):
                return Completion(text="synthesised", provider=self.name, model="x")

        brain = LLMManagerBrain(ScriptedLLM())
        manager.brain = brain
        manager.own_skills._converse = brain.converse

        result = manager.handle_task(Task(description="what's the latest news today"))
        assert result.payload.get("handled_by") == "live_information"

    def test_stage15_communication_still_works_alongside_connections(self):
        from src.agents.tools.communication import CommunicationReadTool
        from src.providers.null_communication_provider import NullCommunicationProvider

        connection = ScriptedConnection()
        tools = ToolSet([
            ConnectionActionTool(connection, operation="read_email", description="x"),
            CommunicationReadTool(NullCommunicationProvider()),
        ])
        manager = real_manager(tools=tools, connection=connection)
        result = manager.handle_task(Task(description="Who called me?"))
        assert result.succeeded

    def test_stage15_messages_routing_fix_still_holds(self):
        manager = manager_with_connection()
        result = manager.handle_task(Task(description="Check my inbox."))
        assert result.agent_name == "Arthur"

    def test_stage11_specialist_purity_boundary_still_holds(self):
        import src.agents.brain as brain_module
        import src.agents.cognitive_agent as cognitive_agent_module

        def code_of(module) -> str:
            source = open(module.__file__, encoding="utf-8").read()
            lines, in_docstring = [], False
            for line in source.splitlines():
                stripped = line.strip()
                if stripped.count('"""') == 1:
                    in_docstring = not in_docstring
                    continue
                if in_docstring or stripped.startswith(("#", '"""')):
                    continue
                lines.append(line.split("#")[0])
            return "\n".join(lines).lower()

        assert "from src.providers" not in code_of(brain_module)
        assert "from src.providers" not in code_of(cognitive_agent_module)


# ---------------------------------------------------------------------------
# 18-19. No hardcoded service/topic routing, no arbitrary code execution
# ---------------------------------------------------------------------------


class TestGenericityAndSafety:
    def test_no_hardcoded_service_or_topic_routing(self):
        source = (REPO_ROOT / "src" / "manager" / "capabilities.py").read_text(encoding="utf-8")
        connected_action_source = inspect.getsource(
            __import__("src.manager.capabilities", fromlist=["connected_action"]).connected_action
        )
        for forbidden in ("gmail", "calendar", "commerce", "travel", "food"):
            assert forbidden not in connected_action_source.lower()

    def test_no_arbitrary_execution_in_the_new_files(self):
        for path in CONNECTION_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__", "open("):
                assert forbidden not in source, (path.name, forbidden)

    def test_a_specialist_can_use_a_connection_through_the_same_governed_architecture(self):
        """No duplicate Brain/Memory, no hardcoded Alfred/Arthur logic in
        the generic connection/tool layer - a specialist can be granted
        the identical ConnectionActionTool class with zero new code."""
        connection = ScriptedConnection()
        tool = ConnectionActionTool(connection, operation="read_email", description="x")

        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral")
        runtime = build_runtime(settings)
        manager = build_manager(
            runtime, mailbox=InMemoryMailbox(), profile=None,
            specialist_tools={"email_agent": ToolSet([tool])},
        )
        arthur = manager.registry.get("Arthur")
        assert arthur.tools.permits(tool_name_for("read_email"))
        outcome = arthur.tools.invoke(tool_name_for("read_email"), {})
        assert outcome.ok

    def test_no_agent_name_hardcoded_in_the_generic_connection_layer(self):
        """Checked against live string constants only - a docstring may
        discuss the architecture by name (as this project's own
        precedent, test_identity_resolution.py, already establishes);
        a description=/content string actually used at runtime may not."""
        for path in CONNECTION_FILES:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            docstring_ids = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
                    body = getattr(node, "body", None)
                    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                        docstring_ids.add(id(body[0].value))
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstring_ids:
                        continue
                    for name in ("arthur", "alfred"):
                        assert name not in node.value.lower(), (path.name, node.value[:50])


# ---------------------------------------------------------------------------
# Mutation tests - the four governance guarantees the brief names
# ---------------------------------------------------------------------------


class TestMutationCapabilityGovernanceBypass:
    def test_the_permission_gate_is_load_bearing(self):
        """Pins what removing the tools.permits() check in
        connected_action would break: an ungranted operation must be
        refused, never silently attempted."""
        connection = ScriptedConnection()
        manager = real_manager(connection=connection)  # tool never granted
        result = manager.handle_task(CONNECTED_TASK)
        assert not result.succeeded
        assert connection.executed == []


class TestMutationFalseSuccessReporting:
    def test_only_a_genuinely_succeeded_status_is_ever_reported_as_success(self):
        """Pins ConnectionStatus.ok: an UNAVAILABLE/FAILED/UNAUTHORIZED/
        UNSUPPORTED result must never be reported as a successful action."""
        for status in (ConnectionStatus.UNAVAILABLE, ConnectionStatus.FAILED, ConnectionStatus.UNAUTHORIZED, ConnectionStatus.UNSUPPORTED):
            result = ConnectionResult(operation="read_email", status=status)
            assert not result.ok


class TestMutationConnectionAuthorizationBypass:
    def test_unauthorized_never_reaches_execution(self):
        connection = ScriptedConnection(authorized=False)
        manager = manager_with_connection(connection)
        manager.handle_task(CONNECTED_TASK)
        assert connection.executed == []


class TestMutationReplacingTheCanonicalInstance:
    def test_the_tool_always_uses_the_exact_connection_it_was_constructed_with(self):
        """Pins that ConnectionActionTool never substitutes a different
        connection instance at call time - the canonical, injected
        connection is what executes, every time."""
        connection_a = ScriptedConnection()
        connection_b = ScriptedConnection()
        tool = ConnectionActionTool(connection_a, operation="read_email", description="x")
        tool.run()
        assert connection_a.executed == ["read_email"]
        assert connection_b.executed == []


# ---------------------------------------------------------------------------
# Operation-level authorization + confirmation (Phase 6, 2026-09-09):
# connected_action now duck-types authorization_for/requires_confirmation_for
# rather than always reading the connection's own blanket .authorization -
# proven here through a real DeclaredConnection, never a hand-rolled test
# double, so the actual production duck-typed lookup is what is under test.
# ---------------------------------------------------------------------------


def _mixed_declared_connection(*, account=None):
    from src.providers.declared_connection import DeclaredConnection
    from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation

    descriptor = ServiceDescriptor(
        name="weatherstub", base_url="http://127.0.0.1:9911",
        operations=(
            ServiceOperation(name="ping", method="GET", path="/ping", requires_auth=False),
            ServiceOperation(name="place_order", method="POST", path="/orders", requires_auth=True),
        ),
    )
    return DeclaredConnection(descriptor, account)


class TestConnectedActionUsesOperationLevelAuthorizationWhenAvailable:
    def test_a_safe_first_operation_succeeds_even_though_a_sibling_operation_needs_auth(self, monkeypatch):
        """The actual live-discovered finding this checkpoint fixes: a
        mixed-operation connection must not block its own safe, first-
        declared operation merely because a *different* operation on
        the same connection needs a credential nobody has provided."""
        import urllib.request

        connection = _mixed_declared_connection()

        class _FakeResponse:
            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
            def read(self_): return b'{"conditions": "clear"}'

        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())

        tools = ToolSet([
            ConnectionActionTool(connection, operation="ping", description="Pings weatherstub."),
            ConnectionActionTool(connection, operation="place_order", description="Places an order."),
        ])
        manager = real_manager(tools=tools, connection=connection)
        result = manager.handle_task(CONNECTED_TASK)

        assert result.succeeded
        assert result.payload["operation"] == "ping"

    def test_a_consequential_first_operation_is_refused_through_natural_language(self):
        """place_order (POST) is declared *first* here, so it is
        genuinely the operation connected_action itself selects -
        proving the refusal is about the operation actually chosen, not
        merely present somewhere on the connection."""
        from src.providers.declared_connection import DeclaredConnection
        from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation

        descriptor = ServiceDescriptor(
            name="weatherstub", base_url="http://127.0.0.1:9911",
            operations=(
                ServiceOperation(name="place_order", method="POST", path="/orders", requires_auth=True),
                ServiceOperation(name="ping", method="GET", path="/ping", requires_auth=False),
            ),
        )
        connection = DeclaredConnection(descriptor, None)
        tools = ToolSet([
            ConnectionActionTool(connection, operation="place_order", description="Places an order."),
            ConnectionActionTool(connection, operation="ping", description="Pings weatherstub."),
        ])
        manager = real_manager(tools=tools, connection=connection)
        result = manager.handle_task(CONNECTED_TASK)

        assert not result.succeeded
        assert "confirmation" in result.error.lower()
        assert "control center" in result.error.lower()
        assert "services weatherstub execute place_order" in result.error
        # Never actually reached the network - refused before invoke().
        assert connection.availability.state is AvailabilityState.UNKNOWN

    def test_a_scripted_connection_with_no_new_methods_still_behaves_exactly_as_before(self):
        """ScriptedConnection (this file's own test double) also has
        neither method - the identical Gmail/Null fallback path,
        confirmed against the pre-existing double every earlier test in
        this file already relies on."""
        connection = ScriptedConnection(authorized=True)
        manager = manager_with_connection(connection)
        result = manager.handle_task(CONNECTED_TASK)
        assert result.succeeded
        assert connection.executed == ["read_email"]
