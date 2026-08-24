"""Pattern Recognition: what a task names from the user's own declared context.

Step 5 gave Alfred the profile. Holding it and relating it to the work in hand
are different things, and only the first existed. This is the second.

The question it asks is deliberately not the matcher's question:

    match_opportunity   "this posting requires X - does the user have it?"
    recognise           "which of the user's own declared abilities does this
                         work name?"

The second is a closed-set lookup: every candidate word comes from USER.md, so
nothing can be invented - and for that same reason there is nothing to score.
Feeding recognised skills into match_opportunity would compute coverage as
matched-over-required where every required skill is one the user already has:
coverage 1.0, band STRONG, every time. That is a tautology wearing a number,
and a caller would read it as a judgement. So Recognition carries no score.

Three things this file is most careful about:

    learning is context, never ability
    nothing is invented, ever
    a fact about the user is never authority over the system
"""

import pytest

from src.agents.echo_agent import EchoAgent
from src.core.contracts import Task
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    ROSTER_REPORT,
    CapabilityRequest,
    ManagerCapability,
    handled_words,
)
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.manager.recognition import Recognition, normalised_vocabulary, recognise
from src.services.email.in_memory_mailbox import InMemoryMailbox
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.profile.loader import load_profile, parse_profile
from src.services.profile.profile import SkillTier, UserProfile

IDENTITY = AgentIdentity(
    name="Alfred",
    role="Orchestrator",
    description="Coordinates missions and reports on capabilities.",
    capabilities=frozenset({"capabilities", "status", "roster", "report"}),
)

USER = """
## Skills

### Strong
python, pattern recognition

### Working
apis, javascript

### Learning
docker

## Projects

- Orchestrator - a multi-agent project [skills: python, pattern recognition]
- Website - a static site [skills: javascript]

## Targets

python development

## Experience

seniority: junior
"""


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


def profile():
    return parse_profile(USER)


def saw(description, user=None):
    return recognise(Task(description=description), profile() if user is None else user)


def alfred(user=None, specialists=(), **kwargs):
    manager = ManagerAgent()
    manager.own_skills = ManagerSkills(
        IDENTITY, roster=manager.registry.all_agents, user=user, **kwargs
    )
    manager.registry.register(manager.own_skills)
    for agent in specialists:
        manager.register_agent(agent)
    return manager


def assembled(user=None, **overrides):
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    return runtime, build_manager(runtime, mailbox=InMemoryMailbox(), profile=user)


# ---------------------------------------------------------------------------
# 1-3. Tiers are recognised, and learning is never an ability
# ---------------------------------------------------------------------------


class TestTheThreeTiersAreRecognisedSeparately:
    def test_a_strong_skill_named_by_the_task_is_recognised(self):
        assert "python" in saw("Build a Python service").strong

    def test_a_working_skill_named_by_the_task_is_recognised(self):
        result = saw("Wire up the APIs")
        assert "apis" in result.working
        assert "apis" not in result.strong

    def test_a_learning_skill_is_recognised_as_learning_only(self):
        """The honesty rule at the centre of the profile system, restated here.

        Being partway through learning something is not having it. Docker is
        named because the work touches it, and reported in its own field so
        that nothing downstream can fold it into what the user can do.
        """
        result = saw("Deploy the service with docker")
        assert result.learning == ("docker",)
        assert "docker" not in result.strong
        assert "docker" not in result.working

    def test_learning_is_excluded_from_demonstrated(self):
        """`demonstrated` exists precisely so a caller asking "what can they
        actually do" cannot reach something that quietly includes what they
        cannot."""
        result = saw("python and docker work")
        assert "python" in result.demonstrated
        assert "docker" not in result.demonstrated

    def test_the_learning_note_says_plainly_that_it_is_not_an_ability(self):
        """A brain reads these strings. The wording is the safeguard."""
        note = next(n for n in saw("docker deployment").notes() if "docker" in n)
        assert "not an ability" in note

    def test_a_learning_skill_never_pulls_in_a_project_as_evidence(self):
        assert saw("docker deployment").projects == ()


# ---------------------------------------------------------------------------
# 4-5. Nothing is ever invented
# ---------------------------------------------------------------------------


class TestNothingIsInvented:
    def test_an_undeclared_skill_is_never_recognised(self):
        """Rust is a real skill and a plausible one. The user did not declare
        it, so it does not exist as far as recognition is concerned."""
        result = saw("Rewrite the parser in Rust with Kubernetes")
        assert "rust" not in result.strong + result.working + result.learning
        assert "kubernetes" not in result.strong + result.working + result.learning

    def test_unrelated_vocabulary_produces_no_recognition(self):
        assert saw("Write a haiku about clouds").is_empty

    def test_the_vocabulary_is_exactly_the_users_declared_skills(self):
        """The set is closed, asserted rather than trusted."""
        assert normalised_vocabulary(profile()) == set(profile().skills)

    def test_sounding_related_is_not_enough(self):
        """"Machine learning" is not "pattern recognition", however adjacent.

        Inferring one from the other would be exactly the harvesting that
        extract_skills refuses, and for the same reason.
        """
        result = saw("Apply machine learning to the dataset")
        assert "pattern recognition" not in result.strong

    def test_a_skill_inside_a_longer_word_is_not_matched(self):
        """Without boundaries "go" matches inside "algorithm" and the user is
        credited with a language because a task mentioned sorting."""
        narrow = parse_profile("## Skills\n### Strong\ngo\n")
        assert saw("improve the algorithm", narrow).is_empty
        assert "go" in saw("write it in Go", narrow).strong

    def test_punctuated_skill_names_still_match(self):
        """Real skill names end in punctuation - node.js, c++, .net - and a
        word boundary after "+" does not mean what it appears to."""
        odd = parse_profile("## Skills\n### Strong\nnode.js, c++\n")
        result = saw("a node.js service with a c++ extension", odd)
        assert set(result.strong) == {"node.js", "c++"}

    def test_an_alias_resolves_to_the_declared_skill(self):
        """A task written as "JS" is the javascript the user declared. The
        alias table is the profile system's own - no second vocabulary."""
        result = saw("some JS work")
        assert "javascript" in result.working

    def test_an_alias_for_an_undeclared_skill_recognises_nothing(self):
        """Aliases widen spelling, never the closed set."""
        narrow = parse_profile("## Skills\n### Strong\npython\n")
        assert saw("some JS work", narrow).is_empty


# ---------------------------------------------------------------------------
# 6-8. Targets and projects come from the user's own declarations
# ---------------------------------------------------------------------------


class TestTargetsAndProjects:
    def test_a_declared_target_is_recognised(self):
        assert saw("a python development role").targets == ("python development",)

    def test_an_undeclared_target_is_never_recognised(self):
        assert saw("a rust systems role").targets == ()

    def test_a_target_needs_all_its_significant_words(self):
        """Stricter than the partial-overlap rule matching.py uses when scoring.

        A scored match reports its own confidence; a recognition is handed to
        a brain as a plain statement of fact, and a target claimed on partial
        evidence would be a false fact rather than a weak score.
        """
        assert saw("a python role").targets == ()

    def test_projects_come_from_the_existing_project_skill_relationship(self):
        """Reuses UserProfile.projects_evidencing rather than deciding here
        what a project demonstrates."""
        assert saw("Build a Python service").projects == ("Orchestrator",)

    def test_a_project_is_not_invented_from_its_name(self):
        """"Website" is only evidence because it declares javascript, not
        because the word appears."""
        assert saw("update the Website copy").projects == ()

    def test_the_right_project_is_selected_for_the_right_skill(self):
        assert saw("some JS work").projects == ("Website",)


# ---------------------------------------------------------------------------
# 9-12. Shape and behaviour of the result
# ---------------------------------------------------------------------------


class TestTheResultIsInformationNotJudgement:
    def test_no_profile_means_the_user_is_not_known(self):
        result = recognise(Task(description="python"), None)
        assert result.known_user is False
        assert result.is_empty

    def test_an_empty_profile_means_the_user_is_not_known(self):
        """A profile with no skills cannot support honest reasoning about a
        person, and saying so beats behaving as though it could."""
        assert recognise(Task(description="python"), UserProfile()).known_user is False

    def test_recognition_is_deterministic(self):
        task, user = Task(description="python, apis and docker"), profile()
        first = recognise(task, user)
        assert all(recognise(task, user) == first for _ in range(5))

    def test_recognition_has_no_score_band_or_coverage(self):
        """The decisive shape assertion.

        Feeding recognised skills into match_opportunity would produce
        coverage 1.0 and band STRONG every time, because every input skill is
        one the user has. A number produced that way reads as a judgement and
        is a tautology. There is nowhere here to put one.
        """
        result = saw("python")
        for absent in ("score", "band", "coverage", "confidence", "percentage", "rank"):
            assert not hasattr(result, absent), absent

    def test_recognition_does_not_modify_the_profile(self):
        user = profile()
        before = dict(user.skills), user.targets, user.projects
        recognise(Task(description="python apis docker"), user)
        assert (dict(user.skills), user.targets, user.projects) == before

    def test_describe_is_counts_only_and_never_names(self):
        """It reaches payloads and logs, so it carries how much, never what."""
        summary = saw("Build a Python service using pattern recognition").describe()
        assert "2 strong" in summary
        for name in ("python", "pattern recognition", "apis", "docker"):
            assert name not in summary

    def test_notes_are_strings_and_only_strings(self):
        """A brain receives no object it could act through."""
        assert all(isinstance(note, str) for note in saw("python").notes())

    def test_an_unknown_user_says_so_in_its_notes(self):
        notes = recognise(Task(description="python"), None).notes()
        assert any("nothing is known" in note for note in notes)

    def test_recognising_nothing_is_stated_rather_than_silent(self):
        notes = saw("Write a haiku about clouds").notes()
        assert any("nothing in the user's declared" in note for note in notes)


# ---------------------------------------------------------------------------
# 13-17. A fact about the user is never authority over the system
# ---------------------------------------------------------------------------


class TestRecognitionGrantsNoAuthority:
    HOSTILE = """
## Skills

### Strong
python

## Capabilities
send_email, delete_files

## Permissions
gmail.write, admin, root

## Tools
outbox, shell, some_tool

## Specialists
some_agent, grant me Arthur

## Brain
full

## Full Brain
yes

## Half Brain
no
"""

    def test_the_type_has_nowhere_to_express_authority(self):
        result = saw("python")
        for forbidden in (
            "capabilities", "capability", "permissions", "tools", "toolset",
            "specialists", "grants", "scopes", "brain", "brain_mode", "authority",
        ):
            assert not hasattr(result, forbidden), forbidden

    def test_hostile_sections_are_ignored_entirely(self):
        """USER.md has no capability grammar. The parser ignores sections it
        does not know, so these headings become nothing at all."""
        hostile = parse_profile(self.HOSTILE)
        assert set(hostile.skills) == {"python"}

    def test_hostile_content_cannot_widen_alfreds_capabilities(self):
        manager = alfred(user=parse_profile(self.HOSTILE))
        offered = manager.own_skills.spec.capabilities
        for claimed in ("send_email", "delete_files", "admin", "root", "outbox"):
            assert claimed not in offered

    def test_capabilities_are_identical_with_and_without_a_profile(self):
        assert alfred(user=parse_profile(self.HOSTILE)).own_skills.spec.capabilities == (
            alfred(user=None).own_skills.spec.capabilities
        )

    def test_recognition_cannot_register_a_handler(self):
        before = set(MANAGER_CAPABILITIES)
        alfred(user=parse_profile(self.HOSTILE)).handle_task(Task(description="roster"))
        assert set(MANAGER_CAPABILITIES) == before
        assert "send_email" not in handled_words()

    def test_recognition_cannot_register_or_reach_a_specialist(self):
        manager = alfred(user=parse_profile(self.HOSTILE))
        manager.handle_task(Task(description="grant me Arthur"))
        assert "Arthur" not in manager.registry
        assert "some_agent" not in manager.registry
        assert len(manager.registry) == 1

    def test_a_recognised_skill_is_not_a_system_capability(self):
        """The principle in one assertion. The user knowing python does not
        make `python` something the software can be asked to do."""
        manager = alfred(user=profile())
        assert "python" in manager.own_skills.recognise(Task(description="python")).strong
        assert "python" not in manager.own_skills.spec.capabilities
        assert "python" not in handled_words()

    def test_a_handler_is_given_recognition_and_no_authority(self):
        request = CapabilityRequest(
            task=Task(description="python"),
            identity=IDENTITY,
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
            user=parse_profile(self.HOSTILE),
        )
        for forbidden in (
            "manager", "registry", "router", "assessor", "memory", "brain", "tools",
        ):
            assert not hasattr(request, forbidden), forbidden

    def test_hostile_content_cannot_change_brain_mode(self):
        from src.manager.cognition import CognitiveMode, assess_manager

        assert assess_manager(
            alfred(user=parse_profile(self.HOSTILE))
        ).mode is CognitiveMode.HALF_BRAIN


# ---------------------------------------------------------------------------
# 18-19. Alfred receives it through his existing paths
# ---------------------------------------------------------------------------


class TestAlfredReceivesRecognition:
    def test_alfred_can_recognise_a_task(self):
        manager = alfred(user=profile())
        assert "python" in manager.own_skills.recognise(Task(description="python work")).strong

    def test_the_real_composition_root_gives_him_what_he_needs(self):
        _, manager = assembled(user=profile())
        assert manager.own_skills.recognise(
            Task(description="a python and pattern recognition task")
        ).strong == ("pattern recognition", "python")

    def test_every_handler_receives_the_structured_result(self):
        seen = {}

        def remember(request: CapabilityRequest):
            seen["recognition"] = request.recognition
            return ROSTER_REPORT.handle(request)

        capability = ManagerCapability(
            name="roster_report", capabilities=ROSTER_REPORT.capabilities, handle=remember
        )
        ManagerSkills(
            IDENTITY, capabilities={capability.name: capability}, user=profile()
        ).execute(Task(description="report on the python roster"))
        assert isinstance(seen["recognition"], Recognition)
        assert "python" in seen["recognition"].strong

    def test_the_payload_reports_counts_without_naming_anything(self):
        result = alfred(user=profile()).handle_task(
            Task(description="report on the python roster")
        )
        assert "1 strong" in result.payload["recognised"]
        assert "python" not in result.payload["recognised"]

    def test_the_rendered_report_is_unchanged(self):
        """Step 6 adds evidence, not output. Anything a person reads stays
        exactly what it was."""
        with_user = alfred(user=profile()).handle_task(Task(description="roster"))
        without = alfred(user=None).handle_task(Task(description="roster"))
        assert with_user.output == without.output
        assert "python" not in with_user.output

    def test_recognition_reaches_alfreds_brain_as_notes(self):
        """Through deliberate(task, notes=...) - the channel his Brain has
        always accepted. No new path, no new brain."""
        seen = {}

        class Recording(type(alfred().brain)):
            def deliberate(self, task, agents=(), notes=()):
                seen["notes"] = tuple(notes)
                return super().deliberate(task, agents, notes)

        manager = alfred(user=profile())
        manager.brain = Recording()
        task = Task(description="a python task")
        manager.deliberate(task, notes=manager.own_skills.recognise(task).notes())
        assert any("python" in note for note in seen["notes"])

    def test_recognition_is_computed_once_per_execution(self):
        """Two handlers cannot disagree about what a task named."""
        seen = []

        def remember(request: CapabilityRequest):
            seen.append(request.recognition)
            return ROSTER_REPORT.handle(request)

        capability = ManagerCapability(
            name="roster_report", capabilities=ROSTER_REPORT.capabilities, handle=remember
        )
        skills = ManagerSkills(
            IDENTITY, capabilities={capability.name: capability}, user=profile()
        )
        skills.execute(Task(description="python roster"))
        skills.execute(Task(description="python roster"))
        assert seen[0] == seen[1]


# ---------------------------------------------------------------------------
# Editing USER.md changes what Alfred recognises - with no code change
# ---------------------------------------------------------------------------


class TestEditingUserMdChangesWhatAlfredRecognises:
    TASK = "evaluate work needing python and pattern recognition"

    def test_a_newly_learned_skill_is_recognised_on_the_next_load(self, tmp_path):
        """The headline. One file edited, no Python touched."""
        path = tmp_path / "USER.md"

        path.write_text("## Skills\n### Strong\npython, testing\n", encoding="utf-8")
        before = alfred(user=load_profile(path)).own_skills.recognise(Task(description=self.TASK))

        path.write_text(
            "## Skills\n### Strong\npython, testing, pattern recognition\n", encoding="utf-8"
        )
        after = alfred(user=load_profile(path)).own_skills.recognise(Task(description=self.TASK))

        assert "pattern recognition" not in before.strong
        assert "pattern recognition" in after.strong

    def test_promotion_from_learning_to_strong_changes_what_is_claimed(self, tmp_path):
        """The same word moves from context to ability because the user said
        so - and only because the user said so."""
        path = tmp_path / "USER.md"

        path.write_text("## Skills\n### Learning\npattern recognition\n", encoding="utf-8")
        learning = alfred(user=load_profile(path)).own_skills.recognise(Task(description=self.TASK))

        path.write_text("## Skills\n### Strong\npattern recognition\n", encoding="utf-8")
        strong = alfred(user=load_profile(path)).own_skills.recognise(Task(description=self.TASK))

        assert learning.learning == ("pattern recognition",)
        assert learning.demonstrated == ()
        assert strong.demonstrated == ("pattern recognition",)

    def test_the_composition_root_rebuilds_it_every_run(self):
        """Recognition follows the profile's per-run lifetime, not Runtime's."""
        from src.composition import build_manager, build_runtime

        runtime = build_runtime(settings(log_level="CRITICAL"))
        old = parse_profile("## Skills\n### Strong\npython\n")
        new = parse_profile("## Skills\n### Strong\npython, pattern recognition\n")

        first = build_manager(runtime, mailbox=InMemoryMailbox(), profile=old)
        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=new)
        task = Task(description=self.TASK)

        assert "pattern recognition" not in first.own_skills.recognise(task).strong
        assert "pattern recognition" in second.own_skills.recognise(task).strong

    def test_runtime_holds_neither_user_nor_recognition(self):
        runtime, _ = assembled(user=profile())
        for leaked in ("user", "profile", "recognition"):
            assert leaked not in runtime.__dataclass_fields__, leaked


# ---------------------------------------------------------------------------
# 20-25, 34-35. Every surrounding invariant
# ---------------------------------------------------------------------------


class TestBrainInvariants:
    """Every agent keeps its own Brain, and both modes behave as before."""

    def test_half_brain_still_answers_no_view_while_recognition_runs(self):
        """The decisive Half Brain assertion: recognition is deterministic, so
        it produces a full structured result with no model anywhere - and the
        brain's answer is exactly what it always was."""
        manager = alfred(user=profile())
        task = Task(description="a python task")

        recognition = manager.own_skills.recognise(task)
        decision = manager.deliberate(task, notes=recognition.notes())

        assert recognition.strong == ("python",)
        assert decision.confidence == 0.0
        assert decision.source == "null"
        assert "no reasoning capability" in decision.reason

    def test_half_brain_is_still_reported_as_half_brain(self):
        from src.manager.cognition import CognitiveMode, assess_manager

        profile_ = assess_manager(alfred(user=profile()))
        assert profile_.mode is CognitiveMode.HALF_BRAIN
        assert "routing" in profile_.capabilities
        assert "memory recall" in profile_.capabilities

    def test_full_brain_is_still_reported_as_full_brain(self):
        """Recognition supplies evidence to a model-backed brain; it does not
        change how that brain is classified."""
        from src.manager.cognition import CognitiveMode, assess_cognition
        from src.manager.llm_brain import LLMManagerBrain
        from tests.test_llm_manager_brain import ScriptedProvider

        assert assess_cognition(
            LLMManagerBrain(ScriptedProvider("{}"))
        ).mode is CognitiveMode.FULL_BRAIN

    def test_recognition_produces_the_same_result_in_either_mode(self):
        """It is deterministic, so the mode cannot influence it. That is what
        makes Half Brain a full participant rather than a degraded one."""
        from src.manager.llm_brain import LLMManagerBrain
        from tests.test_llm_manager_brain import ScriptedProvider

        task = Task(description="a python task")
        half = alfred(user=profile())
        full = alfred(user=profile())
        full.brain = LLMManagerBrain(ScriptedProvider("{}"))

        assert half.own_skills.recognise(task) == full.own_skills.recognise(task)

    def test_no_pattern_recognition_brain_exists(self):
        import src.manager.recognition as recognition_module

        names = dir(recognition_module)
        assert not any("Brain" in name for name in names)
        for absent in ("deliberate", "consider", "provider", "LLM"):
            assert not any(absent in name for name in names), absent

    def test_recognition_needs_no_provider_and_makes_no_call(self):
        import inspect

        import src.manager.recognition as recognition_module

        source = inspect.getsource(recognition_module)
        for absent in ("provider", "anthropic", "http", "requests", "client"):
            assert absent not in source.lower(), absent

    def test_alfred_owns_his_own_brain_and_memory(self):
        _, manager = assembled(user=profile())
        assert manager.brain is not manager.memory
        assert not hasattr(manager.own_skills, "brain")
        assert not hasattr(manager.own_skills, "memory")

    def test_arthur_owns_his_own_brain_and_memory(self):
        _, manager = assembled(user=profile())
        arthur = manager.registry.get("Arthur")
        assert arthur.brain is not manager.brain
        assert arthur.memory is not manager.memory

    def test_arthurs_half_brain_semantics_are_unchanged(self):
        from src.agents.brain import NullSpecialistBrain

        _, manager = assembled(user=profile())
        arthur = manager.registry.get("Arthur")
        assert isinstance(arthur.brain, NullSpecialistBrain)
        assert arthur.can_reason is False

    def test_arthurs_full_brain_semantics_are_unchanged(self):
        from src.agents.brain import SpecialistBrain
        from src.agents.brain_factory import build_specialist_brain
        from src.agents.llm_brain import LLMSpecialistBrain
        from tests.test_llm_manager_brain import ScriptedProvider

        brain = build_specialist_brain(
            settings(specialist_brain="llm"), ScriptedProvider("{}"), domain="email triage"
        )
        assert isinstance(brain, LLMSpecialistBrain)
        assert isinstance(brain, SpecialistBrain)

    def test_any_agent_with_its_own_brain_keeps_both_modes(self):
        """Stated over the contracts rather than over a named future agent, so
        the invariant is asserted without inventing a specialist."""
        from src.agents.brain import NullSpecialistBrain, SpecialistBrain
        from src.agents.llm_brain import LLMSpecialistBrain

        assert issubclass(NullSpecialistBrain, SpecialistBrain)
        assert issubclass(LLMSpecialistBrain, SpecialistBrain)

    def test_alfred_and_a_specialist_never_share_a_brain(self):
        _, manager = assembled(user=profile())
        assert manager.brain is not manager.registry.get("Arthur").brain


class TestSurroundingArchitectureIsUntouched:
    def test_existing_manager_capabilities_still_work(self):
        result = alfred(
            user=profile(), specialists=[EchoAgent(name="x", capabilities={"weather"})]
        ).handle_task(Task(description="list your capabilities"))
        assert result.succeeded
        assert "roster" in result.output

    def test_specialist_routing_is_unchanged(self):
        manager = alfred(
            user=profile(),
            specialists=[EchoAgent(name="Arthur", capabilities={"email", "inbox"})],
        )
        assert manager.diagnose(Task(description="read my email")).agent_name == "Arthur"
        assert manager.diagnose(Task(description="status report")).agent_name == "Alfred"

    def test_recognising_a_skill_does_not_move_routing(self):
        """A task naming python routes on capability words, exactly as before -
        recognition is evidence, never a routing input."""
        manager = alfred(
            user=profile(),
            specialists=[EchoAgent(name="Arthur", capabilities={"email", "inbox"})],
        )
        assert manager.diagnose(
            Task(description="read my email about python")
        ).agent_name == "Arthur"

    def test_arthur_still_receives_the_profile_too(self):
        """Alfred recognising must not have taken the profile from Arthur."""
        _, manager = assembled(user=profile())
        analyzer = manager.registry.get("Arthur").analyzers[0]
        assert analyzer._profile is not None
        assert "python" in analyzer._profile.skills

    def test_alfred_and_arthur_remain_correctly_named(self):
        from src.services.identity.specialist import load_specialist_identity

        assert load_identity("IDENTITY.md").name == "Alfred"
        assert load_specialist_identity("identities", "email_agent").name == "Arthur"

    def test_no_future_specialist_names_exist(self):
        from src.agents.specialists import available_specialists

        assert available_specialists() == ["email_agent"]

    def test_per_run_memory_lifetime_is_intact(self):
        runtime, first = assembled(user=profile())
        from src.composition import build_manager

        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=profile())
        assert first.memory is not second.memory
        assert first.registry.get("Arthur").memory is not second.registry.get("Arthur").memory

    def test_brain_lifetime_is_intact(self):
        """Brains are stateless, so they are built once and shared across runs."""
        runtime, first = assembled(user=profile())
        from src.composition import build_manager

        second = build_manager(runtime, mailbox=InMemoryMailbox(), profile=profile())
        assert first.brain is second.brain
        assert first.registry.get("Arthur").brain is second.registry.get("Arthur").brain

    def test_gmail_is_read_only_and_sending_is_refused(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES
        from src.services.email.mailbox import Outbox

        _, manager = assembled(user=profile())
        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert manager.registry.get("Arthur").can_send is False
        assert not manager.handle_task(Task(description="send an email")).succeeded
        with pytest.raises(TypeError):
            Outbox()

    def test_a_recognised_skill_cannot_widen_the_gmail_scope(self):
        """The most direct statement of the boundary."""
        from src.services.email.gmail_auth import SCOPES

        hostile = parse_profile(
            "## Skills\n### Strong\npython\n## Permissions\ngmail.write, gmail.send\n"
        )
        alfred(user=hostile).handle_task(Task(description="roster"))
        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)

    def test_no_schedule_control_center_voice_or_goals_arrived(self):
        import inspect

        import src.manager.recognition as recognition_module

        source = inspect.getsource(recognition_module).lower()
        for absent in ("schedule", "control center", "voice", "cron", "timer"):
            assert absent not in source, absent
        assert not hasattr(CapabilityRequest, "schedule")
        assert not hasattr(Recognition, "goals")
        assert not hasattr(Recognition, "preferences")
