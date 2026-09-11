"""Phase 7 - Universal Connection Layer.

Three things this file proves, neither a new architecture - all
strengthening what Stage 16/Phase 6 already built:

1. ``Connection``'s operation-level extension protocol
   (``authorization_for``/``requires_confirmation_for``/
   ``parameters_for``/``operation_description_for``) is now part of
   the base ABC itself (``src/providers/connection.py``), not a
   convention a caller had to discover by reading
   ``src/manager/capabilities.py``'s own ``getattr(..., None)`` calls.
   Every existing connection (``NullConnection``, Gmail) is completely
   unaffected - it inherits an honest, blanket-level default it never
   had to write. A brand-new future connection *kind* - the
   extensibility test Phase 7 itself asks for - now gets this for
   free too: implement only the five members ``Connection`` already
   required and it is immediately reachable through the identical,
   unmodified ``connected_action`` mechanism, proven below with a
   connection this file invents on the spot and that has never
   existed anywhere else in this project.

2. Two independently constructed agents (``ManagerSkills``), each
   given their own ``ConnectionResolver`` the identical way
   ``governance_log`` was already proven independent
   (``tests/test_governance.py::TestAgentIndependenceIsPreserved``),
   genuinely never share, leak into, or affect each other's
   connection - through the real, unmodified ``execute()``/
   ``connected_action`` dispatch path, never inspected by mocking it
   away.

3. ONE agent, equipped with MULTIPLE simultaneous connections in one
   ``ConnectionResolver`` - the actual canonical model Phase 7
   requires - genuinely reaches whichever one the request's own words
   name, dynamically, through the identical real dispatch path, with
   the configured default winning whenever nothing else is named
   (every existing single-connection installation's behaviour,
   unchanged).
"""

from __future__ import annotations

from src.agents.tools.connection import ConnectionActionTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.manager_skills import ManagerSkills
from src.providers.availability import Availability, AvailabilityState
from src.providers.connection import (
    AuthorizationState,
    Connection,
    ConnectionResult,
    ConnectionStatus,
    utc_now,
)
from src.providers.connection_resolver import ConnectionResolver
from src.providers.null_connection import NullConnection
from src.services.identity.identity import AgentIdentity


def _identity(name: str) -> AgentIdentity:
    return AgentIdentity(name=name, role="Manager", capabilities=frozenset({"handout"}))


# ---------------------------------------------------------------------------
# 1. The operation-level extension protocol is now on the base ABC itself.
# ---------------------------------------------------------------------------


class TestTheOperationLevelExtensionProtocolIsGenuinelyOnTheBaseABC:
    def test_an_existing_connection_with_no_overrides_inherits_the_honest_defaults(self):
        """NullConnection never wrote any of these four methods - it now
        answers all of them anyway, exactly reproducing what the old
        getattr(..., None) call-site fallback already did (never a
        behaviour change, only where the fallback logic lives)."""
        connection = NullConnection()
        assert connection.authorization_for("anything") == connection.authorization
        assert connection.requires_confirmation_for("anything") is False
        assert connection.parameters_for("anything") == ()
        assert connection.operation_description_for("anything") == ""

    def test_a_brand_new_future_connection_kind_needs_only_the_five_original_members(self):
        """The extensibility test Phase 7 itself asks for: 'could we add
        a completely new connection category without rewriting the
        core connection architecture?' - proven by inventing one here,
        on the spot, that has never existed anywhere else in this
        project, implementing nothing beyond what Connection already
        required before this checkpoint, and confirming it still gets
        real, working operation-level answers for free."""

        class _SomeFutureConnectionKind(Connection):
            """A stand-in for literally any category this project has
            not conceived of yet - a device, a media system, a data
            source, anything with the same shape a Connection already
            asks for and nothing more."""

            @property
            def name(self) -> str:
                return "future-kind"

            @property
            def availability(self) -> Availability:
                return Availability(state=AvailabilityState.AVAILABLE)

            @property
            def authorization(self) -> AuthorizationState:
                return AuthorizationState.NOT_REQUIRED

            @property
            def supported_operations(self) -> "tuple[str, ...]":
                return ("do_the_thing",)

            def execute(self, operation: str, **params) -> ConnectionResult:
                return ConnectionResult(
                    operation=operation, status=ConnectionStatus.SUCCEEDED,
                    source=self.name, executed_at=utc_now(),
                )

        connection = _SomeFutureConnectionKind()
        # No KeyError, no AttributeError, no special-casing anywhere in
        # this module needed to ask these questions.
        assert connection.authorization_for("do_the_thing").grants_access
        assert connection.requires_confirmation_for("do_the_thing") is False
        assert connection.parameters_for("do_the_thing") == ()
        result = connection.execute("do_the_thing")
        assert result.ok and result.source == "future-kind"


# ---------------------------------------------------------------------------
# 2. Agent independence: two agents, two connections, never shared.
# ---------------------------------------------------------------------------


class _FakeConnection(Connection):
    """A minimal, hand-written, distinguishable Connection double - real
    enough to prove routing, never a mock that merely records calls
    without genuinely implementing the contract."""

    def __init__(self, name: str):
        self._name = name
        self.executed: "list[tuple[str, dict]]" = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def availability(self) -> Availability:
        return Availability(state=AvailabilityState.AVAILABLE)

    @property
    def authorization(self) -> AuthorizationState:
        return AuthorizationState.AUTHORIZED

    @property
    def supported_operations(self) -> "tuple[str, ...]":
        return ("ping",)

    def execute(self, operation: str, **params) -> ConnectionResult:
        self.executed.append((operation, dict(params)))
        return ConnectionResult(
            operation=operation, status=ConnectionStatus.SUCCEEDED,
            source=self._name, executed_at=utc_now(),
        )


def _resolver(*connections: Connection, default: "str | None" = None) -> ConnectionResolver:
    by_name = {c.name: c for c in connections}
    return ConnectionResolver(by_name, default or connections[0].name)


def _agent_with_own_connection(name: str, connection: Connection) -> ManagerSkills:
    return ManagerSkills(
        _identity(name),
        connections=_resolver(connection),
        tools=ToolSet([
            ConnectionActionTool(connection, operation="ping", description="Pings the connection.", parameters=())
        ]),
    )


class TestAgentToConnectionIndependence:
    def test_two_agents_each_reach_only_their_own_connection_through_real_dispatch(self):
        """The Independence requirement, proven through the real,
        unmodified ManagerSkills.execute() -> connected_action ->
        _governance_check -> ConnectionActionTool -> Connection.execute
        path - not by inspecting wiring, by actually running two
        separate agents and checking which connection each one really
        reached."""
        alfred_connection = _FakeConnection("alfred-service")
        arthur_connection = _FakeConnection("arthur-service")
        alfred = _agent_with_own_connection("Alfred", alfred_connection)
        arthur = _agent_with_own_connection("Arthur", arthur_connection)

        alfred_result = alfred.execute(Task(description="check my connected account: ping"))
        arthur_result = arthur.execute(Task(description="check my connected account: ping"))

        assert alfred_result.succeeded, alfred_result.error
        assert arthur_result.succeeded, arthur_result.error
        # Each agent's own action reached exactly its own connection -
        # never the other's, never both.
        assert alfred_connection.executed == [("ping", {})]
        assert arthur_connection.executed == [("ping", {})]
        assert alfred_connection is not arthur_connection

    def test_replacing_one_agents_connection_never_affects_a_different_agents_own(self):
        """Replacement is construction-time, the identical pattern every
        other provider seam in this project already uses (Connection
        included - see composition.py). Proven here at the agent
        level: giving 'Alfred' a new connection instance must never
        reach into, mutate, or otherwise affect a completely different
        agent's own, independently-held connection."""
        arthur_connection = _FakeConnection("arthur-service")
        arthur = _agent_with_own_connection("Arthur", arthur_connection)

        alfred_v1_connection = _FakeConnection("alfred-service-v1")
        _agent_with_own_connection("Alfred", alfred_v1_connection).execute(
            Task(description="check my connected account: ping")
        )
        # Alfred's own connection is "replaced" by constructing a fresh
        # agent with a different one - never a mutation of the old
        # instance, and never anything Arthur's own connection observes.
        alfred_v2_connection = _FakeConnection("alfred-service-v2")
        _agent_with_own_connection("Alfred", alfred_v2_connection).execute(
            Task(description="check my connected account: ping")
        )

        arthur.execute(Task(description="check my connected account: ping"))

        assert alfred_v1_connection.executed == [("ping", {})]
        assert alfred_v2_connection.executed == [("ping", {})]
        assert arthur_connection.executed == [("ping", {})]


# ---------------------------------------------------------------------------
# 3. ONE agent, MULTIPLE simultaneous connections - the actual canonical
# model this checkpoint exists to prove, not merely two separate agents
# each with one.
# ---------------------------------------------------------------------------


class TestMultipleSimultaneousConnectionsFromOneAgent:
    def _agent(self) -> "tuple[ManagerSkills, _FakeConnection, _FakeConnection]":
        primary = _FakeConnection("primarysvc")
        secondary = _FakeConnection("secondarysvc")
        resolver = _resolver(primary, secondary, default="primarysvc")
        agent = ManagerSkills(
            _identity("Alfred"),
            connections=resolver,
            tools=ToolSet([
                ConnectionActionTool(primary, operation="ping", description="Pings primarysvc.", parameters=()),
                ConnectionActionTool(secondary, operation="ping", description="Pings secondarysvc.", parameters=()),
            ]),
        )
        return agent, primary, secondary

    def test_ordinary_phrasing_with_no_connection_named_reaches_the_default(self):
        """Every existing single-connection installation's behaviour,
        unchanged: naming no connection at all means the configured
        default, even though a second one now genuinely coexists."""
        agent, primary, secondary = self._agent()
        result = agent.execute(Task(description="check my connected account: ping"))
        assert result.succeeded, result.error
        assert primary.executed == [("ping", {})]
        assert secondary.executed == []

    def test_naming_the_non_default_connection_reaches_it_instead(self):
        """Dynamic resolution at execution time (Phase 7's own
        requirement): the SAME agent, the SAME running process, no
        rebuild - just different words routing to a different,
        already-equipped connection."""
        agent, primary, secondary = self._agent()
        result = agent.execute(Task(description="check my connected account: ping secondarysvc"))
        assert result.succeeded, result.error
        assert secondary.executed == [("ping", {})]
        assert primary.executed == []

    def test_adding_one_never_disconnects_or_shadows_the_other(self):
        """Both connections genuinely, simultaneously usable in the
        same session - not a slot that gets replaced by declaring a
        second one."""
        agent, primary, secondary = self._agent()
        agent.execute(Task(description="check my connected account: ping"))
        agent.execute(Task(description="check my connected account: ping secondarysvc"))
        agent.execute(Task(description="check my connected account: ping"))
        assert primary.executed == [("ping", {}), ("ping", {})]
        assert secondary.executed == [("ping", {})]

    def test_no_fixed_connection_count_limit(self):
        """No "3 connections", no "10 connections" - however many are
        declared is however many ConnectionResolver holds, and
        connected_action reaches every one of them by name."""
        connections = [_FakeConnection(f"svc{i}") for i in range(25)]
        resolver = _resolver(*connections, default="svc0")
        agent = ManagerSkills(
            _identity("Alfred"),
            connections=resolver,
            tools=ToolSet([
                ConnectionActionTool(c, operation="ping", description=f"Pings {c.name}.", parameters=())
                for c in connections
            ]),
        )
        assert len(resolver.names) == 25
        result = agent.execute(Task(description="check my connected account: ping svc17"))
        assert result.succeeded, result.error
        assert connections[17].executed == [("ping", {})]
        assert all(c.executed == [] for i, c in enumerate(connections) if i != 17)
