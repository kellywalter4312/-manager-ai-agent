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

## Purpose
To serve one person, and to report what is worth their attention.

## Description
Receives missions and decides how they should be carried out.

## Capabilities
capabilities, status, roster, report

## Delegates
- email_agent - reading mailboxes

## Area of responsibility
Coordination, not depth.
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


class TestPurposeAndAreaOfResponsibility:
    """Two prose sections: what the agent is *for*, and how far it reaches.

    ``## Area of responsibility`` is not new to the document - it has been
    written there since the file existed. What it was not, until now, is
    loaded: the parser collected it into a section dict and then dropped it,
    which made it a heading that looked like configuration and behaved like a
    comment. A section nothing reads is worse than no section at all, because
    a reader reasonably assumes it takes effect.

    ``## Purpose`` is new. ``role`` says what the agent is and ``description``
    says what it does; neither states why it exists, and a purpose that has to
    be inferred from a job description is not durably stated anywhere.
    """

    def test_reads_purpose(self):
        assert "serve one person" in parse_identity(IDENTITY_SAMPLE).purpose

    def test_reads_area_of_responsibility(self):
        assert "Coordination" in parse_identity(IDENTITY_SAMPLE).area_of_responsibility

    def test_purpose_is_not_the_role_or_the_description(self):
        """Three separate questions, three separate fields.

        Collapsing any two would be the small compromise that makes a later
        "who is this agent" answer vague, so the distinction is pinned here.
        """
        identity = parse_identity(IDENTITY_SAMPLE)
        assert identity.purpose != identity.role
        assert identity.purpose != identity.description

    def test_both_are_optional(self):
        """A document that omits them is still a valid identity.

        Anything that parsed before these fields existed must still parse, so
        the default is an empty string and never an error.
        """
        identity = parse_identity("## Name\nm\n## Capabilities\nreport\n")
        assert identity.purpose == ""
        assert identity.area_of_responsibility == ""

    def test_the_committed_identity_file_declares_both(self):
        """The real IDENTITY.md, not a sample - the file the Manager loads."""
        identity = load_identity("IDENTITY.md")
        assert identity.purpose.strip()
        assert identity.area_of_responsibility.strip()


class TestIdentityProseCannotGrantCapability:
    """Identity describes; it does not grant.

    The rule SOUL.md is already held to, applied to the identity document: a
    capability that could be created by writing a sentence about the agent
    would not be a capability boundary at all. Only ``## Capabilities``
    decides what a router may send here.
    """

    SPRAWLING = """
## Name
manager

## Role
An agent that can send, delete and apply on the user's behalf.

## Purpose
To send email, reply to recruiters, apply for jobs and delete messages.

## Description
Sends, forwards, deletes, applies, contacts and purchases.

## Capabilities
report

## Area of responsibility
Sending, deleting, applying and contacting anyone it likes.
"""

    def test_prose_does_not_widen_the_capability_set(self):
        assert parse_identity(self.SPRAWLING).capabilities == {"report"}

    def test_prose_does_not_widen_the_routing_contract(self):
        """AgentSpec is what the router scores a task against, so this is the
        assertion that would actually matter if the rule were broken."""
        spec = parse_identity(self.SPRAWLING).to_spec()
        assert spec.capabilities == {"report"}
        for claimed in ("send", "delete", "apply", "contact", "purchase"):
            assert claimed not in spec.capabilities

    def test_the_prose_is_still_read_back_faithfully(self):
        """Refusing to act on it is not the same as refusing to load it.

        The document is reported as written; what it cannot do is take effect.
        Silently dropping the text would hide the mismatch from whoever has to
        notice that the document is wrong.
        """
        assert "send" in parse_identity(self.SPRAWLING).purpose.lower()


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
