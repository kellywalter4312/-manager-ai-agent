"""Phase 7 - Universal Connection Layer.

Two things this file proves, neither a new architecture - both
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
   given their own ``Connection`` instance the identical way
   ``governance_log`` was already proven independent
   (``tests/test_governance.py::TestAgentIndependenceIsPreserved``),
   genuinely never share, leak into, or affect each other's
   connection - through the real, unmodified ``execute()``/
   ``connected_action`` dispatch path, never inspected by mocking it
   away.
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


def _agent_with_own_connection(name: str, connection: Connection) -> ManagerSkills:
    return ManagerSkills(
        _identity(name),
        connection=connection,
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
