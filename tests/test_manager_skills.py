"""Tests for the Manager's own capabilities and self-vs-delegate decisions.

The central claim of Milestone 8: the Manager is a CEO, not a switchboard. It
performs work it is best suited to, delegates what a specialist owns, and
judges what comes back.
"""

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.echo_agent import EchoAgent
from src.core.contracts import FINDINGS_KEY, AgentSpec, Task, TaskResult, TaskStatus
from src.manager.manager import ManagerAgent, MissionOutcome
from src.manager.manager_skills import ManagerSkills
from src.registry.agent_registry import AgentRegistry
from src.services.identity.identity import AgentIdentity

IDENTITY = AgentIdentity(
    name="manager",
    role="Orchestrator",
    description="Coordinates missions and reports on capabilities.",
    capabilities=frozenset({"capabilities", "status", "roster", "report"}),
)


def build(specialists=()):
    registry = AgentRegistry()
    skills = ManagerSkills(IDENTITY, roster=registry.all_agents)
    manager = ManagerAgent(registry=registry, own_skills=skills)
    for agent in specialists:
        manager.register_agent(agent)
    return manager


@pytest.fixture
def email_like():
    return EchoAgent(name="email_agent", capabilities={"email", "inbox", "job", "opportunity"})


class TestManagerSkillsIsAnOrdinaryAgent:
    def test_is_a_base_agent(self):
        assert isinstance(ManagerSkills(IDENTITY), BaseAgent)

    def test_spec_comes_from_identity(self):
        spec = ManagerSkills(IDENTITY).spec
        assert isinstance(spec, AgentSpec)
        assert spec.name == "manager"
        assert "roster" in spec.capabilities

    def test_editing_identity_changes_the_spec(self):
        """What the Manager takes on is still a document edit, not a code change.

        Stated with `roster` rather than `plan`: a declared word is offered
        only if some registered handler answers it, and nothing has ever
        implemented planning. The intent of this test is unchanged - editing
        the document changes the spec - and the companion below covers the
        half of the rule that a single declaration cannot show.
        """
        other = AgentIdentity(name="boss", description="d", capabilities=frozenset({"roster"}))
        assert ManagerSkills(other).spec.capabilities == {"roster"}

    def test_declaring_an_unhandled_capability_does_not_add_it(self):
        """The other half: a word with no handler behind it is dropped.

        `plan` and `coordinate` sat in IDENTITY.md from the first commit with
        nothing implementing them, and a task asking for a plan was answered
        with a roster listing and reported as done. Declaring a capability
        does not create one.
        """
        other = AgentIdentity(
            name="boss",
            description="d",
            capabilities=frozenset({"roster", "plan", "coordinate"}),
        )
        assert ManagerSkills(other).spec.capabilities == {"roster"}

    def test_it_is_registered_like_any_specialist(self):
        manager = build()
        assert "manager" in manager.registry

    def test_registering_is_optional(self):
        assert ManagerAgent().own_skills is None


class TestSelfVersusDelegate:
    def test_delegates_domain_work_to_the_specialist(self, email_like):
        manager = build([email_like])
        decision = manager.diagnose(Task(description="check my inbox for job opportunities"))
        assert decision.agent_name == "email_agent"

    def test_handles_coordination_work_itself(self, email_like):
        manager = build([email_like])
        decision = manager.diagnose(Task(description="report your capabilities and roster"))
        assert decision.agent_name == "manager"

    def test_full_loop_delegates(self, email_like):
        manager = build([email_like])
        result = manager.handle_task(Task(description="scan my inbox for job opportunities"))
        assert result.agent_name == "email_agent"
        assert not manager.handled_it_myself(result)

    def test_full_loop_self_handles(self, email_like):
        manager = build([email_like])
        result = manager.handle_task(Task(description="what are your capabilities?"))
        assert result.status is TaskStatus.COMPLETED
        assert manager.handled_it_myself(result)

    def test_does_not_delegate_merely_because_a_specialist_exists(self, email_like):
        """Coordination work stays with the Manager even with specialists present."""
        manager = build([email_like, EchoAgent(name="other", capabilities={"weather"})])
        assert manager.diagnose(Task(description="status report")).agent_name == "manager"

    def test_does_not_hoard_work_it_is_not_suited_to(self, email_like):
        manager = build([email_like])
        assert manager.diagnose(Task(description="read my email")).agent_name == "email_agent"


class TestManagerSkillsOutput:
    def test_reports_its_own_capabilities(self, email_like):
        manager = build([email_like])
        result = manager.handle_task(Task(description="list your capabilities"))
        assert "roster" in result.output

    def test_reports_the_specialist_roster(self, email_like):
        manager = build([email_like])
        result = manager.handle_task(Task(description="list your capabilities"))
        assert "email_agent" in result.output

    def test_payload_carries_structured_roster(self, email_like):
        manager = build([email_like])
        result = manager.handle_task(Task(description="list your capabilities"))
        assert "email_agent" in result.payload["specialists"]
        assert "roster" in result.payload["own_capabilities"]

    def test_copes_with_no_specialists(self):
        result = build().handle_task(Task(description="list your capabilities"))
        assert "none registered" in result.output

    def test_roster_reflects_agents_added_after_construction(self, email_like):
        manager = build()
        manager.register_agent(email_like)
        assert "email_agent" in manager.handle_task(Task(description="roster")).output


class TestReviewMission:
    def _task(self):
        return Task(description="check my inbox for job opportunities")

    def test_accepts_a_result_with_findings(self):
        task = self._task()
        result = TaskResult.success(task, "email_agent", "text", payload={FINDINGS_KEY: ("a",)})
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.ACCEPTED

    def test_reports_nothing_new_for_an_empty_findings_list(self):
        task = self._task()
        result = TaskResult.success(task, "email_agent", "text", payload={FINDINGS_KEY: ()})
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.NOTHING_NEW

    def test_a_text_only_result_is_accepted_not_treated_as_empty(self):
        """No payload means 'unknown', which is not the same as 'found nothing'."""
        task = self._task()
        result = TaskResult.success(task, "email_agent", "text")
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.ACCEPTED

    def test_failure_is_retryable(self):
        task = self._task()
        result = TaskResult.failure(task, "email_agent", "mailbox unavailable")
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.RETRY

    def test_no_agent_found_is_not_retryable(self):
        task = self._task()
        result = TaskResult.no_agent(task, "nothing matches")
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.FAILED

    def test_contract_breach_is_rejected(self):
        task = self._task()
        result = TaskResult.rejected(task, "email_agent", "wrong type")
        assert ManagerAgent().review_mission(task, result) is MissionOutcome.REJECTED


class TestPayloadRoundTrip:
    def test_payload_defaults_to_none(self):
        task = Task(description="x")
        assert TaskResult.success(task, "a", "out").payload is None

    def test_payload_survives_the_full_loop(self, email_like):
        class PayloadAgent(BaseAgent):
            @property
            def spec(self):
                return AgentSpec(name="p", description="d", capabilities={"payload"})

            def execute(self, task):
                return TaskResult.success(task, "p", "done", payload={FINDINGS_KEY: (1, 2)})

        manager = build([PayloadAgent()])
        result = manager.handle_task(Task(description="payload please"))
        assert result.payload[FINDINGS_KEY] == (1, 2)

    def test_assessor_does_not_strip_the_payload(self):
        task = Task(description="x")
        result = TaskResult.success(task, "a", "out", payload={"k": "v"})
        from src.manager.assessor import ContractAssessor

        assert ContractAssessor().assess(task, "a", result).payload == {"k": "v"}
