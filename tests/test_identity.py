"""Tests for IDENTITY.md and SOUL.md - who the Manager is, and how it behaves."""

import pytest

from src.core.contracts import AgentSpec
from src.services.identity.identity import (
    AgentIdentity,
    IdentityError,
    load_identity,
    parse_identity,
)
from src.services.identity.soul import (
    DEFAULT_NEVER,
    BehaviourPolicy,
    SoulError,
    load_soul,
    parse_soul,
)

IDENTITY_SAMPLE = """
# IDENTITY

## Name
manager

## Role
Orchestrator and coordinator

## Description
Receives missions and decides how they should be carried out.

## Capabilities
capabilities, status, roster, report

## Delegates
- email_agent - reading mailboxes
"""

SOUL_SAMPLE = """
# SOUL

## Principles
- Report, do not act.
- Never invent.

## Policy
report_only: true
report_when_nothing_new: false
max_attempts_per_mission: 2
max_opportunities_reported: 3
include_ambiguous: true

## Never
send, reply, delete
"""


class TestIdentityParsing:
    def test_reads_name_and_capabilities(self):
        identity = parse_identity(IDENTITY_SAMPLE)
        assert identity.name == "manager"
        assert {"capabilities", "status", "roster", "report"} == set(identity.capabilities)

    def test_reads_role_and_description(self):
        identity = parse_identity(IDENTITY_SAMPLE)
        assert "Orchestrator" in identity.role
        assert "missions" in identity.description

    def test_reads_delegates(self):
        assert "email_agent" in parse_identity(IDENTITY_SAMPLE).delegates

    def test_capabilities_are_normalised(self):
        identity = parse_identity("## Name\nm\n## Capabilities\n  REPORT , Status \n")
        assert identity.capabilities == {"report", "status"}

    def test_requires_a_name(self):
        with pytest.raises(IdentityError):
            parse_identity("## Capabilities\nreport\n")

    def test_requires_at_least_one_capability(self):
        with pytest.raises(IdentityError):
            parse_identity("## Name\nmanager\n")


class TestIdentityBecomesAnAgentSpec:
    def test_produces_a_valid_spec(self):
        spec = parse_identity(IDENTITY_SAMPLE).to_spec()
        assert isinstance(spec, AgentSpec)
        assert spec.name == "manager"

    def test_spec_carries_the_declared_capabilities(self):
        assert "roster" in parse_identity(IDENTITY_SAMPLE).to_spec().capabilities

    def test_description_falls_back_to_role(self):
        identity = AgentIdentity(name="m", role="A role", capabilities=frozenset({"x"}))
        assert identity.to_spec().description == "A role"


class TestIdentityLoader:
    def test_missing_file_raises_with_guidance(self, tmp_path):
        with pytest.raises(IdentityError) as exc:
            load_identity(tmp_path / "nope.md")
        assert "IDENTITY.md" in str(exc.value)

    def test_the_real_identity_file_loads(self):
        """The committed IDENTITY.md must stay parseable."""
        identity = load_identity("IDENTITY.md")
        assert identity.name and identity.capabilities

    def test_editing_the_file_changes_capabilities(self, tmp_path):
        path = tmp_path / "IDENTITY.md"
        path.write_text("## Name\nm\n## Capabilities\nreport\n", encoding="utf-8")
        assert load_identity(path).capabilities == {"report"}
        path.write_text("## Name\nm\n## Capabilities\nreport, plan\n", encoding="utf-8")
        assert load_identity(path).capabilities == {"report", "plan"}


class TestSoulParsing:
    def test_reads_policy_values(self):
        policy = parse_soul(SOUL_SAMPLE)
        assert policy.report_only is True
        assert policy.report_when_nothing_new is False
        assert policy.max_attempts_per_mission == 2
        assert policy.max_opportunities_reported == 3

    def test_reads_principles(self):
        assert any("Report" in p for p in parse_soul(SOUL_SAMPLE).principles)

    def test_rejects_a_non_boolean(self):
        with pytest.raises(SoulError):
            parse_soul("## Policy\nreport_only: maybe\n")

    def test_rejects_a_non_numeric_limit(self):
        with pytest.raises(SoulError):
            parse_soul("## Policy\nmax_opportunities_reported: lots\n")

    def test_rejects_a_zero_limit(self):
        with pytest.raises(SoulError):
            BehaviourPolicy(max_opportunities_reported=0)

    def test_the_real_soul_file_loads(self):
        assert load_soul("SOUL.md").report_only is True

    def test_missing_file_falls_back_to_safe_defaults(self, tmp_path):
        """Absent behaviour policy must degrade to the most conservative one."""
        policy = load_soul(tmp_path / "nope.md")
        assert policy.report_only is True
        assert policy.report_when_nothing_new is False


class TestSoulCannotGrantCapabilities:
    def test_never_list_can_be_extended(self):
        assert parse_soul("## Never\nsend, purchase\n").forbids("purchase")

    def test_never_list_cannot_be_shortened(self):
        """Policy may tighten; it must not be able to loosen."""
        policy = parse_soul("## Never\npurchase\n")
        for action in DEFAULT_NEVER:
            assert policy.forbids(action), action

    def test_emptying_the_never_section_keeps_the_defaults(self):
        assert parse_soul("## Policy\nreport_only: false\n").forbids("send")

    def test_disabling_report_only_cannot_create_a_sender(self):
        """The decisive test: SOUL.md is not what stops the system sending."""
        from src.agents.email_agent import EmailAgent
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        policy = parse_soul("## Policy\nreport_only: false\n")
        assert policy.report_only is False  # policy says "go ahead"

        agent = EmailAgent(InMemoryMailbox())
        assert agent.can_send is False       # structure says otherwise
        assert not hasattr(agent, "send")

    def test_no_outbox_implementation_exists_to_enable(self):
        from src.services.email.mailbox import Outbox

        with pytest.raises(TypeError):
            Outbox()
