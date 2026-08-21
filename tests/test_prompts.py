"""Tests for prompt construction and answer parsing.

No provider, no model, no Manager - these are pure functions.
"""

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task
from src.manager.prompts import (
    ROUTING_SYSTEM_PROMPT,
    build_routing_prompt,
    parse_routing_answer,
)


@pytest.fixture
def agents():
    return [
        EchoAgent(name="alpha_agent", capabilities={"email", "inbox"},
                  description="Handles email."),
        EchoAgent(name="beta_agent", capabilities={"calendar"},
                  description="Handles scheduling."),
    ]


class TestSystemPrompt:
    def test_states_the_routing_role(self):
        assert "rout" in ROUTING_SYSTEM_PROMPT.lower()

    def test_demands_a_bare_name_or_none(self):
        assert "NONE" in ROUTING_SYSTEM_PROMPT
        assert "exactly one agent name" in ROUTING_SYSTEM_PROMPT


class TestBuildRoutingPrompt:
    def test_includes_the_task_description(self, agents):
        prompt = build_routing_prompt(Task(description="send an email"), agents)
        assert "send an email" in prompt

    def test_includes_every_agent_name(self, agents):
        prompt = build_routing_prompt(Task(description="x"), agents)
        assert "alpha_agent" in prompt and "beta_agent" in prompt

    def test_includes_capabilities_and_descriptions(self, agents):
        prompt = build_routing_prompt(Task(description="x"), agents)
        assert "email, inbox" in prompt
        assert "Handles email." in prompt

    def test_is_deterministic_regardless_of_agent_order(self, agents):
        task = Task(description="send an email")
        assert build_routing_prompt(task, agents) == build_routing_prompt(
            task, list(reversed(agents))
        )

    def test_never_includes_the_payload(self, agents):
        """Payloads may carry credentials; routing does not need them."""
        task = Task(description="send an email", payload={"api_key": "SUPER_SECRET_VALUE"})
        assert "SUPER_SECRET_VALUE" not in build_routing_prompt(task, agents)

    def test_handles_an_empty_agent_list(self):
        assert "Available agents:" in build_routing_prompt(Task(description="x"), [])


class TestParseRoutingAnswer:
    VALID = {"alpha_agent", "beta_agent"}

    def test_accepts_an_exact_name(self):
        assert parse_routing_answer("alpha_agent", self.VALID) == "alpha_agent"

    def test_ignores_surrounding_whitespace(self):
        assert parse_routing_answer("  alpha_agent \n", self.VALID) == "alpha_agent"

    def test_is_case_insensitive_but_returns_the_registered_spelling(self):
        assert parse_routing_answer("ALPHA_AGENT", self.VALID) == "alpha_agent"

    def test_strips_common_decoration(self):
        assert parse_routing_answer('"alpha_agent".', self.VALID) == "alpha_agent"
        assert parse_routing_answer("**alpha_agent**", self.VALID) == "alpha_agent"

    def test_reads_only_the_first_line(self):
        answer = "alpha_agent\nbeta_agent\nand also gamma"
        assert parse_routing_answer(answer, self.VALID) == "alpha_agent"

    def test_none_answer_means_no_agent(self):
        assert parse_routing_answer("NONE", self.VALID) is None
        assert parse_routing_answer("none", self.VALID) is None

    def test_rejects_an_unregistered_name(self):
        """The hallucination guard: a model cannot invent an agent."""
        assert parse_routing_answer("gmail_agent", self.VALID) is None

    def test_rejects_prose(self):
        assert parse_routing_answer(
            "I think the best choice here is alpha_agent.", self.VALID
        ) is None

    def test_rejects_empty_and_whitespace(self):
        assert parse_routing_answer("", self.VALID) is None
        assert parse_routing_answer("   \n  ", self.VALID) is None

    def test_rejects_everything_when_no_agents_are_valid(self):
        assert parse_routing_answer("alpha_agent", set()) is None
