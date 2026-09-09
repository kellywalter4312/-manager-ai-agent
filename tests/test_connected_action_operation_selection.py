"""Intent-driven operation selection (2026-09-09), extended with
semantic/description-aware selection (2026-09-09, follow-up).

The last verified Stage 16 limitation: ``connected_action``
(``src/manager/capabilities.py``) always attempted
``connection.supported_operations[0]`` - whichever operation happened to
be declared first, never what the user actually asked for. A connection
with more than one safe operation could only ever be reached through
its first one, regardless of wording.

Closed by ``_select_operation``: for a connection with more than one
operation, the user's own words (the part of the request past the
routing marker - ``_after_marker``, already used by ``forget_this``/
``recall_memory``) are matched against each operation's own declared
*name* (word-split by this codebase's own canonical ``tokenize()`` -
the same function every other word-overlap capability here already
uses), never a fixed word list, never an industry, never a specific
service. A single operation is still trivially selected regardless of
wording - Gmail, Null, and any other legacy single-operation connection
are completely unaffected.

**Follow-up: name alone is not always enough.** A real service can
declare a technically-named operation (``fetch_state``) whose
*description* is what actually says what it does. ``_select_operation``
now matches against each operation's combined vocabulary - its name
*and* its declared description (``_operation_vocabulary``,
duck-typed off ``operation_description_for`` exactly like
``authorization_for``/``requires_confirmation_for`` already are) -
with common English function words stripped (the existing
``src/services/memory/query.py`` ``STOPWORDS`` set, reused rather than
duplicated) so an unrelated request does not look relevant merely
because it shares "the" or "a" with some operation's prose.

Every test here builds its own small ``ServiceDescriptor`` inline,
following the exact precedent ``test_stage16_connections.py`` already
sets - never a shared, growing fixture that would start to look like a
catalogue.
"""

from __future__ import annotations

import pytest

from src.agents.tools.connection import ConnectionActionTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.providers.availability import AvailabilityState
from src.providers.connection import Connection
from src.providers.declared_connection import DeclaredConnection
from src.providers.null_connection import NullConnection
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation


def real_manager(*, tools: "ToolSet | None" = None, connection: "Connection | None" = None, **overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)
    if connection is not None:
        manager.own_skills._connection = connection
    return manager


def _connection(*operations: ServiceOperation) -> DeclaredConnection:
    descriptor = ServiceDescriptor(
        name="genericfixture", base_url="http://127.0.0.1:9199", operations=operations,
    )
    return DeclaredConnection(descriptor, None)


def _manager_for(connection: DeclaredConnection) -> "object":
    tools = ToolSet([
        ConnectionActionTool(connection, operation=op, description=f"Performs {op}.")
        for op in connection.supported_operations
    ])
    return real_manager(tools=tools, connection=connection)


def _task(words: str) -> Task:
    return Task(description=f"check my connected account: {words}")


@pytest.fixture(autouse=True)
def _stub_network(monkeypatch):
    """A genuinely-executed safe operation still makes a real HTTP call
    (DeclaredConnection.execute) - this repo's own test suite blocks
    every real socket, so every test that expects success needs this,
    the identical shape every other DeclaredConnection test already
    uses. Content is irrelevant here - every assertion in this file is
    about *which operation* ran, never the payload it returned."""
    import urllib.request

    class _FakeResponse:
        def __enter__(self_): return self_
        def __exit__(self_, *a): return False
        def read(self_): return b'{}'

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse())


class TestEitherOfTwoSafeOperationsCanBeSelected:
    """Requirement 1: a generic service with two safe operations can
    have either operation selected, purely according to the request's
    own wording."""

    def _two_safe_ops_manager(self):
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
            ServiceOperation(name="list_events", method="GET", path="/events", requires_auth=False),
        )
        return _manager_for(connection)

    def test_a_status_request_selects_get_status(self):
        manager = self._two_safe_ops_manager()
        result = manager.handle_task(_task("what is the status"))
        assert result.succeeded
        assert result.payload["operation"] == "get_status"

    def test_an_events_request_selects_list_events(self):
        manager = self._two_safe_ops_manager()
        result = manager.handle_task(_task("list the events please"))
        assert result.succeeded
        assert result.payload["operation"] == "list_events"


class TestDescriptionAloneCanSelectATechnicallyNamedOperation:
    """Requirement B: a request whose wording barely overlaps an
    operation's bare *name* still reaches it, because its declared
    *description* says what it actually does."""

    def test_a_condition_request_reaches_a_technically_named_operation_via_its_description(self):
        connection = _connection(
            ServiceOperation(
                name="fetch_state", method="GET", path="/state", requires_auth=False,
                description="reports the account's current condition",
            ),
            ServiceOperation(
                name="retrieve_activity", method="GET", path="/activity", requires_auth=False,
                description="lists recent activity log entries",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("what is my current condition"))

        assert result.succeeded
        assert result.payload["operation"] == "fetch_state"

    def test_an_activity_request_reaches_the_other_technically_named_operation(self):
        connection = _connection(
            ServiceOperation(
                name="fetch_state", method="GET", path="/state", requires_auth=False,
                description="reports the account's current condition",
            ),
            ServiceOperation(
                name="retrieve_activity", method="GET", path="/activity", requires_auth=False,
                description="lists recent activity log entries",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("show me the recent activity log"))

        assert result.succeeded
        assert result.payload["operation"] == "retrieve_activity"


class TestNameAndDescriptionCombinedEvidence:
    """Requirement C: when both an operation's name and its description
    carry real evidence, selection still behaves sensibly - the
    combined vocabulary wins over an operation with only weak, partial
    overlap."""

    def test_combined_name_and_description_evidence_outweighs_a_partial_competitor(self):
        connection = _connection(
            ServiceOperation(
                name="get_status", method="GET", path="/status", requires_auth=False,
                description="reports the current operational status",
            ),
            ServiceOperation(
                name="list_events", method="GET", path="/events", requires_auth=False,
                description="a historical log unrelated to live status",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("what is the current operational status"))

        assert result.succeeded
        assert result.payload["operation"] == "get_status"


class TestTwoTechnicalNamesDistinguishedByDescription:
    """Requirement D: two operations whose *names* are both
    non-descriptive/technical are still told apart purely by their
    declared descriptions."""

    def test_two_opaque_names_are_distinguished_by_what_their_descriptions_say(self):
        connection = _connection(
            ServiceOperation(
                name="op_alpha", method="GET", path="/a", requires_auth=False,
                description="reports the current temperature reading",
            ),
            ServiceOperation(
                name="op_beta", method="GET", path="/b", requires_auth=False,
                description="reports the current humidity reading",
            ),
        )
        manager = _manager_for(connection)

        temp_result = manager.handle_task(_task("what is the temperature"))
        assert temp_result.succeeded
        assert temp_result.payload["operation"] == "op_alpha"

        humidity_result = manager.handle_task(_task("what is the humidity"))
        assert humidity_result.succeeded
        assert humidity_result.payload["operation"] == "op_beta"


class TestSemanticAmbiguityAlsoRefusesHonestly:
    """Requirement E: a tie need not come from identical name tokens -
    two materially equivalent *description* matches must refuse just as
    honestly as a name-level tie already does."""

    def test_equally_matching_descriptions_refuse_rather_than_choosing(self):
        connection = _connection(
            ServiceOperation(
                name="op_alpha", method="GET", path="/a", requires_auth=False,
                description="reports the current reading",
            ),
            ServiceOperation(
                name="op_beta", method="GET", path="/b", requires_auth=False,
                description="reports the current reading",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("what is the current reading"))

        assert not result.succeeded
        assert "op_alpha" in result.error
        assert "op_beta" in result.error
        assert connection.availability.state is AvailabilityState.UNKNOWN


class TestRoutingMarkerWordsNeverCountAsIntent:
    """Requirement G: the routing marker itself ('check my connected
    account') is never mistaken for the user's actual intent, even when
    an operation's own description happens to share one of those exact
    words - only the text *past* the marker is ever scored."""

    def test_an_operations_description_sharing_a_marker_word_is_not_favoured_by_that_alone(self):
        connection = _connection(
            ServiceOperation(
                name="op_alpha", method="GET", path="/a", requires_auth=False,
                description="manages the connected account registry",  # shares "connected"/"account" with the marker
            ),
            ServiceOperation(
                name="op_beta", method="GET", path="/b", requires_auth=False,
                description="reports the current reading",
            ),
        )
        manager = _manager_for(connection)

        # The marker itself supplies no usable intent once stripped, and
        # neither description is otherwise implicated - an honest
        # non-match, never an accidental pick of op_alpha merely because
        # its description happens to reuse marker vocabulary.
        result = manager.handle_task(Task(description="check my connected account"))

        assert not result.succeeded

        # With genuine intent past the marker, the correct operation is
        # still reached - proving the mechanism works at all here.
        matched = manager.handle_task(_task("what is the current reading"))
        assert matched.succeeded
        assert matched.payload["operation"] == "op_beta"


class TestTheFirstDeclaredOperationIsNotAutomaticallySelected:
    """Requirement 2, proven directly: 'list_events' is declared
    *first*, but a request that only matches 'get_status' still reaches
    'get_status' - the old operations[0] behaviour is genuinely gone."""

    def test_the_second_declared_operation_wins_when_intent_says_so(self):
        connection = _connection(
            ServiceOperation(name="list_events", method="GET", path="/events", requires_auth=False),
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
        )
        assert connection.supported_operations[0] == "list_events"

        manager = _manager_for(connection)
        result = manager.handle_task(_task("check the status"))

        assert result.succeeded
        assert result.payload["operation"] == "get_status"


class TestAmbiguousRequestsFailHonestly:
    """Requirement 3: two operations that are genuinely, equally
    plausible must never be arbitrarily resolved - the ambiguity is
    reported, not guessed away."""

    def test_a_tied_request_refuses_rather_than_choosing(self):
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
            ServiceOperation(name="set_status", method="GET", path="/status2", requires_auth=False),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("status"))

        assert not result.succeeded
        assert "get_status" in result.error
        assert "set_status" in result.error
        # Never even attempted - refused before either Tool was invoked.
        assert connection.availability.state is AvailabilityState.UNKNOWN


class TestAnUnmatchedRequestFailsHonestly:
    """Requirement 4: nothing in the request overlaps any declared
    operation at all - an honest 'I don't know which' beats a guess."""

    def test_unrelated_wording_is_refused_not_mapped_to_something_close(self):
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
            ServiceOperation(name="list_events", method="GET", path="/events", requires_auth=False),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("play some music"))

        assert not result.succeeded
        assert "get_status" in result.error
        assert "list_events" in result.error
        assert connection.availability.state is AvailabilityState.UNKNOWN

    def test_the_bare_marker_alone_is_also_unmatched_when_multiple_operations_exist(self):
        """No text past the marker at all (_after_marker returns
        nothing) is exactly as honest a non-match as unrelated wording -
        multiple operations means a real decision is required, and
        silence cannot make one."""
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
            ServiceOperation(name="list_events", method="GET", path="/events", requires_auth=False),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(Task(description="check my connected account"))

        assert not result.succeeded

    def test_rich_prose_descriptions_do_not_produce_a_false_match_via_common_words(self):
        """Both descriptions are ordinary English sentences, heavy with
        'the'/'a'/'is' - an unrelated request must still refuse cleanly
        rather than matching on function words alone."""
        connection = _connection(
            ServiceOperation(
                name="op_alpha", method="GET", path="/a", requires_auth=False,
                description="this is the operation that reports the current reading for the device",
            ),
            ServiceOperation(
                name="op_beta", method="GET", path="/b", requires_auth=False,
                description="this is the operation that lists the recent activity for the account",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("play some music please"))

        assert not result.succeeded
        assert connection.availability.state is AvailabilityState.UNKNOWN


class TestConsequentialOperationsStayBlockedFromConversation:
    """Requirement 5: correctly *identifying* a consequential operation
    by intent is not the same as being allowed to run it through
    conversation - the existing confirmation boundary still applies to
    whatever intent-driven selection picks."""

    def test_a_matched_consequential_operation_is_refused_with_the_confirmation_message(self):
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
            ServiceOperation(name="reset_counter", method="POST", path="/counter", requires_auth=True),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("reset the counter"))

        assert not result.succeeded
        assert "confirmation" in result.error.lower()
        assert "control center" in result.error.lower()
        assert "services genericfixture execute reset_counter" in result.error
        assert connection.availability.state is AvailabilityState.UNKNOWN

    def test_a_consequential_operation_matched_only_through_its_description_is_still_blocked(self):
        """Selection now has a second way to find a consequential
        operation - through its description rather than its name - so
        the governance boundary must hold there too, not merely for a
        name-based match."""
        connection = _connection(
            ServiceOperation(
                name="get_status", method="GET", path="/status", requires_auth=False,
                description="reports the current status",
            ),
            ServiceOperation(
                name="op_reset", method="POST", path="/reset", requires_auth=True,
                description="permanently clears the stored counter value",
            ),
        )
        manager = _manager_for(connection)

        result = manager.handle_task(_task("clear the stored counter value"))

        assert not result.succeeded
        assert "confirmation" in result.error.lower()
        assert "control center" in result.error.lower()
        assert "services genericfixture execute op_reset" in result.error
        assert connection.availability.state is AvailabilityState.UNKNOWN


class TestLegacySingleOperationConnectionsAreUnaffected:
    """Requirement 6: Gmail, Null, and any other connection that only
    ever declares one operation take the trivial "no decision to make"
    branch, exactly as before intent-driven selection existed."""

    def test_a_single_operation_connection_needs_no_matching_wording_at_all(self):
        connection = _connection(
            ServiceOperation(name="get_status", method="GET", path="/status", requires_auth=False),
        )
        manager = _manager_for(connection)

        # The bare marker, with no intent words past it at all - would
        # be an honest non-match for two-or-more operations (see
        # TestAnUnmatchedRequestFailsHonestly above), but there is only
        # one operation here, so there is nothing to disambiguate.
        result = manager.handle_task(Task(description="check my connected account"))

        assert result.succeeded
        assert result.payload["operation"] == "get_status"

    def test_null_connection_is_unaffected(self):
        manager = real_manager(connection=NullConnection())
        result = manager.handle_task(Task(description="check my connected account"))
        assert not result.succeeded
        assert "no supported actions" in result.error


class TestNoHardcodingWasIntroduced:
    """Requirement 7, checked as behaviour, not merely as review: every
    real-world domain this checkpoint's own brief explicitly named as an
    example is genuinely absent from the modified source, and the
    mechanism itself is a generic scorer, never a lookup table."""

    def _new_mechanisms_source(self) -> str:
        """Source of exactly the functions this checkpoint added/
        changed, not the whole (7000+ line, many-checkpoints-old) file -
        a whole-file scan would false-flag this project's own, unrelated,
        already-shipped calendar/appointment capability living
        elsewhere in the same module."""
        import inspect

        from src.manager import capabilities as capabilities_module

        return (
            inspect.getsource(capabilities_module._meaningful_words)
            + inspect.getsource(capabilities_module._operation_vocabulary)
            + inspect.getsource(capabilities_module._select_operation)
            + inspect.getsource(capabilities_module.connected_action)
        )

    def test_no_forbidden_industry_or_company_words_appear_in_the_new_mechanism(self):
        text = self._new_mechanisms_source().lower()
        forbidden = (
            "restaurant", "salon", "flight", "taxi", "hotel", "airline",
            "food delivery", "shopping", "onlyfans", "jumia", "mary salon",
            "booking.com", "commerce", "appointment", "stripe", "paypal",
            "amazon", "uber",
        )
        for word in forbidden:
            assert word not in text, word

    def test_no_finite_operation_catalogue_or_service_specific_branch_exists(self):
        """The mechanism reads connection.supported_operations and the
        task's own words - no dict/list of known operation names, no
        'if connection.name ==' branch."""
        text = self._new_mechanisms_source()
        for forbidden in ('connection.name == "', "connection.name == '", 'operation == "read_item"'):
            assert forbidden not in text, forbidden
