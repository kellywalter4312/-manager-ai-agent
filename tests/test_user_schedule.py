"""The user's schedule: when the person Alfred serves is around.

The fourth document about the same system, and the first about time:

    IDENTITY.md   who the Manager is
    SOUL.md       how the Manager behaves
    USER.md       who the agent serves, and what they can do
    SCHEDULE.md   when they are around

Two boundaries this file is most careful about, because both are easy to blur:

**It is not the Calendar.** ``services/memory/day.py`` decides which human day
a stored memory fell on - arithmetic over a timestamp, and its own docstring
forbids it becoming more. "When is this person free" is a fact somebody wrote
down, not something derivable from a clock. A schedule that started resolving
instants would be a second temporal system inside a project that has been
careful to have exactly one.

**It is not authority.** When someone is free is a fact about them. It grants
no capability, tool, permission, scope or brain mode, and the type has no field
in which any of those could be expressed.

And the thing it must never become: this project schedules nothing. There is no
timer, no daemon and no cron here, and a document describing a week must not
turn into the thing that starts a run.
"""

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task
from src.manager.capabilities import MANAGER_CAPABILITIES, CapabilityRequest, handled_words
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.profile.loader import parse_profile
from src.services.schedule.loader import (
    DEFAULT_SCHEDULE_PATH,
    load_schedule,
    parse_schedule,
)
from src.services.schedule.schedule import ScheduleError, UserSchedule

IDENTITY = AgentIdentity(
    name="Alfred",
    role="Orchestrator",
    description="Coordinates missions and reports on capabilities.",
    capabilities=frozenset({"capabilities", "status", "roster", "report"}),
)

SCHEDULE = """
# SCHEDULE

## Working hours

monday to friday, 09:00-17:00

## Quiet hours

after 22:00 on weeknights

## Commitments

- tuesday evenings - class
"""

PROFILE = "## Skills\n### Strong\npython\n"


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


def alfred(schedule=None, user=None, specialists=()):
    manager = ManagerAgent()
    manager.own_skills = ManagerSkills(
        IDENTITY, roster=manager.registry.all_agents, user=user, schedule=schedule
    )
    manager.registry.register(manager.own_skills)
    for agent in specialists:
        manager.register_agent(agent)
    return manager


def assembled(schedule=None, user=None, **overrides):
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    return runtime, build_manager(
        runtime, mailbox=InMemoryMailbox(), profile=user, schedule=schedule
    )


# ---------------------------------------------------------------------------
# Parsing and loading
# ---------------------------------------------------------------------------


class TestReadingASchedule:
    def test_the_three_sections_are_read(self):
        schedule = parse_schedule(SCHEDULE)
        assert schedule.working_hours == ("monday to friday, 09:00-17:00",)
        assert schedule.quiet_hours == ("after 22:00 on weeknights",)
        assert schedule.commitments == ("tuesday evenings - class",)

    def test_whole_lines_are_kept_not_split_on_commas(self):
        """"monday to friday, 09:00-17:00" is one statement about a week.

        Splitting it would leave fragments that mean nothing alone - the
        opposite of how a capabilities list is read, and deliberately so.
        """
        assert parse_schedule(SCHEDULE).working_hours == (
            "monday to friday, 09:00-17:00",
        )

    def test_bullet_markers_are_stripped(self):
        assert parse_schedule(SCHEDULE).commitments == ("tuesday evenings - class",)

    def test_unknown_sections_are_ignored(self):
        """The same tolerance the profile parser has, and for the same reason:
        it lets a user keep notes in the file, and stops anything written there
        reaching the system by accident."""
        schedule = parse_schedule(SCHEDULE + "\n## Anything Else\nnot a schedule line\n")
        assert "not a schedule line" not in (
            schedule.working_hours + schedule.quiet_hours + schedule.commitments
        )

    def test_the_committed_example_parses(self):
        """The file a user is told to copy must actually work."""
        schedule = load_schedule("SCHEDULE.example.md")
        assert schedule.configured
        assert schedule.working_hours and schedule.quiet_hours and schedule.commitments

    def test_the_example_keeps_its_explanations_out_of_the_sections(self):
        """Prose inside a section becomes a statement about the week.

        The example puts its explanations in the preamble for that reason, and
        this asserts it stayed that way.
        """
        schedule = load_schedule("SCHEDULE.example.md")
        for line in schedule.working_hours + schedule.quiet_hours + schedule.commitments:
            assert "parsed" not in line.lower()
            assert "heading" not in line.lower()

    def test_the_default_path_is_schedule_md(self):
        assert DEFAULT_SCHEDULE_PATH == "SCHEDULE.md"

    def test_a_real_schedule_file_is_gitignored(self):
        """SCHEDULE.md holds a person's week and must not reach the repository,
        exactly as USER.md does not."""
        assert "SCHEDULE.md" in open(".gitignore", encoding="utf-8").read()


# ---------------------------------------------------------------------------
# A missing schedule is normal
# ---------------------------------------------------------------------------


class TestAMissingScheduleIsSafe:
    def test_a_missing_file_is_not_an_error(self, tmp_path):
        """Most people will never write one. Refusing to run without it would
        make an optional convenience compulsory."""
        schedule = load_schedule(tmp_path / "nowhere.md")
        assert schedule.configured is False
        assert schedule.is_empty

    def test_a_missing_schedule_says_so_rather_than_assuming_one(self, tmp_path):
        schedule = load_schedule(tmp_path / "nowhere.md")
        assert schedule.describe() == "no schedule configured"
        assert any("nothing is known" in note for note in schedule.notes())

    def test_absent_and_blank_are_told_apart(self):
        """A document written and left blank is a statement; absence is not."""
        blank = parse_schedule("# SCHEDULE\n")
        assert blank.configured is True
        assert blank.is_empty
        assert blank.describe() == "schedule configured but empty"
        assert UserSchedule().describe() == "no schedule configured"

    def test_an_unreadable_document_does_raise(self, tmp_path):
        """Missing is fine; present-but-unreadable is not. A document that
        exists is meant to take effect."""
        path = tmp_path / "SCHEDULE.md"
        path.mkdir()  # a directory where a file was expected
        with pytest.raises(ScheduleError):
            load_schedule(path)

    def test_alfred_without_a_schedule_still_works(self):
        result = alfred().handle_task(Task(description="roster"))
        assert result.succeeded
        assert result.payload["scheduled"] == "no schedule configured"


# ---------------------------------------------------------------------------
# Alfred receives it, per run
# ---------------------------------------------------------------------------


class TestAlfredReceivesTheSchedule:
    def test_alfred_knows_the_users_week(self):
        assert alfred(schedule=parse_schedule(SCHEDULE)).own_skills.schedule.working_hours

    def test_the_real_composition_root_hands_it_to_him(self):
        _, manager = assembled(schedule=parse_schedule(SCHEDULE))
        assert manager.own_skills.schedule.quiet_hours == ("after 22:00 on weeknights",)

    def test_every_handler_receives_it(self):
        from src.manager.capabilities import ROSTER_REPORT, ManagerCapability

        seen = {}

        def remember(request: CapabilityRequest):
            seen["schedule"] = request.schedule
            return ROSTER_REPORT.handle(request)

        capability = ManagerCapability(
            name="roster_report", capabilities=ROSTER_REPORT.capabilities, handle=remember
        )
        ManagerSkills(
            IDENTITY,
            capabilities={capability.name: capability},
            schedule=parse_schedule(SCHEDULE),
        ).execute(Task(description="roster"))
        assert seen["schedule"].commitments == ("tuesday evenings - class",)

    def test_the_payload_reports_counts_and_never_the_week_itself(self):
        result = alfred(schedule=parse_schedule(SCHEDULE)).handle_task(
            Task(description="roster")
        )
        assert "1 working-hours line(s)" in result.payload["scheduled"]
        assert "monday" not in result.payload["scheduled"]
        assert "class" not in result.payload["scheduled"]

    def test_the_rendered_report_is_unchanged(self):
        """Step 7 adds context, not output. Anything a person reads stays what
        it was."""
        with_schedule = alfred(schedule=parse_schedule(SCHEDULE)).handle_task(
            Task(description="roster")
        )
        without = alfred().handle_task(Task(description="roster"))
        assert with_schedule.output == without.output
        assert "monday" not in with_schedule.output

    def test_notes_describe_the_week_and_never_instruct(self):
        """"the user works ..." and never "do not contact the user before ...".

        A schedule tells Alfred about someone's week; it does not instruct him.
        Wording it as an instruction would be the first step towards a markdown
        file deciding what the system may do.
        """
        notes = " ".join(parse_schedule(SCHEDULE).notes()).lower()
        assert "the user works" in notes
        for imperative in ("do not", "must not", "never ", "you shall", "refuse"):
            assert imperative not in notes

    def test_the_schedule_is_kept_apart_from_the_profile(self):
        """Different documents, different facts, different reasons to change."""
        manager = alfred(schedule=parse_schedule(SCHEDULE), user=parse_profile(PROFILE))
        assert manager.own_skills.schedule is not manager.own_skills.user
        assert not hasattr(manager.own_skills.user, "working_hours")
        assert not hasattr(manager.own_skills.schedule, "skills")


# ---------------------------------------------------------------------------
# Per-run lifetime, and Runtime stays clean
# ---------------------------------------------------------------------------


class TestPerRunLifetime:
    def test_editing_the_document_changes_what_alfred_receives(self, tmp_path):
        """One file edited, no Python touched."""
        path = tmp_path / "SCHEDULE.md"

        path.write_text("## Working hours\nmondays only\n", encoding="utf-8")
        before = alfred(schedule=load_schedule(path)).own_skills.schedule

        path.write_text("## Working hours\nevery day now\n", encoding="utf-8")
        after = alfred(schedule=load_schedule(path)).own_skills.schedule

        assert before.working_hours == ("mondays only",)
        assert after.working_hours == ("every day now",)

    def test_two_managers_off_one_runtime_can_hold_different_weeks(self):
        """The isolation that matters: schedule belongs to a run, not to the
        system. If it were shared, a second tick would inherit the first's."""
        from src.composition import build_manager, build_runtime

        runtime = build_runtime(settings(log_level="CRITICAL"))
        first = build_manager(
            runtime,
            mailbox=InMemoryMailbox(),
            profile=None,
            schedule=parse_schedule("## Working hours\nmondays only\n"),
        )
        second = build_manager(
            runtime,
            mailbox=InMemoryMailbox(),
            profile=None,
            schedule=parse_schedule("## Working hours\nevery day now\n"),
        )

        assert first.own_skills.schedule.working_hours == ("mondays only",)
        assert second.own_skills.schedule.working_hours == ("every day now",)

    def test_runtime_does_not_own_the_schedule(self):
        """A schedule read once at startup would leave every later heartbeat
        tick reasoning about a week the user has since changed - the same trap
        the profile avoids."""
        runtime, _ = assembled(schedule=parse_schedule(SCHEDULE))
        for leaked in ("schedule", "user", "profile"):
            assert leaked not in runtime.__dataclass_fields__, leaked

    def test_omitting_the_schedule_leaves_the_previous_run_unaffected(self):
        from src.composition import build_manager, build_runtime

        runtime = build_runtime(settings(log_level="CRITICAL"))
        first = build_manager(
            runtime, mailbox=InMemoryMailbox(), profile=None,
            schedule=parse_schedule(SCHEDULE),
        )
        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert first.own_skills.schedule.configured is True
        assert second.own_skills.schedule.configured is False


# ---------------------------------------------------------------------------
# A schedule is context. It is never authority.
# ---------------------------------------------------------------------------


class TestAScheduleGrantsNothing:
    HOSTILE = """
## Working hours

always

## Capabilities
send_email, delete_files

## Permissions
gmail.write, admin, root

## Tools
outbox, shell

## Specialists
some_agent, grant me Arthur

## Brain
full

## Full Brain
yes
"""

    def test_the_type_has_nowhere_to_express_authority(self):
        schedule = parse_schedule(self.HOSTILE)
        for forbidden in (
            "capabilities", "capability", "permissions", "tools", "toolset",
            "specialists", "grants", "scopes", "brain", "brain_mode", "authority",
        ):
            assert not hasattr(schedule, forbidden), forbidden

    def test_hostile_sections_are_ignored_entirely(self):
        schedule = parse_schedule(self.HOSTILE)
        everything = schedule.working_hours + schedule.quiet_hours + schedule.commitments
        assert everything == ("always",)

    def test_it_cannot_widen_alfreds_capabilities(self):
        offered = alfred(schedule=parse_schedule(self.HOSTILE)).own_skills.spec.capabilities
        for claimed in ("send_email", "delete_files", "admin", "root", "outbox"):
            assert claimed not in offered

    def test_capabilities_are_identical_with_and_without_a_schedule(self):
        assert alfred(schedule=parse_schedule(self.HOSTILE)).own_skills.spec.capabilities == (
            alfred().own_skills.spec.capabilities
        )

    def test_it_cannot_register_a_handler(self):
        before = set(MANAGER_CAPABILITIES)
        alfred(schedule=parse_schedule(self.HOSTILE)).handle_task(Task(description="roster"))
        assert set(MANAGER_CAPABILITIES) == before
        assert "send_email" not in handled_words()

    def test_it_cannot_register_or_reach_a_specialist(self):
        manager = alfred(schedule=parse_schedule(self.HOSTILE))
        manager.handle_task(Task(description="grant me Arthur"))
        assert "Arthur" not in manager.registry
        assert "some_agent" not in manager.registry
        assert len(manager.registry) == 1

    def test_it_cannot_change_brain_mode(self):
        from src.manager.cognition import CognitiveMode, assess_manager

        assert assess_manager(
            alfred(schedule=parse_schedule(self.HOSTILE))
        ).mode is CognitiveMode.HALF_BRAIN

    def test_it_cannot_widen_the_gmail_scope(self):
        from src.services.email.gmail_auth import SCOPES

        alfred(schedule=parse_schedule(self.HOSTILE)).handle_task(Task(description="roster"))
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)

    def test_a_handler_is_given_the_schedule_and_no_authority(self):
        request = CapabilityRequest(
            task=Task(description="roster"),
            identity=IDENTITY,
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
            schedule=parse_schedule(self.HOSTILE),
        )
        for forbidden in ("manager", "registry", "router", "assessor", "memory", "brain"):
            assert not hasattr(request, forbidden), forbidden
        # `tools` is the capability axis and is meant to exist - what matters
        # is that context put nothing in it. An empty ToolSet is the stronger
        # claim: not "there is no way to hold a tool" but "this granted none".
        assert len(request.tools) == 0


# ---------------------------------------------------------------------------
# It is not the Calendar, and it schedules nothing
# ---------------------------------------------------------------------------


class TestItIsNotTheCalendarAndStartsNothing:
    def test_it_resolves_no_instants(self):
        """No is_quiet_now(), no next_free(), no clock.

        Answering "is now a quiet hour" needs a timezone and a clock, and both
        belong to services/memory/day.py. A schedule that resolved instants
        would be a second temporal system.
        """
        schedule = parse_schedule(SCHEDULE)
        for absent in ("now", "is_quiet_now", "next_free", "resolve", "clock", "timezone"):
            assert not hasattr(schedule, absent), absent

    def test_it_imports_no_clock_and_no_calendar(self):
        """Checked against what the modules actually bind, not what they say.

        Grepping the source would match the docstrings, which discuss the
        Calendar at length precisely in order to stay out of its way. What
        matters is that neither module has a clock or a Calendar in scope.
        """
        import src.services.schedule.loader as loader_module
        import src.services.schedule.schedule as schedule_module

        for module in (schedule_module, loader_module):
            bound = vars(module)
            for absent in ("datetime", "date", "time", "Calendar", "DayView", "utc_now"):
                assert absent not in bound, (module.__name__, absent)

    def test_it_starts_no_timer_daemon_or_cron(self):
        """This project schedules nothing, and a document about a week must
        never become the thing that starts a run.

        Again checked by what is bound rather than what is written: the
        docstrings say "no cron" out loud, and a source grep would trip on
        the promise instead of testing it.
        """
        import src.services.schedule.loader as loader_module
        import src.services.schedule.schedule as schedule_module

        for module in (schedule_module, loader_module):
            bound = vars(module)
            for absent in ("threading", "asyncio", "subprocess", "sched", "signal"):
                assert absent not in bound, (module.__name__, absent)

    def test_the_calendar_is_untouched_by_it(self):
        """Calendar keeps deciding which day a memory fell on, and knows
        nothing about a user's week."""
        from src.services.memory.day import Calendar

        calendar = Calendar.utc()
        for absent in ("working_hours", "quiet_hours", "commitments", "schedule"):
            assert not hasattr(calendar, absent), absent

    def test_memory_records_carry_no_schedule(self):
        from src.services.memory.memory import MemoryRecord

        record = MemoryRecord(content="x")
        for absent in ("schedule", "working_hours", "quiet_hours"):
            assert not hasattr(record, absent), absent


# ---------------------------------------------------------------------------
# Everything around it is unchanged
# ---------------------------------------------------------------------------


class TestSurroundingArchitectureIsUntouched:
    def test_existing_manager_capabilities_still_work(self):
        result = alfred(schedule=parse_schedule(SCHEDULE)).handle_task(
            Task(description="list your capabilities")
        )
        assert result.succeeded
        assert "roster" in result.output

    def test_specialist_routing_is_unchanged(self):
        manager = alfred(
            schedule=parse_schedule(SCHEDULE),
            specialists=[EchoAgent(name="Arthur", capabilities={"email", "inbox"})],
        )
        assert manager.diagnose(Task(description="read my email")).agent_name == "Arthur"
        assert manager.diagnose(Task(description="status report")).agent_name == "Alfred"

    def test_alfred_and_arthur_remain_correctly_named(self):
        from src.services.identity.specialist import load_specialist_identity

        assert load_identity("IDENTITY.md").name == "Alfred"
        assert load_specialist_identity("identities", "email_agent").name == "Arthur"

    def test_no_future_specialist_names_exist(self):
        from src.agents.specialists import available_specialists

        assert available_specialists() == ["email_agent"]

    def test_alfred_and_arthur_keep_separate_brains(self):
        _, manager = assembled(schedule=parse_schedule(SCHEDULE))
        assert manager.brain is not manager.registry.get("Arthur").brain

    def test_alfred_and_arthur_keep_separate_memories(self):
        _, manager = assembled(schedule=parse_schedule(SCHEDULE))
        assert manager.memory is not manager.registry.get("Arthur").memory

    def test_alfreds_half_brain_semantics_are_unchanged(self):
        from src.manager.cognition import CognitiveMode, assess_manager

        profile = assess_manager(alfred(schedule=parse_schedule(SCHEDULE)))
        assert profile.mode is CognitiveMode.HALF_BRAIN
        assert "routing" in profile.capabilities
        assert "memory recall" in profile.capabilities

    def test_alfreds_full_brain_semantics_are_unchanged(self):
        from src.core.contracts import Task as _Task
        from src.manager.cognition import CognitiveMode, assess_cognition
        from src.manager.llm_brain import LLMManagerBrain
        from tests.test_llm_manager_brain import ScriptedProvider

        # Verified first: Full Brain now means the model actually answered.
        brain = LLMManagerBrain(ScriptedProvider("{}"))
        brain.deliberate(_Task(description="warm up"))
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN

    def test_arthurs_half_brain_semantics_are_unchanged(self):
        from src.agents.brain import NullSpecialistBrain

        _, manager = assembled(schedule=parse_schedule(SCHEDULE))
        arthur = manager.registry.get("Arthur")
        assert isinstance(arthur.brain, NullSpecialistBrain)
        assert arthur.can_reason is False

    def test_arthurs_full_brain_semantics_are_unchanged(self):
        from src.agents.brain_factory import build_specialist_brain
        from src.agents.llm_brain import LLMSpecialistBrain
        from tests.test_llm_manager_brain import ScriptedProvider

        assert isinstance(
            build_specialist_brain(
                settings(specialist_brain="llm"), ScriptedProvider("{}"), domain="email"
            ),
            LLMSpecialistBrain,
        )

    def test_per_run_memory_lifetime_is_intact(self):
        from src.composition import build_manager

        runtime, first = assembled(schedule=parse_schedule(SCHEDULE))
        second = build_manager(
            runtime, mailbox=InMemoryMailbox(), profile=None,
            schedule=parse_schedule(SCHEDULE),
        )
        assert first.memory is not second.memory
        assert first.registry.get("Arthur").memory is not second.registry.get("Arthur").memory

    def test_brain_lifetime_is_intact(self):
        from src.composition import build_manager

        runtime, first = assembled(schedule=parse_schedule(SCHEDULE))
        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert first.brain is second.brain
        assert first.registry.get("Arthur").brain is second.registry.get("Arthur").brain

    def test_gmail_is_read_only_and_sending_is_refused(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES
        from src.services.email.mailbox import Outbox

        _, manager = assembled(schedule=parse_schedule(SCHEDULE))
        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert manager.registry.get("Arthur").can_send is False
        assert not manager.handle_task(Task(description="send an email")).succeeded
        with pytest.raises(TypeError):
            Outbox()

    def test_recognition_still_works_and_is_unchanged_by_the_schedule(self):
        """Step 6 keeps working, and a schedule is not evidence about skills."""
        manager = alfred(schedule=parse_schedule(SCHEDULE), user=parse_profile(PROFILE))
        recognition = manager.own_skills.recognise(Task(description="a python task"))
        assert recognition.strong == ("python",)
        for absent in ("working_hours", "quiet_hours", "commitments", "schedule"):
            assert not hasattr(recognition, absent), absent

    def test_no_control_center_voice_goals_or_preferences_arrived(self):
        import inspect

        import src.services.schedule.schedule as schedule_module

        source = inspect.getsource(schedule_module).lower()
        for absent in ("control center", "voice", "goals", "preferences", "heartbeat"):
            assert absent not in source, absent
        for absent in ("goals", "preferences"):
            assert not hasattr(UserSchedule(), absent), absent
