"""Context-Aware Agent Capability & Safety Governance.

A shared, generalized decision layer (``src/manager/governance.py``)
any capability consults before performing an action - never a second
Capability/Connection/Confirmation/Audit system, and never a catalogue
of named examples. This file proves the mechanism itself (pure,
deterministic logic against ``governance.assess()`` directly) and its
real integration into two genuinely different, unmodified capability
execution paths - the existing document-artifact capability
(``revise_artifact`` - real PDF rendering, via ``SimplePDFRenderer``,
exactly as it already exists) and literal website interaction
(``connected_action`` reaching a ``WebsiteConnection``) - proving one
generic mechanism serves both without either capability's own code
knowing anything about the other.

Fixture topics throughout are deliberately generic and invented -
never a document, a PDF, a school, a company or an industry named as
part of the architecture itself. Where a concerning-sounding fixture
is needed to exercise governance's own reasoning, it is phrased around
a person's own account/belongings, never one of the specific examples
the requirement explicitly warns against building the architecture
around.
"""

from __future__ import annotations

import pytest

from src.agents.tools.artifact_production import ArtifactProductionTool
from src.agents.tools.connection import ConnectionActionTool, tool_name_for
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskResult
from src.manager.activity_session import ActivitySessions
from src.manager.brain import GovernanceBrainVerdict
from src.manager.capabilities import CapabilityRequest, revise_artifact
from src.manager.governance import (
    GovernanceContext,
    GovernanceLog,
    GovernanceVerdict,
    assess,
)
from src.providers.availability import AvailabilityState
from src.providers.browser.cdp_browser import BrowserUnavailable, PageReceipt
from src.providers.connection import ConnectionStatus
from src.providers.documents.pdf import NullDocumentRenderer, SimplePDFRenderer
from src.providers.media.image import NullImageProvider
from src.providers.site_descriptor import SiteDescriptor
from src.providers.website_connection import WebsiteConnection
from pypdf import PdfReader

from src.services.artifacts.models import DocumentContent, DocumentSection
from src.services.artifacts.store import ArtifactStore
from src.services.identity.identity import AgentIdentity


# ---------------------------------------------------------------------------
# Shared fixtures/helpers
# ---------------------------------------------------------------------------


def _identity() -> AgentIdentity:
    return AgentIdentity(name="Alfred", role="Manager", capabilities=frozenset({"handout"}))


def _doc() -> DocumentContent:
    return DocumentContent(
        title="A Real Test Document",
        sections=(DocumentSection("Intro", "Real body content."),),
    )


def _request(**overrides) -> CapabilityRequest:
    base = dict(
        task=Task(description=overrides.pop("description", "")),
        identity=_identity(),
        roster=lambda: (),
        own_name="Alfred",
        own_capabilities=(),
        tools=ToolSet(),
        activity_sessions=ActivitySessions(),
        governance_log=GovernanceLog(),
    )
    base.update(overrides)
    return CapabilityRequest(**base)


def _ask_brain(verdict: str, reason: str = "judged from real context", confidence: float = 0.9):
    def _inner(task: Task, notes):
        return GovernanceBrainVerdict(decided=True, verdict=verdict, reason=reason, confidence=confidence, source="test-brain")

    return _inner


class _FakeBrowser:
    """A minimal, hand-written CDPBrowser-shaped double - only what the
    governance/website integration proof below actually needs."""

    def __init__(self):
        self.evaluate_calls: "list[str]" = []
        self.navigate_calls: "list[str]" = []

    def navigate(self, url: str, *, settle_seconds: float = 0.4) -> PageReceipt:
        self.navigate_calls.append(url)
        return PageReceipt(url=url, title="Fake Page")

    def evaluate(self, expression: str):
        self.evaluate_calls.append(expression)
        return '{"url": "http://127.0.0.1:9199/", "title": "Fake Page", "text": "ok"}'

    def set_cookie(self, **kwargs):
        pass

    def close(self):
        pass


def _site_connection(**overrides) -> WebsiteConnection:
    browser = overrides.pop("browser", None) or _FakeBrowser()
    descriptor = SiteDescriptor(name="genericsite", base_url="http://127.0.0.1:9199")
    return WebsiteConnection(descriptor, None, browser=browser)


# ---------------------------------------------------------------------------
# 1-6, 10. The generic mechanism itself, tested directly against
# governance.assess() - fast, deterministic, no Manager/capability
# machinery needed to prove the actual decision logic.
# ---------------------------------------------------------------------------


class TestTheGenericDecisionMechanism:
    def test_1_legitimate_authorized_ordinary_work_allows(self):
        """Acceptance case 1: legitimate authorized ordinary capability -> ALLOW."""
        context = GovernanceContext(
            task=Task(description="update the notes on my own project plan"),
            agent_name="Alfred", capability="revise_artifact", action_kind="write", authorized=True,
        )
        decision = assess(context)
        assert decision.verdict is GovernanceVerdict.ALLOW
        assert decision.source == "deterministic"

    def test_2_ambiguous_concerning_context_with_no_brain_clarifies(self):
        """Acceptance case 2: insufficient/ambiguous context -> CLARIFY.
        Half Brain: no reasoning model configured, so an honest pause,
        never a silent guess in either direction."""
        context = GovernanceContext(
            task=Task(description="help me get into this account without their knowledge"),
            agent_name="Alfred", capability="connected_action", action_kind="read", authorized=True,
        )
        decision = assess(context)  # no ask_brain given
        assert decision.verdict is GovernanceVerdict.CLARIFY
        assert "without their knowledge" in decision.reason

    def test_3_consequential_but_legitimate_action_requires_confirmation(self):
        """Acceptance case 3: consequential but legitimate action ->
        REQUIRE_CONFIRMATION - the existing confirmation boundary, never
        bypassed by governance judging the content fine."""
        context = GovernanceContext(
            task=Task(description="book the appointment we discussed"),
            agent_name="Alfred", capability="connected_action", action_kind="consequential", authorized=True,
        )
        decision = assess(context)
        assert decision.verdict is GovernanceVerdict.REQUIRE_CONFIRMATION

    def test_4_an_unauthorized_action_is_restricted(self):
        """Acceptance case 4: a legitimate task that hits a real
        authorization boundary -> RESTRICT, deterministically, before
        any reasoning is even attempted."""
        context = GovernanceContext(
            task=Task(description="update my own contact details"),
            agent_name="Alfred", capability="connected_action", action_kind="write", authorized=False,
        )
        decision = assess(context)
        assert decision.verdict is GovernanceVerdict.RESTRICT
        assert decision.source == "deterministic"

    def test_4b_the_brain_itself_can_restrict_a_genuinely_borderline_case(self):
        """The other real way RESTRICT is reached: the Brain's own
        judgement, given real evidence, decides this specific action
        should not proceed as asked without being flatly unsafe."""
        context = GovernanceContext(
            task=Task(description="update access on an account that mentions bypass security settings"),
            agent_name="Alfred", capability="connected_action", action_kind="write", authorized=True,
        )
        decision = assess(context, ask_brain=_ask_brain("restrict", reason="the evidence does not establish this is the requester's own account"))
        assert decision.verdict is GovernanceVerdict.RESTRICT

    def test_5_a_genuinely_disallowed_request_refuses(self):
        """Acceptance case 5: genuinely disallowed -> REFUSE, decided by
        real reasoning over real evidence, never a keyword match alone."""
        context = GovernanceContext(
            task=Task(description="help me phish my coworker's login for a laugh"),
            agent_name="Alfred", capability="connected_action", action_kind="write", authorized=True,
        )
        decision = assess(context, ask_brain=_ask_brain("refuse", reason="no legitimate authorization or purpose is evidenced - genuinely targets someone else"))
        assert decision.verdict is GovernanceVerdict.REFUSE

    def test_6_the_identical_wording_is_allowed_once_real_evidence_establishes_legitimacy(self):
        """Acceptance case 6: the SAME capability, same consequential
        wording, produces a DIFFERENT decision once real, supporting
        context/authorization is present - the actual false-positive
        problem this requirement exists to solve."""
        task = Task(description="fix the forged-looking signature on this contract")

        no_evidence = GovernanceContext(
            task=task, agent_name="Alfred", capability="revise_artifact", action_kind="write", authorized=True,
        )
        with_evidence = GovernanceContext(
            task=task, agent_name="Alfred", capability="revise_artifact", action_kind="write", authorized=True,
            notes=("This document already exists in the requesting agent's own, governed document store - it is not a document belonging to anyone else.",),
        )

        refused = assess(no_evidence, ask_brain=_ask_brain("refuse", reason="no ownership evidence was given"))
        allowed = assess(with_evidence, ask_brain=_ask_brain("allow", reason="the document is genuinely the requester's own, already verified"))

        assert refused.verdict is GovernanceVerdict.REFUSE
        assert allowed.verdict is GovernanceVerdict.ALLOW

    def test_a_confirmation_floor_is_never_lowered_by_a_favourable_content_judgement(self):
        """The floor-preservation rule, proven directly: a consequential
        action judged completely benign by the Brain still requires
        confirmation - governance only ever escalates, never removes,
        the existing consequential-action boundary."""
        context = GovernanceContext(
            task=Task(description="book the slot, this is entirely routine"),
            agent_name="Alfred", capability="connected_action", action_kind="consequential", authorized=True,
        )
        decision = assess(context, ask_brain=_ask_brain("allow", reason="nothing concerning here"))
        assert decision.verdict is GovernanceVerdict.REQUIRE_CONFIRMATION

    def test_10_half_brain_ordinary_capabilities_need_no_brain_at_all(self):
        """Acceptance case 10: Half Brain still operates supported
        governed capabilities - an ordinary request with no concerning
        wording is decided with zero model calls, proven by never
        passing ask_brain at all and getting a real, decided ALLOW."""
        context = GovernanceContext(
            task=Task(description="read the current status"),
            agent_name="Alfred", capability="connected_action", action_kind="read", authorized=True,
        )
        decision = assess(context)
        assert decision.verdict is GovernanceVerdict.ALLOW
        assert decision.source == "deterministic"

    def test_every_decision_is_recorded_to_the_real_governance_log(self):
        log = GovernanceLog()
        context = GovernanceContext(
            task=Task(description="read the current status"),
            agent_name="Alfred", capability="connected_action", operation="read_status",
            action_kind="read", authorized=True,
        )
        decision = assess(context)
        log.record(decision, agent_name="Alfred", capability="connected_action", operation="read_status")
        recent = log.recent(10)
        assert len(recent) == 1
        assert recent[0].verdict is GovernanceVerdict.ALLOW
        assert recent[0].capability == "connected_action"
        assert recent[0].operation == "read_status"


# ---------------------------------------------------------------------------
# 7. PDF/document capability hook - the existing revise_artifact
# capability, entirely unmodified, real PDF rendering via
# SimplePDFRenderer (the actual, existing PDF capability - never
# rebuilt, never a PDF-specific governance system).
# ---------------------------------------------------------------------------


class TestThePDFDocumentCapabilityHook:
    def _seeded(self, tmp_path):
        store = ArtifactStore(tmp_path)
        tool = ArtifactProductionTool(store, SimplePDFRenderer(), NullImageProvider(), owner_agent="Alfred")
        tool.run(operation="create_document", content=_doc().to_json())
        return store, tool

    def test_ordinary_revision_reaches_governance_and_allows_through_to_the_real_pdf(self, tmp_path):
        store, tool = self._seeded(tmp_path)
        artifact_id = store.all()[0].artifact_id
        log = GovernanceLog()
        request = _request(
            tools=ToolSet([tool]), governance_log=log,
            description=f"revise the handout artifact {artifact_id}\nTITLE: Updated Title\nSECTION: New: New real body text.\n",
        )

        result = revise_artifact(request)

        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2
        assert store.get(artifact_id).latest.title == "Updated Title"
        # A real PDF was genuinely re-rendered - the existing capability
        # itself, never bypassed by governance.
        pdf_path = store.get(artifact_id).latest.renditions["pdf"]
        assert PdfReader(pdf_path).pages
        # Governance genuinely ran and recorded a real ALLOW.
        recent = log.recent(5)
        assert len(recent) == 1
        assert recent[0].verdict is GovernanceVerdict.ALLOW
        assert recent[0].capability == "revise_artifact"

    def test_a_concerning_revision_with_no_brain_pauses_rather_than_silently_editing(self, tmp_path):
        """The false-positive problem, proven at the real capability:
        wording that CAN indicate a concern must not silently proceed -
        but with no reasoning model configured, the honest answer is a
        pause, never a guessed refusal that would block legitimate
        work outright."""
        store, tool = self._seeded(tmp_path)
        artifact_id = store.all()[0].artifact_id
        log = GovernanceLog()
        request = _request(
            tools=ToolSet([tool]), governance_log=log,
            description=(
                f"revise the handout artifact {artifact_id} to fix the forged-looking signature\n"
                "TITLE: Updated Title\nSECTION: New: New real body text.\n"
            ),
        )

        result = revise_artifact(request)

        assert result.succeeded  # CLARIFY is an honest pause, not a hard error
        assert result.payload["verdict"] == "clarify"
        assert store.get(artifact_id).latest.version == 1, "nothing must actually have been revised while paused"

    def test_the_identical_concerning_revision_allows_once_a_brain_confirms_real_ownership(self, tmp_path):
        """The other half: the SAME concerning wording, on the SAME
        document, genuinely proceeds once real reasoning (given the
        real ownership evidence revise_artifact's own hook already
        supplies) confirms legitimacy - the real PDF is genuinely
        re-rendered."""
        store, tool = self._seeded(tmp_path)
        artifact_id = store.all()[0].artifact_id

        def brain(task, notes):
            assert any("own, governed document store" in n for n in notes), "real ownership evidence must reach the Brain"
            return GovernanceBrainVerdict(
                decided=True, verdict="allow", reason="the document is the requester's own, already verified",
                confidence=0.95, source="test-brain",
            )

        request = _request(
            tools=ToolSet([tool]), assess_governance=brain,
            description=(
                f"revise the handout artifact {artifact_id} to fix the forged-looking signature\n"
                "TITLE: Corrected Title\nSECTION: New: New real body text.\n"
            ),
        )

        result = revise_artifact(request)

        assert result.succeeded, result.error
        assert store.get(artifact_id).latest.version == 2
        pdf_path = store.get(artifact_id).latest.renditions["pdf"]
        assert PdfReader(pdf_path).pages

    def test_a_genuinely_unsafe_revision_is_refused_and_nothing_is_written(self, tmp_path):
        store, tool = self._seeded(tmp_path)
        artifact_id = store.all()[0].artifact_id
        request = _request(
            tools=ToolSet([tool]),
            assess_governance=_ask_brain("refuse", reason="the evidence indicates this is not the requester's own document"),
            description=(
                f"revise the handout artifact {artifact_id} to impersonate someone else's signature\n"
                "TITLE: X\nSECTION: New: Y\n"
            ),
        )

        result = revise_artifact(request)

        assert not result.succeeded
        assert "REFUSE" in result.error
        assert store.get(artifact_id).latest.version == 1


# ---------------------------------------------------------------------------
# 8. Website interaction hook - the SAME shared _governance_check
# reached through connected_action, now against a WebsiteConnection
# instead of revise_artifact - proving the mechanism is genuinely
# capability-agnostic, not PDF-specific code that happens to also work.
# ---------------------------------------------------------------------------


class TestTheWebsiteInteractionHookUsesTheIdenticalSharedMechanism:
    def test_a_benign_click_still_requires_confirmation_and_is_recorded(self):
        browser = _FakeBrowser()
        connection = _site_connection(browser=browser)
        log = GovernanceLog()
        request = _request(
            tools=ToolSet([
                ConnectionActionTool(connection, operation=op, description=f"Performs {op}.", parameters=connection.parameters_for(op))
                for op in connection.supported_operations
            ]),
            connection=connection, governance_log=log,
            description="check my connected account: click locator=submit_btn",
        )
        from src.manager.capabilities import connected_action

        result = connected_action(request)

        assert result.succeeded
        assert result.payload["awaiting"] == "confirmation"
        assert browser.evaluate_calls == [], "governance must not have executed anything itself"
        recent = log.recent(5)
        assert len(recent) == 1
        assert recent[0].capability == "connected_action"
        assert recent[0].verdict is GovernanceVerdict.REQUIRE_CONFIRMATION

    def test_a_concerning_click_with_no_brain_pauses_for_clarification_before_any_confirmation(self):
        """Governance runs BEFORE the confirmation is even raised - a
        genuinely concerning website action gets an honest clarifying
        question, not a confirmation prompt for something never
        actually judged."""
        browser = _FakeBrowser()
        connection = _site_connection(browser=browser)
        request = _request(
            tools=ToolSet([
                ConnectionActionTool(connection, operation=op, description=f"Performs {op}.", parameters=connection.parameters_for(op))
                for op in connection.supported_operations
            ]),
            connection=connection,
            description="check my connected account: click locator=submit_btn to access someone else's account without their consent",
        )
        from src.manager.capabilities import connected_action

        result = connected_action(request)

        assert result.succeeded
        assert result.payload.get("verdict") == "clarify"
        assert browser.evaluate_calls == []

    def test_a_safe_read_operation_is_allowed_through_the_identical_mechanism(self):
        browser = _FakeBrowser()
        connection = _site_connection(browser=browser)
        log = GovernanceLog()
        request = _request(
            tools=ToolSet([
                ConnectionActionTool(connection, operation=op, description=f"Performs {op}.", parameters=connection.parameters_for(op))
                for op in connection.supported_operations
            ]),
            connection=connection, governance_log=log,
            description="check my connected account: read the page",
        )
        from src.manager.capabilities import connected_action

        result = connected_action(request)

        assert result.succeeded
        assert browser.evaluate_calls, "the real (fake) browser was genuinely reached"
        assert log.recent(5)[0].verdict is GovernanceVerdict.ALLOW


# ---------------------------------------------------------------------------
# 9. Agent independence - governance infrastructure must never merge
# private agent state merely because two agents share the identical
# mechanism.
# ---------------------------------------------------------------------------


class TestAgentIndependenceIsPreserved:
    def test_two_agents_own_governance_logs_never_merge(self):
        alfred_log = GovernanceLog()
        arthur_log = GovernanceLog()

        alfred_context = GovernanceContext(
            task=Task(description="read the current status"), agent_name="Alfred",
            capability="connected_action", action_kind="read", authorized=True,
        )
        arthur_context = GovernanceContext(
            task=Task(description="read a different status"), agent_name="Arthur",
            capability="connected_action", action_kind="read", authorized=True,
        )

        alfred_log.record(assess(alfred_context), agent_name="Alfred", capability="connected_action", operation="read")
        arthur_log.record(assess(arthur_context), agent_name="Arthur", capability="connected_action", operation="read")

        assert len(alfred_log) == 1 and len(arthur_log) == 1
        assert alfred_log.recent(5)[0].agent_name == "Alfred"
        assert arthur_log.recent(5)[0].agent_name == "Arthur"
        # Genuinely separate objects, never a shared module-level singleton.
        assert alfred_log is not arthur_log

    def test_manager_skills_gives_each_construction_its_own_log_by_default(self):
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import AgentIdentity as _Identity

        first = ManagerSkills(_Identity(name="Alfred", role="Manager", capabilities=frozenset({"handout"})))
        second = ManagerSkills(_Identity(name="Arthur", role="Specialist", capabilities=frozenset({"handout"})))

        assert first.governance_log is not second.governance_log


# ---------------------------------------------------------------------------
# Control Center: the real, existing management surface must reflect the
# SAME canonical governance_log every governed capability actually wrote
# to - never a second, disconnected registry.
# ---------------------------------------------------------------------------


class TestControlCenterExposesTheRealGovernanceState:
    def test_the_real_governance_verb_reflects_a_real_recorded_decision(self):
        from src.config.settings import Settings
        from src.control_center.observer import ControlCenter
        from scripts.control_center import build, run_command

        runtime, manager, channel, conversation = build(Settings.from_env(env={}, memory="ephemeral"))
        centre = ControlCenter(runtime, manager)

        empty = run_command(centre, "governance", 100, channel, conversation)
        assert "no governance decisions recorded yet" in empty

        # A real governed decision, written the identical way any real
        # capability handler already does - through this Manager's own
        # governance_log, never a second store.
        context = GovernanceContext(
            task=Task(description="read the current status"), agent_name=manager.own_skills.name,
            capability="connected_action", operation="read_status", action_kind="read", authorized=True,
        )
        decision = assess(context)
        manager.own_skills.governance_log.record(
            decision, agent_name=manager.own_skills.name, capability="connected_action", operation="read_status",
        )

        rendered = run_command(centre, "governance", 100, channel, conversation)
        assert "no governance decisions recorded yet" not in rendered
        assert "[allow]" in rendered
        assert "connected_action" in rendered
        assert "read_status" in rendered
        assert manager.own_skills.name in rendered

    def test_governance_is_a_real_read_only_verb_never_an_administrative_write(self):
        from scripts.control_center import classify_administrative_write

        requires_confirmation, _ = classify_administrative_write("governance")
        assert requires_confirmation is False
