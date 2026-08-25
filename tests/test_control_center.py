"""The front door: three ways in, and no ownership of the house.

The Control Center is a presentation layer. Everything it shows was decided by
the architecture underneath it, and the tests that matter most here are the
ones proving it stayed that way - that it routes nothing, assesses no brain,
registers no specialist, invents no tool and predicts no wake.

Three claims run through the file:

    it shows the truth        every value traces to a real read; an absent tool
                              is absent, an unproven model is Half Brain, an
                              unrun heartbeat has no last wake

    it shows enough           a person can see routing, delegation, capability,
                              brain mode and tool availability happening - the
                              difference between an agent system and a chat box

    it shows no more          decision summaries, never a model's private
                              reasoning; an overview, never the whole system on
                              one screen
"""

import ast
from pathlib import Path

import pytest

from src.control_center.observer import ControlCenter
from src.control_center.render import (
    NARROW,
    render_agent,
    render_history,
    render_overview,
    render_system,
    render_turn,
    render_voice,
)
from src.control_center.trail import observe
from src.control_center.views import (
    AgentView,
    ConversationTurn,
    OverviewView,
    VoiceView,
)
from src.providers.availability import Availability, AvailabilityState
from src.core.contracts import Task
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.email.sample_inbox import sample_inbox

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_CENTER = REPO_ROOT / "src" / "control_center"


def docstring_nodes(tree):
    """Every string Constant that is actually a docstring.

    Identified by position rather than by value: ast.get_docstring cleans and
    dedents what it returns, so comparing text finds nothing. A docstring is
    the first statement of a module, class or function, and that is what this
    matches - so prose may name an agent while code may not.
    """
    found = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                found.add(id(first.value))
    return found


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


def centre(mailbox=None, profile=None, **overrides):
    """A Control Center over a system built by the real composition root."""
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    manager = build_manager(
        runtime,
        mailbox=mailbox if mailbox is not None else InMemoryMailbox(),
        profile=profile,
    )
    return ControlCenter(runtime, manager)


def with_profile(**overrides):
    from src.services.profile.loader import parse_profile

    return centre(
        mailbox=sample_inbox(),
        profile=parse_profile("## Skills\n### Strong\npython, testing\n"),
        **overrides,
    )


# ---------------------------------------------------------------------------
# The three ways in
# ---------------------------------------------------------------------------


class TestVoiceStatusReportsEvidenceNotAClaim:
    """The screen shows the adapter's Availability, and adds nothing to it.

    A boolean used to decide this, and got it wrong in the one case that
    matters: an engine that is installed but unproven rendered as "not
    installed", which is a different situation and a false one. Three states
    arrive here and three states are shown.
    """

    def voice(self, availability, engine="test-engine", columns=80):
        return render_voice(VoiceView(engine=engine, availability=availability), columns)

    def test_a_missing_engine_is_reported_with_the_reason_it_gave(self):
        from src.channels.speech import NoSpeech

        speech = NoSpeech()
        screen = self.voice(speech.availability, engine=speech.name)
        assert "no speech engine is installed" in screen
        assert "none" in screen

    def test_the_real_voice_command_reports_what_the_system_actually_has(self):
        """Pins the live path, not just the renderer: whatever `voice` builds
        must carry the shipped adapter's own reason."""
        import scripts.control_center as cc

        screen = cc.run_command(centre(), "voice", 80)
        assert "no speech engine is installed" in screen

    def test_an_unproven_engine_is_not_reported_as_available(self):
        """The case the boolean got wrong. UNKNOWN is not usable and is also
        not missing, and the screen must not collapse it into either."""
        screen = self.voice(Availability())
        assert "verified usable" not in screen
        assert "not installed" not in screen
        assert "not yet verified" in screen

    def test_a_proven_engine_is_reported_as_available(self):
        screen = self.voice(
            Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        )
        assert "verified usable" in screen

    def test_a_failed_engine_is_reported_with_its_recorded_reason(self):
        screen = self.voice(
            Availability(
                state=AvailabilityState.UNAVAILABLE,
                reason="no default output device",
                error_type="OSError",
            )
        )
        assert "unusable" in screen
        assert "no default output device" in screen

    def test_the_three_states_are_distinguishable_on_screen(self):
        """Whatever the wording, the three must not render alike - a screen
        that showed the same line for all of them would have re-created the
        boolean it replaced."""
        unknown = self.voice(Availability())
        available = self.voice(
            Availability(state=AvailabilityState.AVAILABLE, reason="a call succeeded")
        )
        unavailable = self.voice(
            Availability(state=AvailabilityState.UNAVAILABLE, reason="nothing here")
        )
        assert len({unknown, available, unavailable}) == 3

    def test_the_view_holds_the_availability_rather_than_copying_it(self):
        """No second source of truth: the view carries the record itself, so
        it cannot drift from what the adapter reported."""
        from src.channels.speech import NoSpeech

        speech = NoSpeech()
        view = VoiceView(engine=speech.name, availability=speech.availability)
        assert view.availability is speech.availability
        assert view.status == speech.availability.describe()

    def test_hearing_and_speaking_are_not_reported_as_separate_verdicts(self):
        """One adapter publishes one Availability, so printing two conclusions
        from it would re-invent the conflation at the screen."""
        screen = self.voice(Availability())
        assert "hearing" not in screen.lower()
        assert "speaking" not in screen.lower()


class TestTheThreeModes:
    def test_status_history_and_talk_all_exist(self):
        import scripts.control_center as cc

        window = centre()
        for command in ("status", "history"):
            assert cc.run_command(window, command, 80).strip()
        assert cc.run_command(window, "talk roster", 80).strip()

    def test_talking_needs_no_keyword(self):
        """Conversation is the primary mode, so it should not need one."""
        import scripts.control_center as cc

        window = centre()
        keyed = cc.run_command(window, "talk list your capabilities", 80)
        bare = cc.run_command(window, "list your capabilities", 80)
        assert "RESULT" in keyed and "RESULT" in bare

    def test_the_user_never_picks_a_specialist(self):
        """You say what you want. Alfred decides who does it."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        assert turn.handled_by == "Arthur"
        assert turn.delegated is True

    def test_an_unknown_agent_is_reported_rather_than_guessed(self):
        import scripts.control_center as cc

        answer = cc.run_command(centre(), "status Nobody", 80)
        assert "no agent called" in answer
        assert "Alfred" in answer and "Arthur" in answer


# ---------------------------------------------------------------------------
# Talk to Manager shows an agent system working
# ---------------------------------------------------------------------------


class TestTalkToManagerIsObservable:
    def test_a_delegation_shows_the_whole_path(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        rendered = render_turn(turn, 80)

        assert "Alfred -> Arthur" in rendered
        assert "Routing decision" in rendered
        assert "Manager brain" in rendered
        assert "Specialist brain" in rendered
        assert "Assessed" in rendered

    def test_direct_handling_says_so(self):
        window = centre()
        turn = observe(window.manager, Task(description="tell me about anything at all"))
        assert turn.delegated is False
        assert turn.handled_by == "Alfred"
        assert "Handled directly" in render_turn(turn, 80)

    def test_a_missing_tool_is_named_in_the_trail(self):
        """The honest case, and the one a chat box could not show."""
        window = centre()
        turn = observe(window.manager, Task(description="research a subject"))
        assert turn.tool_used == "web_search"
        assert turn.tool_available is False
        assert "NOT INSTALLED" in render_turn(turn, 80)

    def test_both_brain_modes_appear_when_a_specialist_is_involved(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        assert turn.brain_mode == "HALF BRAIN"
        assert turn.specialist_brain_mode == "HALF BRAIN"

    def test_memory_activity_is_reported_as_counts(self):
        """That something was learned, not the text of it - the content lives
        in the agent's own memory view."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        assert isinstance(turn.memory_written, int)

    def test_no_private_reasoning_is_exposed(self):
        """The boundary that separates a decision summary from a chain of
        thought. Steps describe what happened; none quotes a model thinking."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for jobs"))
        rendered = render_turn(turn, 80).lower()

        for leaked in (
            "chain of thought", "chain-of-thought", "thinking:", "internal reasoning",
            "let me think", "reasoning trace", "scratchpad",
        ):
            assert leaked not in rendered, leaked

    def test_the_trail_can_be_skipped_when_a_router_would_charge_for_it(self):
        """Reading the routing decision costs a second call on a model-driven
        router, so a caller can decline it."""
        window = centre()
        turn = observe(window.manager, Task(description="roster"), explain=False)
        assert not any(step.label == "Routing decision" for step in turn.steps)
        assert turn.succeeded

    def test_a_long_result_is_capped_rather_than_dumped(self):
        """A conversation shows an answer, not a report."""
        window = with_profile()
        turn = observe(window.manager, Task(description="scan my inbox for job opportunities"))
        rendered = render_turn(turn, 80)
        assert "more line(s)" in rendered
        assert len(rendered.splitlines()) < 40


# ---------------------------------------------------------------------------
# Overview first, detail on demand
# ---------------------------------------------------------------------------


class TestProgressiveDisclosure:
    def test_the_overview_is_short(self):
        """The first screen is a summary. If this grows without bound, the
        Control Center is turning into the wall of everything."""
        rendered = render_overview(centre().overview(), 80)
        assert len(rendered.splitlines()) < 40

    def test_the_overview_does_not_dump_memory_or_capabilities(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        rendered = render_overview(window.overview(), 100)

        # Counts, not contents.
        assert "STRONG MATCH" not in rendered
        assert "job_opportunity" not in rendered
        for capability in ("recruiter", "vacancy", "reporting"):
            assert capability not in rendered

    def test_a_wide_screen_shows_the_same_screens_not_more_of_them(self):
        """Extra width is comfort, never permission to show everything."""
        overview = centre().overview()
        narrow = render_overview(overview, 40)
        wide = render_overview(overview, 120)

        assert "SPECIALISTS (1)" in narrow and "SPECIALISTS (1)" in wide
        assert len(wide.splitlines()) - len(narrow.splitlines()) < 10

    def test_detail_lives_behind_a_drill_down(self):
        window = with_profile()
        overview = window.overview()
        arthur = overview.agent("Arthur")
        assert isinstance(arthur, AgentView)

        detail = render_agent(arthur, 80)
        assert "BRAIN" in detail and "MEMORY" in detail and "CAPABILITIES" in detail
        assert "recruiter" in detail

    def test_the_system_view_is_separate_from_the_agents(self):
        rendered = render_system(centre().overview(), 80)
        assert "HEARTBEAT" in rendered and "PERMISSIONS" in rendered
        assert "BRAIN" not in rendered


# ---------------------------------------------------------------------------
# Brain and Memory stay distinct, and per agent
# ---------------------------------------------------------------------------


class TestBrainAndMemoryAreNeverCollapsed:
    def test_each_agent_has_its_own_brain_view(self):
        window = with_profile()
        overview = window.overview()
        assert overview.manager.brain.agent == "Alfred"
        assert overview.specialists[0].brain.agent == "Arthur"

    def test_each_agent_has_its_own_memory_view(self):
        window = with_profile()
        overview = window.overview()
        assert overview.manager.memory.owner == "Alfred"
        assert overview.specialists[0].memory.owner == "Arthur"

    def test_a_brain_view_carries_no_memory_and_a_memory_view_no_brain(self):
        overview = centre().overview()
        brain, memory = overview.manager.brain, overview.manager.memory
        for forbidden in ("records", "recent", "recall", "content"):
            assert not hasattr(brain, forbidden), forbidden
        for forbidden in ("mode", "provider", "verified", "reason"):
            assert not hasattr(memory, forbidden), forbidden

    def test_the_manager_brain_view_does_not_report_a_specialist_mode(self):
        """Alfred being Full Brain must never make Arthur Full Brain."""
        from src.core.contracts import Task as _Task
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Works(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider="anthropic")

        window = with_profile()
        window.manager.brain = LLMManagerBrain(Works())
        window.manager.brain.deliberate(_Task(description="warm up"))

        overview = window.overview()
        assert overview.manager.brain.mode == "FULL BRAIN"
        assert overview.specialists[0].brain.mode == "HALF BRAIN"

    def test_full_brain_is_never_shown_for_a_merely_configured_model(self):
        """The Control Center must not be the place the definition slips."""
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Configured(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider="anthropic")

        window = centre()
        window.manager.brain = LLMManagerBrain(Configured())

        view = window.manager_view().brain
        assert view.mode == "HALF BRAIN"
        assert view.verified is False
        assert "[--]" in render_agent(window.manager_view(), 80)


# ---------------------------------------------------------------------------
# Specialists are discovered, never listed
# ---------------------------------------------------------------------------


class TestSpecialistDiscovery:
    def test_specialists_come_from_the_registry(self):
        overview = centre().overview()
        assert [s.name for s in overview.specialists] == ["Arthur"]

    def test_no_agent_name_is_written_into_the_control_center(self):
        """Not Arthur, not Alfred, and certainly not a future specialist.

        Names arrive from identity documents through the registry. A name in
        this package would be the start of the redesign-per-specialist problem
        the architecture exists to avoid.
        """
        forbidden = (
            "arthur", "alfred", "research_agent", "coding_agent",
            "sports_agent", "movie_agent", "media_agent", "jarvis",
        )
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            docstrings = docstring_nodes(tree)
            for node in ast.walk(tree):
                # An identifier naming an agent is a dependency on that agent.
                if isinstance(node, ast.Name):
                    for name in forbidden:
                        assert name not in node.id.lower(), (path.name, node.id)
                # So is a live string literal. A docstring is documentation,
                # and prose explaining the architecture may name the Manager.
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if id(node) in docstrings:
                        continue
                    for name in forbidden:
                        assert name not in node.value.lower(), (path.name, node.value[:40])

    def test_a_second_specialist_appears_without_any_change(self, tmp_path, monkeypatch):
        """The scaling claim. One entry in the registry, one identity document,
        and the Control Center shows it with its own brain and memory."""
        from src.agents.base_agent import BaseAgent
        from src.agents.specialists import SPECIALISTS
        from src.composition import build_manager, build_runtime
        from src.core.contracts import AgentSpec, TaskResult

        identities = tmp_path / "identities"
        identities.mkdir()
        (identities / "email_agent.md").write_text(
            (REPO_ROOT / "identities" / "email_agent.md").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (identities / "ledger_agent.md").write_text(
            "## Name\nTesting Specialist\n## Role\nA stand-in.\n"
            "## Area of responsibility\nbookkeeping\n",
            encoding="utf-8",
        )

        class Second(BaseAgent):
            def __init__(self, context):
                self.context = context
                self.brain = context.brain
                self.memory = context.memory()

            @property
            def spec(self):
                return AgentSpec(
                    name=self.context.wiring.name,
                    description="A stand-in used by the Control Center tests.",
                    capabilities=frozenset({"ledger", "reconcile"}),
                )

            def execute(self, task):
                return TaskResult.success(task, self.name, "reconciled")

        monkeypatch.setitem(SPECIALISTS, "ledger_agent", Second)

        runtime = build_runtime(
            settings(log_level="CRITICAL", identities_dir=str(identities))
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        overview = ControlCenter(runtime, manager).overview()

        names = [s.name for s in overview.specialists]
        assert "Testing Specialist" in names
        assert "Arthur" in names

        second = overview.agent("Testing Specialist")
        assert second.brain.agent == "Testing Specialist"
        assert second.memory.owner == "Testing Specialist"
        assert "bookkeeping" in second.area
        assert "Testing Specialist" in render_overview(overview, 80)


# ---------------------------------------------------------------------------
# Nothing is fabricated
# ---------------------------------------------------------------------------


class TestTheControlCenterTellsTheTruth:
    def test_an_absent_tool_is_shown_as_absent(self):
        rendered = render_system(centre().overview(), 80)
        assert "none installed" in rendered
        assert "Web Search: Available" not in rendered

    def test_a_capability_without_its_tool_is_marked_not_installed(self):
        view = centre().manager_view()
        research = next(c for c in view.capabilities if c.name == "research")
        assert research.tool == "web_search"
        assert research.tool_available is False
        assert research.is_performable is False
        assert "NOT INSTALLED" in render_agent(view, 80)

    def test_a_capability_needing_no_tool_claims_none(self):
        view = centre().manager_view()
        roster = next(c for c in view.capabilities if c.name == "roster")
        assert roster.tool is None
        assert roster.is_performable is True

    def test_an_unrun_heartbeat_has_no_last_wake(self, tmp_path):
        window = centre(state_path=str(tmp_path / "nothing" / "processed.json"))
        beat = window.heartbeat_view()
        assert beat.last_wake is None
        assert "never" in render_overview(window.overview(), 80)

    def test_no_next_wake_is_predicted(self):
        """Scheduling lives outside this process, so naming a time would
        describe a mechanism the project does not have."""
        beat = centre().heartbeat_view()
        assert beat.next_wake is None
        assert "scheduled externally" in render_system(centre().overview(), 80)

    def test_an_empty_history_says_so(self, tmp_path):
        window = centre(reports_dir=str(tmp_path / "none"))
        rendered = render_history(window.history(), 80)
        assert "nothing recorded yet" in rendered


# ---------------------------------------------------------------------------
# History is filtered, not dumped
# ---------------------------------------------------------------------------


class TestHistory:
    def test_history_is_separate_from_status(self):
        window = with_profile()
        assert "HISTORY" in render_history(window.history(), 80)
        assert "HISTORY" not in render_overview(window.overview(), 80)

    def test_history_can_be_filtered_by_category(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        view = window.history(category="memory")
        assert view.is_filtered
        assert all(event.category == "memory" for event in view.events)

    def test_history_can_be_filtered_by_actor(self):
        window = with_profile()
        window.manager.handle_task(Task(description="scan my inbox for jobs"))
        view = window.history(actor="Arthur")
        assert all(event.actor == "Arthur" for event in view.events)

    def test_history_is_limited_before_it_reaches_a_screen(self):
        window = with_profile()
        view = window.history(limit=2)
        assert len(view.events) <= 2


# ---------------------------------------------------------------------------
# The Control Center owns nothing
# ---------------------------------------------------------------------------


class TestItOwnsNoBusinessLogic:
    FORBIDDEN_IMPORTS = {
        "src.manager.router",
        "src.manager.llm_router",
        "src.manager.fallback_router",
        "src.manager.assessor",
        "src.manager.router_factory",
        "src.manager.brain_factory",
        "src.agents.brain_factory",
        "src.services.memory.factory",
        "src.services.memory.policy",
        "src.services.email.gmail_mailbox",
        "src.providers.factory",
        "src.heartbeat.runner",
        "src.heartbeat.lock",
    }

    def test_it_imports_no_engine_it_would_be_tempted_to_reimplement(self):
        """Routing, assessment, brain construction, memory admission,
        scheduling and provider selection all stay where they are."""
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module not in self.FORBIDDEN_IMPORTS, (path.name, node.module)

    def test_it_defines_no_router_assessor_or_brain(self):
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    lowered = node.name.lower()
                    for role in ("router", "assessor", "brain", "memory", "agent"):
                        if role in lowered:
                            assert lowered.endswith("view"), (path.name, node.name)

    def test_it_starts_no_timer_and_schedules_nothing(self):
        timers = {"threading", "asyncio", "sched", "signal", "subprocess"}
        for path in sorted(CONTROL_CENTER.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in timers, path.name
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr not in ("sleep", "tick", "save"), path.name

    def test_it_adds_no_dependency(self):
        requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").lower()
        for absent in ("flask", "fastapi", "uvicorn", "django", "streamlit", "textual", "rich"):
            assert absent not in requirements, absent

    def test_the_observer_only_reads(self):
        """No write reaches the system through this layer."""
        source = (CONTROL_CENTER / "observer.py").read_text(encoding="utf-8")
        for mutation in (".remember(", ".forget(", ".clear(", ".register_agent(", ".save("):
            assert mutation not in source, mutation


# ---------------------------------------------------------------------------
# The safety boundary is unchanged by having a front door
# ---------------------------------------------------------------------------


class TestNoPermissionWasExpanded:
    def test_the_system_view_reports_read_only(self):
        system = centre().system_view()
        assert system.gmail_scope.endswith("gmail.readonly")
        assert system.can_send is False
        assert system.is_read_only is True

    def test_sending_is_still_refused_through_the_front_door(self):
        window = with_profile()
        turn = observe(window.manager, Task(description="send an email to my team"))
        assert turn.succeeded is False

    def test_the_control_center_cannot_grant_a_tool(self):
        window = centre()
        window.overview()
        window.history()
        assert window.manager.own_skills.tools.is_empty

    def test_no_outbox_is_created_by_opening_the_front_door(self):
        from src.services.email.mailbox import Outbox

        centre().overview()
        with pytest.raises(TypeError):
            Outbox()

    def test_the_scope_is_unchanged_after_every_screen(self):
        from src.services.email.gmail_auth import SCOPES

        window = with_profile()
        window.overview()
        window.history()
        render_system(window.overview(), 80)
        observe(window.manager, Task(description="research something"))
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)


# ---------------------------------------------------------------------------
# Responsive
# ---------------------------------------------------------------------------


class TestResponsive:
    def test_a_phone_width_stacks_into_one_column(self):
        rendered = render_overview(centre().overview(), 40)
        assert "Brain:" in rendered
        assert max(len(line) for line in rendered.splitlines()) <= 42

    def test_a_laptop_width_aligns_instead(self):
        rendered = render_overview(centre().overview(), 100)
        assert "  Brain          " in rendered

    def test_every_screen_renders_at_both_widths(self):
        window = with_profile()
        overview = window.overview()
        turn = observe(window.manager, Task(description="research something"))

        for columns in (36, NARROW - 1, NARROW, 80, 132):
            for rendered in (
                render_overview(overview, columns),
                render_agent(overview.manager, columns),
                render_system(overview, columns),
                render_history(window.history(), columns),
                render_turn(turn, columns),
            ):
                assert rendered.strip()
                assert max(len(line) for line in rendered.splitlines()) <= max(columns, 80) + 2

    def test_nothing_is_padded_to_look_busy(self):
        """One specialist shows one specialist."""
        rendered = render_overview(centre().overview(), 80)
        assert "SPECIALISTS (1)" in rendered
