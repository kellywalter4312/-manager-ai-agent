"""Tests for the EmailAgent - the first real BaseAgent implementation.

The last class is the one that matters most: it proves a real agent drops into
the existing Manager with no orchestration changes at all.
"""

import logging

import pytest

from src.agents.base_agent import BaseAgent
from src.agents.email.analyzers import AnalysisReport, Analyzer, JobOpportunityAnalyzer
from src.agents.email_agent import AGENT_NAME, EmailAgent
from src.core.contracts import Task, TaskStatus
from src.manager.manager import ManagerAgent
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.mailbox import Mailbox, MailboxError, Outbox
from src.services.email.sample_inbox import sample_inbox


class BrokenMailbox(Mailbox):
    def fetch(self, limit=20, label=None):
        raise MailboxError("upstream unavailable")


class ExplodingAnalyzer(Analyzer):
    @property
    def name(self):
        return "exploding"

    @property
    def capabilities(self):
        return frozenset({"job"})

    def analyze(self, messages):
        raise RuntimeError("kaboom")


class FakeOutbox(Outbox):
    def __init__(self):
        self.sent = []

    def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return "sent-1"


@pytest.fixture
def agent():
    return EmailAgent(sample_inbox())


class TestConstruction:
    def test_is_a_base_agent(self, agent):
        assert isinstance(agent, BaseAgent)

    def test_is_named_email_agent(self, agent):
        assert agent.name == AGENT_NAME == "email_agent"

    def test_requires_a_mailbox(self):
        with pytest.raises(TypeError):
            EmailAgent("not a mailbox")

    def test_rejects_a_non_analyzer(self):
        with pytest.raises(TypeError):
            EmailAgent(InMemoryMailbox(), analyzers=["not an analyzer"])

    def test_rejects_a_non_positive_fetch_limit(self):
        with pytest.raises(ValueError):
            EmailAgent(InMemoryMailbox(), fetch_limit=0)

    def test_defaults_to_the_job_analyzer(self, agent):
        assert any(isinstance(a, JobOpportunityAnalyzer) for a in agent.analyzers)


class TestCapabilities:
    def test_declares_general_email_capabilities(self, agent):
        assert {"email", "inbox", "mail"} <= agent.spec.capabilities

    def test_inherits_capabilities_from_its_analyzers(self, agent):
        assert {"job", "opportunity"} <= agent.spec.capabilities

    def test_a_new_analyzer_widens_the_agent_automatically(self):
        """Registering an analyzer extends routing with no routing changes."""

        class TravelAnalyzer(Analyzer):
            @property
            def name(self):
                return "travel"

            @property
            def capabilities(self):
                return frozenset({"flight", "itinerary"})

            def analyze(self, messages):
                return AnalysisReport(analyzer="travel", considered=len(messages))

        agent = EmailAgent(sample_inbox(), analyzers=[JobOpportunityAnalyzer(), TravelAnalyzer()])
        assert {"flight", "itinerary", "job"} <= agent.spec.capabilities

    def test_description_states_it_cannot_send(self, agent):
        assert "cannot send" in agent.spec.description.lower()


class TestJobAnalysis:
    def test_finds_the_job_emails_in_the_sample_inbox(self, agent):
        result = agent.execute(Task(description="find job opportunities in my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert "job_opportunity" in result.output

    def test_reports_the_real_openings(self, agent):
        result = agent.execute(Task(description="any new job opportunities?"))
        # The three genuine postings in the sample inbox, not the newsletter.
        assert "Northwind" in result.output
        assert "Harborview" in result.output or "Backend Developer" in result.output
        assert "Brightpath" in result.output

    def test_does_not_flag_the_newsletter_or_personal_mail(self, agent):
        result = agent.execute(Task(description="job opportunities please"))
        assert "Coffee on Saturday" not in result.output
        assert "weekly delivery" not in result.output

    def test_output_is_real_analysis_not_an_echo(self, agent):
        result = agent.execute(Task(description="check for job opportunities"))
        assert "[echo" not in result.output
        assert "score" in result.output


class TestGeneralEmailBehaviour:
    def test_summarizes_the_inbox_when_no_analyzer_matches(self, agent):
        result = agent.execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert "message(s) in the inbox" in result.output

    def test_reports_an_empty_inbox_clearly(self):
        agent = EmailAgent(InMemoryMailbox())
        result = agent.execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.COMPLETED
        assert "empty" in result.output.lower()

    def test_summaries_never_include_message_bodies(self, agent):
        result = agent.execute(Task(description="read my inbox"))
        assert "delivery arrives Thursday" not in result.output
        assert "new place near the station" not in result.output


class TestSendingIsOutOfScope:
    def test_refuses_to_send(self, agent):
        result = agent.execute(Task(description="send an email to my team"))
        assert result.status is TaskStatus.FAILED
        assert "not enabled" in result.error

    @pytest.mark.parametrize("verb", ["send", "compose", "reply", "draft", "forward"])
    def test_refuses_every_send_style_request(self, agent, verb):
        result = agent.execute(Task(description=f"{verb} a message to Sam"))
        assert result.status is TaskStatus.FAILED

    def test_cannot_send_structurally_not_just_by_policy(self, agent):
        """With no Outbox it holds no object with a send method."""
        assert agent.can_send is False

    def test_an_injected_outbox_is_recognised_but_still_unimplemented(self):
        agent = EmailAgent(sample_inbox(), outbox=FakeOutbox())
        assert agent.can_send is True
        result = agent.execute(Task(description="send an email to my team"))
        assert result.status is TaskStatus.FAILED
        assert "not implemented" in result.error

    def test_never_actually_sends_anything(self):
        outbox = FakeOutbox()
        agent = EmailAgent(sample_inbox(), outbox=outbox)
        agent.execute(Task(description="send an email to my team"))
        assert outbox.sent == []


class TestFailureHandling:
    def test_a_broken_mailbox_becomes_a_failed_result(self):
        agent = EmailAgent(BrokenMailbox())
        result = agent.execute(Task(description="read my inbox"))
        assert result.status is TaskStatus.FAILED
        assert "upstream unavailable" in result.error

    def test_a_broken_analyzer_becomes_a_failed_result(self):
        agent = EmailAgent(sample_inbox(), analyzers=[ExplodingAnalyzer()])
        result = agent.execute(Task(description="find job opportunities"))
        assert result.status is TaskStatus.FAILED
        assert "kaboom" in result.error

    def test_a_broken_analyzer_is_logged_with_a_traceback(self, caplog):
        agent = EmailAgent(sample_inbox(), analyzers=[ExplodingAnalyzer()])
        with caplog.at_level(logging.ERROR, logger="src"):
            agent.execute(Task(description="find job opportunities"))
        assert "RuntimeError" in caplog.text

    def test_execute_never_raises(self):
        agent = EmailAgent(BrokenMailbox(), analyzers=[ExplodingAnalyzer()])
        assert agent.execute(Task(description="anything at all")) is not None


class TestDropsIntoTheExistingManager:
    """The Milestone 6 claim: a real agent needs no orchestration changes."""

    @pytest.fixture
    def manager(self):
        m = ManagerAgent()
        m.register_agent(EmailAgent(sample_inbox()))
        return m

    def test_the_registry_accepts_it(self, manager):
        assert "email_agent" in manager.registry

    def test_the_existing_router_routes_email_tasks_to_it(self, manager):
        decision = manager.diagnose(Task(description="scan my inbox for job opportunities"))
        assert decision.agent_name == "email_agent"

    def test_a_full_data_loop_produces_real_analysis(self, manager):
        result = manager.handle_task(Task(description="find job opportunities in my email"))
        assert result.status is TaskStatus.COMPLETED
        assert result.agent_name == "email_agent"
        assert "Northwind" in result.output

    def test_the_assessor_accepts_the_result(self, manager):
        result = manager.handle_task(Task(description="read my inbox"))
        assert result.status is TaskStatus.COMPLETED  # not REJECTED

    def test_a_send_request_surfaces_as_a_failed_task(self, manager):
        result = manager.handle_task(Task(description="send an email to the team"))
        assert result.status is TaskStatus.FAILED

    def test_orchestration_modules_stay_email_unaware(self):
        """Structural check: the Manager still knows nothing about email."""
        import src.manager.manager as manager_module

        source = open(manager_module.__file__, encoding="utf-8").read().lower()
        assert "email" not in source and "mailbox" not in source
