"""The whole-system invariants, pinned so they keep holding.

Milestone 2 built the Manager architecture over nine steps, and each step
tested its own slice. This file tests the *seams between* them - the claims
that are true of the system as a whole and that no single step owns, so no
single step's tests would notice them breaking.

Most of what follows was verified by hand during the final audit and found
already correct. That is exactly why it is written down here: an invariant
confirmed once by a person is an invariant that quietly stops being true. None
of these tests drove a change to `src/` - they describe architecture that
already works, and exist so that it keeps working.

The three axes this file most protects, because collapsing any pair of them is
the failure this whole milestone has been steering away from:

    BRAIN        how deeply can I reason?   FULL (verified usable) or HALF
    CAPABILITY   what kind of work is mine? a declared word AND a handler
    TOOL         what means do I have?      ToolSet membership

And the principle underneath: **a specialist is depth, never permission.**
Alfred does not become incapable of a kind of work because somebody else is
better at it.
"""

import ast
import re
from pathlib import Path

import pytest

from src.core.contracts import Task, TaskResult
from src.services.email.in_memory_mailbox import InMemoryMailbox

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


def settings(**overrides):
    """Settings built from an empty environment, so tests never read os.environ."""
    from src.config.settings import Settings

    return Settings.from_env(env={}, **overrides)


def assembled(**overrides):
    """The real composition root - every claim here is about production."""
    from src.composition import build_manager, build_runtime

    runtime = build_runtime(settings(log_level="CRITICAL", **overrides))
    return runtime, build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)


def source_files():
    """Every shipped Python file, so an audit covers the project not a diff."""
    return sorted(SRC.rglob("*.py"))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# Subjects that have been used as *examples* throughout this project. They are
# data a general capability operates on, and none of them may ever become a
# capability, a handler, a tool or an agent.
EXAMPLE_SUBJECTS = (
    "liverpool", "arsenal", "titanic", "reacher", "umbrella", "avengers",
    "justice league", "elon", "musk", "nigerian", "samurai", "swedish",
    "football", "soccer", "movie", "titanic", "wwii",
)


# ---------------------------------------------------------------------------
# The three axes stay separate
# ---------------------------------------------------------------------------


class TestTheThreeAxesNeverMerge:
    def test_brain_mode_reaches_neither_routing_nor_tools(self):
        """Structural, not behavioural.

        A behavioural test proves a mode does not *currently* change routing.
        This proves the routing and tool modules cannot even see one: none
        imports cognition, and none names a mode in executable code. A future
        "if full brain then route differently" cannot be written without
        failing here first.

        Checked against parsed code rather than raw text on purpose. These
        modules discuss cognition in comments - llm_router explains why it
        records availability at all - and a substring search would trip on the
        explanation instead of testing the rule.
        """
        must_not_know = (
            "src/manager/router.py",
            "src/manager/llm_router.py",
            "src/manager/fallback_router.py",
            "src/manager/manager.py",
            "src/agents/tools/tool.py",
            "src/agents/tools/toolset.py",
        )
        forbidden_names = {
            "CognitiveMode", "CognitiveProfile", "assess_cognition", "assess_manager",
            "FULL_BRAIN", "HALF_BRAIN",
        }
        for relative in must_not_know:
            tree = ast.parse(read(REPO_ROOT / relative))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "cognition" not in node.module, (relative, node.module)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "cognition" not in alias.name, (relative, alias.name)
                if isinstance(node, ast.Name):
                    assert node.id not in forbidden_names, (relative, node.id)
                if isinstance(node, ast.Attribute):
                    assert node.attr not in forbidden_names, (relative, node.attr)

    def test_a_toolset_cannot_be_reached_from_user_context(self):
        """No path from a document about a person to a means of acting.

        The profile, schedule and recognition modules do not import a ToolSet
        and do not mention one. A grant would have to be written somewhere this
        test can see.
        """
        for relative in (
            "src/services/profile/profile.py",
            "src/services/profile/loader.py",
            "src/services/schedule/schedule.py",
            "src/services/schedule/loader.py",
            "src/manager/recognition.py",
        ):
            source = read(REPO_ROOT / relative)
            assert "ToolSet" not in source, relative
            assert "tools" not in source.lower().replace("toolset", ""), relative

    def test_capability_availability_and_tool_availability_are_different_answers(self):
        """Alfred takes responsibility for the work and still lacks the means.

        The distinction that stops "I can do that" being a lie and stops "I
        cannot" being a shrug: the capability is offered, the tool is absent,
        and the answer names the tool.
        """
        _, manager = assembled()
        offered = manager.own_skills.spec.capabilities

        assert "research" in offered
        assert manager.own_skills.tools.is_empty

        result = manager.handle_task(Task(description="research anything at all"))
        assert result.agent_name == "Alfred"
        assert not result.succeeded
        assert "not installed" in result.error

    def test_the_toolset_cannot_grow_or_be_mutated(self):
        """Capability is membership, fixed at construction. A tool that ran
        cannot widen what runs next."""
        from src.agents.tools.toolset import ToolSet

        tools = ToolSet()
        for mutator in ("add", "register", "update", "extend", "append", "__setitem__"):
            assert not hasattr(tools, mutator), mutator


# ---------------------------------------------------------------------------
# Full Brain means verified usable reasoning - everywhere, for everyone
# ---------------------------------------------------------------------------


class TestFullBrainIsEarnedNotConfigured:
    def test_full_brain_is_produced_in_exactly_one_place(self):
        """One gate, so there is one thing to get right.

        If a second construction of FULL_BRAIN appears, it is a second chance
        to claim model reasoning on weaker evidence, and this fails.
        """
        source = read(SRC / "manager" / "cognition.py")
        constructions = [
            line for line in source.splitlines()
            if "mode=CognitiveMode.FULL_BRAIN" in line
        ]
        assert len(constructions) == 1, constructions

    def test_that_one_place_requires_verified_usability(self):
        source = read(SRC / "manager" / "cognition.py")
        gate = source.index("mode=CognitiveMode.FULL_BRAIN")
        preceding = source[:gate]
        assert "if configured and availability.is_usable:" in preceding

    def test_no_module_treats_a_key_as_reasoning(self):
        """An API key is a credential, never evidence of anything working.

        Cognition must not read one, and neither must anything that decides a
        mode. Keys stay confined to configuration and the provider layer -
        which, since Phase 1.5, includes the credential/account layer
        (``src/services/credentials/``) that constructs and names
        ``ProviderAccount``s for that same provider layer to consume; it
        carries no cognition of its own either.
        """
        allowed = {"src/config/settings.py"}
        allowed_prefixes = ("src/providers/", "src/services/credentials/")
        for path in source_files():
            relative = path.relative_to(REPO_ROOT).as_posix()
            if relative in allowed or relative.startswith(allowed_prefixes):
                continue
            source = read(path)
            assert "api_key" not in source.replace('"api_key"', "").replace("'api_key'", ""), relative

    def test_both_agents_transition_independently(self):
        """One agent's verified model says nothing about another's.

        Each brain observes its own provider, so a Manager that has reasoned
        successfully does not make a specialist Full Brain by association.
        """
        from src.agents.llm_brain import LLMSpecialistBrain
        from src.manager.cognition import CognitiveMode, assess_cognition
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Works(LLMProvider):
            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                return Completion(text="{}", provider="anthropic")

        manager_brain = LLMManagerBrain(Works())
        specialist_brain = LLMSpecialistBrain(Works(), domain="anything")

        manager_brain.deliberate(Task(description="x"))

        assert assess_cognition(manager_brain).mode is CognitiveMode.FULL_BRAIN
        assert assess_cognition(specialist_brain).mode is CognitiveMode.HALF_BRAIN

    def test_a_verified_model_that_later_fails_drops_to_half_brain(self):
        """Verification is the last observation, not a badge kept forever."""
        from src.manager.cognition import CognitiveMode, assess_cognition
        from src.manager.llm_brain import LLMManagerBrain
        from src.providers.base import Completion, LLMProvider

        class Flaky(LLMProvider):
            def __init__(self):
                self.calls = 0

            @property
            def name(self):
                return "anthropic"

            def complete(self, prompt, *, system=None):
                self.calls += 1
                if self.calls == 1:
                    return Completion(text="{}", provider="anthropic")
                raise RuntimeError("Your credit balance is too low")

        brain = LLMManagerBrain(Flaky())
        brain.deliberate(Task(description="x"))
        assert assess_cognition(brain).mode is CognitiveMode.FULL_BRAIN

        brain.deliberate(Task(description="x"))
        assert assess_cognition(brain).mode is CognitiveMode.HALF_BRAIN

    def test_half_brain_keeps_every_deterministic_capability(self):
        """Half Brain is not a degraded Manager - it is a Manager without the
        model's depth. Driven end to end rather than read off a list."""
        from src.manager.cognition import CORE_CAPABILITIES, CognitiveMode, assess_manager

        _, manager = assembled()
        profile = assess_manager(manager)
        assert profile.mode is CognitiveMode.HALF_BRAIN
        for core in CORE_CAPABILITIES:
            assert core in profile.capabilities

        assert manager.handle_task(Task(description="status report")).succeeded
        assert manager.handle_task(Task(description="read my inbox")).agent_name == "Arthur"

    def test_the_two_capability_sets_do_not_overlap(self):
        """What a model adds must never be something the deterministic system
        already had, or losing the model would look like losing function."""
        from src.manager.cognition import CORE_CAPABILITIES, MODEL_CAPABILITIES

        assert not set(CORE_CAPABILITIES) & set(MODEL_CAPABILITIES)


# ---------------------------------------------------------------------------
# A specialist is depth, never permission
# ---------------------------------------------------------------------------


class TestNoSpecialistIsACapabilityPrison:
    def test_alfred_can_hold_a_capability_a_specialist_also_has(self):
        """The decisive claim of this milestone, proved by construction.

        Nothing structural stops the Manager owning email work: give him the
        capability and the tool and he does it. What he lacks today is the
        tool, which is an honest absence rather than a prohibition - the same
        position as web_search.

        If someone ever adds "delegate whenever a specialist exists", this
        fails.
        """
        from src.agents.tools.tool import Tool, ToolSpec
        from src.agents.tools.toolset import ToolSet
        from src.manager.capabilities import ROSTER_REPORT, CapabilityRequest, ManagerCapability
        from src.manager.manager import ManagerAgent
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import AgentIdentity

        class Mailbox(Tool):
            @property
            def spec(self) -> ToolSpec:
                return ToolSpec(
                    name="mailbox", description="reads a mailbox", schema={"query": str}
                )

            def run(self, **arguments) -> str:
                return "inbox: 3 messages"

        def simple_email(request: CapabilityRequest) -> TaskResult:
            if not request.tools.permits("mailbox"):
                return TaskResult.failure(
                    request.task, request.own_name, "no mailbox tool is installed"
                )
            outcome = request.tools.invoke("mailbox", {"query": request.task.description})
            return TaskResult.success(request.task, request.own_name, outcome.output)

        capability = ManagerCapability(
            name="simple_email",
            capabilities=frozenset({"email", "inbox"}),
            handle=simple_email,
        )
        identity = AgentIdentity(
            name="Alfred", description="d",
            capabilities=frozenset({"roster", "email", "inbox"}),
        )

        manager = ManagerAgent()
        manager.own_skills = ManagerSkills(
            identity,
            roster=manager.registry.all_agents,
            capabilities={ROSTER_REPORT.name: ROSTER_REPORT, capability.name: capability},
            tools=ToolSet([Mailbox()]),
        )
        manager.registry.register(manager.own_skills)

        assert {"email", "inbox"} <= manager.own_skills.spec.capabilities
        assert manager.handle_task(Task(description="read my email")).output == (
            "inbox: 3 messages"
        )

    def test_nothing_delegates_merely_because_a_specialist_exists(self):
        """Routing scores capabilities. It does not ask whether anyone else
        could plausibly do the job."""
        source = read(SRC / "manager" / "router.py")
        for smell in ("specialist", "delegate_if", "prefer_specialist"):
            assert smell not in source.lower(), smell

    def test_the_manager_holds_capabilities_of_its_own(self):
        """Not a router: the Manager offers work it takes responsibility for."""
        _, manager = assembled()
        assert len(manager.own_skills.spec.capabilities) > 5

    def test_an_ordinary_request_is_never_answered_with_no_agent(self):
        """Across arbitrary subjects, so this is about the shape of a request
        rather than the ones anybody thought of."""
        from src.core.contracts import TaskStatus

        _, manager = assembled()
        for subject in ("a football club", "a film", "a company", "a war", "a sea creature"):
            result = manager.handle_task(Task(description=f"find information about {subject}"))
            assert result.status is not TaskStatus.NO_AGENT_FOUND, subject
            assert result.agent_name == "Alfred", subject


# ---------------------------------------------------------------------------
# Capabilities are kinds of work; subjects are data
# ---------------------------------------------------------------------------


class TestSubjectsNeverBecomeCapabilities:
    def test_no_capability_word_is_a_subject(self):
        from src.manager.capabilities import handled_words

        vocabulary = handled_words()
        for subject in EXAMPLE_SUBJECTS:
            for word in subject.split():
                assert word not in vocabulary, word

    def test_no_shipped_module_is_named_after_a_subject(self):
        for path in source_files():
            stem = path.stem.lower()
            for subject in EXAMPLE_SUBJECTS:
                assert subject not in stem, (path.name, subject)

    def test_no_class_or_function_is_named_after_a_subject(self):
        """Parsed rather than grepped, so a subject in a comment is fine and a
        subject in an identifier is not."""
        for path in source_files():
            tree = ast.parse(read(path))
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                    lowered = node.name.lower()
                    for subject in EXAMPLE_SUBJECTS:
                        assert subject not in lowered, (path.name, node.name)

    def test_the_general_capabilities_describe_work_not_topics(self):
        from src.manager.capabilities import GENERAL_CAPABILITIES

        for capability in GENERAL_CAPABILITIES:
            for subject in EXAMPLE_SUBJECTS:
                assert subject not in capability.kind.lower()
                assert subject not in capability.tool.lower()

    def test_nothing_fabricates_an_answer_about_a_subject(self):
        """No tool is installed, so there is nothing to report - and reporting
        from memory would be worse than reporting nothing."""
        _, manager = assembled()
        result = manager.handle_task(Task(description="tell me about Liverpool"))
        answer = ((result.output or "") + (result.error or "")).lower()
        for invented in ("football", "merseyside", "anfield", "premier league", "1892"):
            assert invented not in answer


# ---------------------------------------------------------------------------
# No future specialist has been invented
# ---------------------------------------------------------------------------


class TestTheRegistryIsTruthful:
    FUTURE = ("research", "coding", "sports", "movie", "media", "download", "history")

    def test_only_the_specialist_that_exists_is_registered(self):
        from src.agents.specialists import available_specialists

        assert available_specialists() == ["email_agent"]

    def test_no_future_specialist_key_is_registered(self):
        from src.agents.specialists import SPECIALISTS

        for future in self.FUTURE:
            assert f"{future}_agent" not in SPECIALISTS, future

    def test_only_one_identity_document_ships(self):
        documents = sorted(p.name for p in (REPO_ROOT / "identities").glob("*.md"))
        assert documents == ["email_agent.md"]

    def test_a_document_alone_still_registers_nothing(self, tmp_path):
        """The rule one level above capabilities: a markdown file must not be
        able to bring an agent into existence."""
        from src.composition import build_manager, build_runtime

        identities = tmp_path / "identities"
        identities.mkdir()
        (identities / "research_agent.md").write_text("## Name\nGhost\n", encoding="utf-8")

        runtime = build_runtime(
            settings(log_level="CRITICAL", identities_dir=str(identities))
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert "research_agent" not in runtime.specialists
        assert "Ghost" not in manager.registry


# ---------------------------------------------------------------------------
# Brain and Memory stay each agent's own
# ---------------------------------------------------------------------------


class TestOwnershipIsNeverShared:
    def test_alfred_and_arthur_share_neither_brain_nor_memory(self, tmp_path):
        _, manager = assembled(memory="persistent", memory_dir=str(tmp_path))
        arthur = manager.registry.get("Arthur")

        assert manager.brain is not arthur.brain
        assert manager.memory is not arthur.memory
        assert manager.memory.path != arthur.memory.path

    def test_a_second_specialist_would_get_its_own_too(self):
        """Stated over the construction path rather than by inventing an agent:
        a builder receives its own brain and builds its own memory."""
        import inspect

        from src.agents.specialists import SpecialistContext

        assert "brain" in {f for f in SpecialistContext.__dataclass_fields__}
        assert callable(SpecialistContext.memory)
        source = inspect.getsource(SpecialistContext.memory)
        assert "build_memory" in source
        assert "wiring.memory_owner" in source

    def test_manager_skills_is_not_a_store_and_not_a_brain(self):
        _, manager = assembled()
        for forbidden in ("memory", "brain", "remember", "recall"):
            assert not hasattr(manager.own_skills, forbidden), forbidden

    def test_runtime_holds_nothing_with_a_per_run_lifetime(self):
        """A profile, schedule or memory frozen here would leave every later
        heartbeat tick reasoning about a person who has moved on."""
        runtime, _ = assembled()
        fields = set(runtime.__dataclass_fields__)
        for leaked in ("user", "profile", "schedule", "recognition", "tools", "memory", "manager"):
            assert leaked not in fields, leaked

    def test_persistent_and_ephemeral_remain_distinct(self):
        from src.services.memory.ephemeral import EphemeralMemory
        from src.services.memory.factory import build_memory
        from src.services.memory.persistent import PersistentMemory

        # Both named explicitly rather than one leaning on the default.
        # The claim is that the two kinds stay distinct and selectable,
        # which must not quietly become a claim about which one is
        # configured - the default moved to persistent and this test kept
        # passing for the wrong reason until it did not.
        assert isinstance(
            build_memory(settings(memory="ephemeral"), "someone"), EphemeralMemory
        )
        assert isinstance(
            build_memory(settings(memory="persistent", memory_dir="data/x"), "someone"),
            PersistentMemory,
        )


# ---------------------------------------------------------------------------
# The heartbeat is an ongoing mechanism, not a one-shot worker
# ---------------------------------------------------------------------------


class TestTheHeartbeatKeepsBeating:
    def test_a_runner_survives_many_wakes(self, tmp_path):
        """Wake, work, report, wake again. The Manager is rebuilt each time and
        the runner is not consumed by running."""
        from datetime import datetime, timezone

        from src.heartbeat.runner import HeartbeatRunner, TickStatus
        from src.heartbeat.state import StateStore
        from src.services.profile.loader import parse_profile

        built = []

        def build(profile):
            from src.composition import build_manager, build_runtime

            runtime = build_runtime(settings(log_level="CRITICAL"))
            manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=profile)
            built.append(manager)
            return manager

        runner = HeartbeatRunner(
            build_manager=build,
            profile_loader=lambda: parse_profile("## Skills\n### Strong\npython\n"),
            state_store=StateStore(str(tmp_path / "state" / "processed.json")),
            enabled=True,
            reports_dir=str(tmp_path / "reports"),
            clock=lambda: datetime(2026, 8, 24, 9, 0, tzinfo=timezone.utc),
        )

        results = [runner.tick() for _ in range(5)]

        assert len(built) == 5
        assert len({id(manager) for manager in built}) == 5
        for result in results:
            assert result.status is not TickStatus.FAILED
        assert runner.enabled is True

    def test_the_watch_loop_repeats_rather_than_exiting(self):
        """--once is registered with an external scheduler; --watch repeats in
        the foreground. Either way the mechanism recurs."""
        source = read(REPO_ROOT / "scripts" / "heartbeat.py")
        assert "while True:" in source
        assert "runner.tick()" in source

    def test_no_timer_was_added_to_a_component_kept_timer_free(self):
        """This project schedules nothing. The only sleep is the --watch loop
        in the entry point, never inside the runner, the lock, the state or the
        schedule.

        Parsed rather than grepped: the schedule module says "no cron" out
        loud, and a substring search would fail on the promise instead of
        testing it.
        """
        timers = {"threading", "asyncio", "sched", "signal", "multiprocessing"}
        for relative in (
            "src/heartbeat/runner.py",
            "src/heartbeat/lock.py",
            "src/heartbeat/state.py",
            "src/services/schedule/schedule.py",
            "src/services/schedule/loader.py",
        ):
            tree = ast.parse(read(REPO_ROOT / relative))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert alias.name.split(".")[0] not in timers, (relative, alias.name)
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert node.module.split(".")[0] not in timers, (relative, node.module)
                # time.sleep(...) specifically - `time` itself is legitimate.
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    assert node.func.attr != "sleep", relative

    def test_the_mission_is_unchanged(self):
        from src.heartbeat.runner import MISSION

        assert MISSION == "check my gmail inbox for new job and freelance opportunities"


# ---------------------------------------------------------------------------
# The safety boundary, checked across the whole project
# ---------------------------------------------------------------------------


class TestTheSafetyBoundaryHolds:
    def test_the_only_gmail_scope_is_read_only(self):
        from src.services.email.gmail_auth import GMAIL_READONLY_SCOPE, SCOPES

        assert SCOPES == (GMAIL_READONLY_SCOPE,)
        assert GMAIL_READONLY_SCOPE.endswith("gmail.readonly")

    def test_no_write_scope_appears_anywhere_in_the_project(self):
        """Not just the constant - the string itself must not exist.

        Absolute, and deliberately with no exception for the one module whose
        job is to refuse a write scope. That module was briefly the reason to
        soften this into "the string may appear where it is being refused",
        which sounds reasonable and is not: the rule stops being a property of
        the source and becomes a judgement about intent, and the next file to
        want an exception will also have a reason.

        The improvement layer refuses exactly the strings it always did. It
        assembles them from fragments instead of writing them out, so this
        assertion stays as blunt as it was - nothing under src/ contains a
        write-scope literal, so nothing under src/ can hand one to an OAuth
        flow by copy-paste, by refactor, or by accident.

        Do not weaken this to accommodate an implementation. Assemble the
        string instead; it costs three lines.
        """
        forbidden = re.compile(
            r"gmail\.(send|modify|compose|insert|labels)|mail\.google\.com", re.IGNORECASE
        )
        for path in source_files():
            assert not forbidden.search(read(path)), path.name

    def test_no_outbox_implementation_exists(self):
        from src.services.email.mailbox import Outbox

        with pytest.raises(TypeError):
            Outbox()

        for path in source_files():
            tree = ast.parse(read(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
                    assert "Outbox" not in bases, (path.name, node.name)

    def test_sending_is_refused_end_to_end(self):
        _, manager = assembled()
        assert manager.registry.get("Arthur").can_send is False
        assert not manager.handle_task(Task(description="send an email to my team")).succeeded

    def test_no_document_can_expand_authority(self):
        """USER.md and SCHEDULE.md at their most hostile, together."""
        from src.manager.cognition import CognitiveMode, assess_manager
        from src.manager.manager import ManagerAgent
        from src.manager.manager_skills import ManagerSkills
        from src.services.identity.identity import load_identity
        from src.services.profile.loader import parse_profile
        from src.services.schedule.loader import parse_schedule

        hostile_user = (
            "## Skills\n### Strong\npython\n"
            "## Capabilities\nsend_email\n## Permissions\ngmail.write\n"
            "## Tools\nweb_search, outbox, mailbox\n## Specialists\nresearch_agent\n"
            "## Brain\nfull\n"
        )
        hostile_schedule = (
            "## Working hours\nalways\n## Tools\nweb_search\n"
            "## Permissions\ngmail.write\n## Brain\nfull\n"
        )

        manager = ManagerAgent()
        manager.own_skills = ManagerSkills(
            load_identity("IDENTITY.md"),
            roster=manager.registry.all_agents,
            user=parse_profile(hostile_user),
            schedule=parse_schedule(hostile_schedule),
        )
        manager.registry.register(manager.own_skills)

        assert manager.own_skills.tools.is_empty
        assert "send_email" not in manager.own_skills.spec.capabilities
        assert "research_agent" not in manager.registry
        assert assess_manager(manager).mode is CognitiveMode.HALF_BRAIN

        from src.services.email.gmail_auth import SCOPES

        assert SCOPES == ("https://www.googleapis.com/auth/gmail.readonly",)


# ---------------------------------------------------------------------------
# Nothing from the next phase has leaked in
# ---------------------------------------------------------------------------


class TestNoFuturePhaseHasStarted:
    def test_no_web_ui_or_voice_dependency_is_declared(self):
        requirements = read(REPO_ROOT / "requirements.txt").lower()
        for absent in (
            "flask", "fastapi", "uvicorn", "django", "streamlit",
            "speechrecognition", "pyttsx", "whisper", "gradio",
        ):
            assert absent not in requirements, absent

    def test_no_server_or_voice_module_is_imported(self):
        """Checked by parsed imports, so a word in a docstring is fine and an
        actual dependency is not."""
        forbidden = {
            "flask", "fastapi", "uvicorn", "django", "streamlit", "gradio",
            "speech_recognition", "pyttsx3", "whisper", "socketserver",
            "http.server", "websockets",
        }
        for path in source_files():
            tree = ast.parse(read(path))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name.split(".")[0] not in forbidden, (path.name, name)

    def test_no_control_center_module_exists(self):
        for path in source_files():
            assert "control" not in path.stem.lower(), path.name

    def test_no_coding_tool_is_falsely_shipped(self):
        """The honest position: "code_assistant" is a named extension
        point and nothing fills it. When it is built this test is what
        says so out loud.

        "web_search" left this list in Phase 3 (Knowledge, Research &
        Live Information): ``WebSearchTool`` (``src/agents/tools/
        web_search.py``) is a real, deliberately-built tool - see
        ARCHITECTURE.md's own Phase 3 record - so asserting its absence
        would now be asserting something false, not honest. This test is
        narrowed the identical way it was already narrowed once before
        (Stage 14, for ``live_information``): the check that matters is
        that ``code_assistant`` - the one name still genuinely unfilled -
        stays unfilled, never that no ``Tool`` subclass may ever claim a
        name this list used to include.
        """
        from src.agents.tools.tool import Tool

        _, manager = assembled()
        assert manager.own_skills.tools.is_empty

        claimed_names: set[str] = set()
        for path in source_files():
            source = read(path)
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases}
                    if "Tool" in bases:
                        class_source = ast.get_source_segment(source, node) or ""
                        for literal in ('"code_assistant"', "'code_assistant'"):
                            if literal in class_source:
                                claimed_names.add(literal.strip("'\""))
        assert claimed_names == set(), claimed_names
