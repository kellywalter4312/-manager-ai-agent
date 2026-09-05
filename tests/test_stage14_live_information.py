"""Stage 14 - Live World / Environment / Real-Time Information.

Gives Alfred a governed architectural path to obtain current, external
information when a request depends on something that may have changed
since the model's training - motivated by real examples (current Arsenal
transfer news, Liverpool/Gakpo, market/tech "what's happening now"
questions) that this file deliberately never hardcodes.

The mechanism, end to end:

    User request
      -> generic recency-word decision (CURRENT_INFORMATION_WORDS,
         src/manager/capabilities.py)
      -> the governed tool boundary (LiveInformationTool, a Tool like any
         other a specialist could be granted)
      -> a provider-independent fetch (LiveInfoProvider -> LiveInfoResult,
         src/providers/live_info.py)
      -> Alfred's own Brain reasons over the retrieved evidence
         (request.converse, the same channel Stage 13's
         answer_with_collaboration already uses for collaboration
         contributions)
      -> a natural answer, or an honest refusal at any stage that could
         not be completed

No Research/Sports/News agent, no real search backend, no ARCHITECTURE.md
change - this stage establishes the seam, not the ecosystem.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.agents.tools.live_information import LiveInformationTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now
from src.providers.null_live_info_provider import NullLiveInfoProvider
from src.services.identity.voice import voice_for

REPO_ROOT = Path(__file__).resolve().parent.parent

LIVE_INFO_FILES = (
    REPO_ROOT / "src" / "providers" / "live_info.py",
    REPO_ROOT / "src" / "providers" / "null_live_info_provider.py",
    REPO_ROOT / "src" / "agents" / "tools" / "live_information.py",
)


def real_manager(*, tools: ToolSet | None = None, **overrides):
    from src.composition import build_manager, build_runtime
    from src.config.settings import Settings
    from src.services.email.in_memory_mailbox import InMemoryMailbox

    settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", **overrides)
    runtime = build_runtime(settings)
    return build_manager(runtime, mailbox=InMemoryMailbox(), profile=None, tools=tools)


class ScriptedLiveInfoProvider(LiveInfoProvider):
    """A real, working LiveInfoProvider double - never a network call."""

    def __init__(self, content: str = "Arsenal have been linked with a new winger.", *, fail: bool = False):
        self._content = content
        self._fail = fail
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def fetch(self, query: str) -> LiveInfoResult:
        if self._fail:
            raise RuntimeError("provider exploded")
        self.queries.append(query)
        return LiveInfoResult(
            query=query, content=self._content, source="scripted-source",
            retrieved_at=utc_now(),
        )


class ScriptedLLMProvider:
    """A real, local LLMProvider double - never a network call."""

    def __init__(self, text: str = "Based on current reports, Arsenal are being linked with a winger."):
        self._text = text

    @property
    def name(self) -> str:
        return "scripted-llm"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        return Completion(text=self._text, provider=self.name, model="scripted-1")


def with_scripted_brain(manager, text: str = "synthesised answer"):
    brain = LLMManagerBrain(ScriptedLLMProvider(text))
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return brain


def manager_with_live_info(provider: LiveInfoProvider | None = None, **overrides):
    tools = ToolSet([LiveInformationTool(provider or ScriptedLiveInfoProvider())])
    return real_manager(tools=tools, **overrides)


CURRENT_TASK = Task(description="what is the latest Arsenal transfer news")
KNOWLEDGE_TASK = Task(description="explain how a car engine works")


# ---------------------------------------------------------------------------
# 1. The abstraction exists and is provider-independent
# ---------------------------------------------------------------------------


class TestTheAbstractionIsProviderIndependent:
    def test_liveinforesult_is_minimal_and_frozen(self):
        result = LiveInfoResult(
            query="x", content="y", source="z",
            retrieved_at=datetime.now(timezone.utc),
        )
        with pytest.raises(Exception):
            result.content = "changed"  # frozen dataclass

    def test_two_unrelated_providers_satisfy_the_same_interface(self):
        assert isinstance(NullLiveInfoProvider(), LiveInfoProvider)
        assert isinstance(ScriptedLiveInfoProvider(), LiveInfoProvider)

    def test_the_provider_module_is_coupled_to_nothing_it_should_not_be(self):
        source = (REPO_ROOT / "src" / "providers" / "live_info.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                module = getattr(node, "module", "") or ""
                assert not module.startswith("src.manager")
                assert not module.startswith("src.agents")
                assert not module.startswith("src.conversation")
                assert not module.startswith("src.services.memory")
                assert not module.startswith("src.services.identity")
                assert not module.startswith("src.channels")

    def test_an_empty_query_is_refused_by_every_provider(self):
        for provider in (NullLiveInfoProvider(), ScriptedLiveInfoProvider()):
            with pytest.raises(ValueError):
                provider.fetch("")


# ---------------------------------------------------------------------------
# 2-3. The generic decision: current-info requests enter the path,
# ordinary knowledge requests do not
# ---------------------------------------------------------------------------


class TestTheGenericDecision:
    def test_a_current_information_request_reaches_the_live_data_path(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert result.succeeded
        assert result.payload["handled_by"] == "live_information"

    def test_an_ordinary_knowledge_request_does_not(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager)

        result = manager.handle_task(KNOWLEDGE_TASK)

        assert result.payload is None or result.payload.get("handled_by") != "live_information"
        assert result.payload["handled_by"] == "open_conversation"

    @pytest.mark.parametrize(
        "text",
        [
            "is Arsenal linked with any wingers right now",
            "what's the latest on Liverpool's midfield situation",
            "what's happening with Gakpo",
            "what's happening in the tech world today",
            "what is the current price of that stock",
        ],
    )
    def test_generic_recency_phrasings_all_reach_the_live_data_path(self, text):
        manager = manager_with_live_info()
        with_scripted_brain(manager)

        result = manager.handle_task(Task(description=text))

        assert result.payload is not None
        assert result.payload.get("handled_by") == "live_information"


# ---------------------------------------------------------------------------
# 4. No subject-specific routing
# ---------------------------------------------------------------------------


class TestNoSubjectSpecificRouting:
    def test_no_subject_word_appears_anywhere_in_the_new_code(self):
        import src.manager.capabilities as capabilities_module

        subjects = (
            "arsenal", "liverpool", "messi", "elliott", "gakpo", "fofana",
            "lyon", "football", "transfer", "premier league",
        )
        sources = [f.read_text(encoding="utf-8").lower() for f in LIVE_INFO_FILES]
        # Only this stage's own additions to capabilities.py - not the
        # whole pre-existing module, whose other docstrings (truthfully)
        # use "football" as an example of a word that must never appear.
        sources.append(inspect.getsource(capabilities_module.live_information).lower())
        sources.append(inspect.getsource(capabilities_module.LIVE_INFORMATION.handle).lower())
        sources.append(str(capabilities_module.CURRENT_INFORMATION_WORDS).lower())
        for source in sources:
            for subject in subjects:
                assert subject not in source


# ---------------------------------------------------------------------------
# 5. Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_the_result_names_its_source_and_moment(self):
        provider = ScriptedLiveInfoProvider()
        result = provider.fetch("arsenal transfer news")
        assert result.source == "scripted-source"
        assert result.retrieved_at is not None
        assert result.query == "arsenal transfer news"

    def test_the_tool_embeds_provenance_in_its_output(self):
        tool = LiveInformationTool(ScriptedLiveInfoProvider())
        output = tool.run(query="arsenal transfer news")
        assert "scripted-source" in output
        assert "retrieved:" in output

    def test_the_final_result_carries_provenance_in_its_payload(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert result.payload["tool_source"] == "live_information"
        assert result.payload["retrieved_at"] is not None
        assert result.payload["brain"]


# ---------------------------------------------------------------------------
# 6. Retrieval failure is honest
# ---------------------------------------------------------------------------


class TestRetrievalFailureIsHonest:
    def test_a_provider_that_raises_produces_an_honest_failure(self):
        manager = manager_with_live_info(ScriptedLiveInfoProvider(fail=True))
        with_scripted_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "live_information" in result.error

    def test_a_provider_that_finds_nothing_is_also_honest(self):
        manager = manager_with_live_info(NullLiveInfoProvider())
        with_scripted_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "no current information is available" in result.error

    def test_no_tool_installed_at_all_is_named_honestly(self):
        manager = real_manager()  # default: no tools granted

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "not installed" in result.error


# ---------------------------------------------------------------------------
# 7-8. Half Brain honesty / Full Brain reasoning
# ---------------------------------------------------------------------------


class TestHalfBrainAndFullBrain:
    def test_half_brain_does_not_fabricate_an_answer(self):
        manager = manager_with_live_info()  # default NullBrain

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "couldn't properly reason" in result.error or "don't have a way to reason" in result.error

    def test_full_brain_reasons_over_the_supplied_live_information(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager, "Arsenal are being linked with a winger, per current reports.")

        result = manager.handle_task(CURRENT_TASK)

        assert result.succeeded
        assert result.agent_name == "Alfred"
        assert "winger" in result.output


# ---------------------------------------------------------------------------
# 9. Live-data availability is independent from Brain availability
# ---------------------------------------------------------------------------


class TestLiveDataAndBrainAvailabilityAreIndependent:
    def test_tool_present_brain_absent_fails_on_the_brain_gap(self):
        manager = manager_with_live_info()
        result = manager.handle_task(CURRENT_TASK)
        assert "language model" in result.error or "reason" in result.error
        assert "not installed" not in result.error

    def test_tool_absent_brain_present_fails_on_the_tool_gap(self):
        manager = real_manager()
        with_scripted_brain(manager)
        result = manager.handle_task(CURRENT_TASK)
        assert "not installed" in result.error

    def test_both_present_succeeds(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager)
        assert manager.handle_task(CURRENT_TASK).succeeded

    def test_both_absent_fails_on_the_tool_gap_first(self):
        manager = real_manager()
        result = manager.handle_task(CURRENT_TASK)
        assert "not installed" in result.error


# ---------------------------------------------------------------------------
# 10. Provider switching does not alter Brain/Memory/Voice
# ---------------------------------------------------------------------------


class TestProviderSwitchingDoesNotAlterCoreArchitecture:
    def test_switching_live_info_providers_leaves_brain_and_memory_untouched(self):
        for provider in (NullLiveInfoProvider(), ScriptedLiveInfoProvider()):
            manager = manager_with_live_info(provider)
            brain_before, memory_before = manager.brain, manager.memory
            manager.handle_task(CURRENT_TASK)
            assert manager.brain is brain_before
            assert manager.memory is memory_before

    def test_switching_live_info_providers_leaves_voice_untouched(self):
        for provider in (NullLiveInfoProvider(), ScriptedLiveInfoProvider()):
            manager = manager_with_live_info(provider)
            voice = voice_for(manager.own_skills)
            assert voice is not None
            assert voice.key == "alfred"


# ---------------------------------------------------------------------------
# 11. No long-term Memory pollution
# ---------------------------------------------------------------------------


class TestNoMemoryPollution:
    def test_a_successful_live_information_answer_writes_nothing_to_memory(self):
        manager = manager_with_live_info()
        with_scripted_brain(manager)
        before = len(manager.memory.recall(limit=200))

        manager.handle_task(CURRENT_TASK)

        assert len(manager.memory.recall(limit=200)) == before

    def test_the_handler_never_touches_a_memory_object(self):
        import src.manager.capabilities as capabilities_module

        source = inspect.getsource(capabilities_module.live_information)
        assert ".memory" not in source
        assert ".remember(" not in source


# ---------------------------------------------------------------------------
# 12. No arbitrary execution
# ---------------------------------------------------------------------------


class TestNoArbitraryExecution:
    def test_no_execution_primitives_in_the_new_files(self):
        for path in LIVE_INFO_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__", "open("):
                assert forbidden not in source, (path.name, forbidden)

    def test_the_tool_only_ever_calls_its_own_provider(self):
        source = inspect.getsource(LiveInformationTool)
        tree = ast.parse(source)
        called_names = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        # The only method call on anything other than the provider is
        # LiveInfoResult attribute access, never a request/socket/process API.
        assert not called_names & {"request", "get", "post", "urlopen", "Popen", "run", "system"}


# ---------------------------------------------------------------------------
# 13-14. Stage 11.A / 11.B isolation
# ---------------------------------------------------------------------------


class TestStage11Isolation:
    def test_the_new_files_import_nothing_from_extensions_or_evolution(self):
        for path in LIVE_INFO_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    names = ", ".join(getattr(n, "name", "") for n in node.names)
                    assert "extensions" not in module and "extensions" not in names
                    assert "evolution" not in module and "evolution" not in names


# ---------------------------------------------------------------------------
# 15. Stage 12 memory behaviour intact
# ---------------------------------------------------------------------------


class TestStage12Intact:
    def test_remember_that_still_works_after_a_live_information_request(self):
        from src.channels.channel import TextChannel
        from src.conversation.conversation import Conversation, Participant, ParticipantKind
        from scripts.exchange import exchange

        manager = manager_with_live_info()
        with_scripted_brain(manager)
        channel = TextChannel(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        exchange(channel, conversation, CURRENT_TASK.description)

        before = len(manager.memory.recall(limit=50))
        exchange(channel, conversation, "remember that we prefer green")
        after = manager.memory.recall(limit=50)

        assert len(after) == before + 1


# ---------------------------------------------------------------------------
# 16. Stage 13 collaboration behaviour intact and compatible
# ---------------------------------------------------------------------------


class TestStage13Intact:
    def test_answer_with_collaboration_still_works_with_a_live_info_tool_granted(self):
        from scripts.exchange import answer_with_collaboration

        manager = manager_with_live_info()
        with_scripted_brain(manager, "synthesised via collaboration")

        result = answer_with_collaboration(manager, Task(description="email opportunities update"))

        assert result.succeeded

    def test_a_liveinforesult_is_trivially_compatible_with_a_contribution(self):
        """Not a redesign of Stage 13 - a demonstration that nothing about
        this stage's shape conflicts with becoming governed context there
        one day: Contribution only requires a non-blank source and
        content, both of which LiveInfoResult already is."""
        from src.conversation.collaboration import Contribution

        live_result = ScriptedLiveInfoProvider().fetch("arsenal transfer news")
        contribution = Contribution(source="live_information", content=live_result.content)
        assert contribution.content == live_result.content


# ---------------------------------------------------------------------------
# 17. A future provider needs no orchestration change
# ---------------------------------------------------------------------------


class TestFutureProviderNeedsNoOrchestrationChange:
    def test_a_never_before_seen_provider_works_through_the_identical_path(self):
        class FutureProvider(LiveInfoProvider):
            @property
            def name(self):
                return "future-backend"

            def fetch(self, query):
                return LiveInfoResult(
                    query=query, content="a genuinely new backend's answer",
                    source="future-backend", retrieved_at=utc_now(),
                )

        manager = manager_with_live_info(FutureProvider())
        with_scripted_brain(manager, "answer composed from the future backend")

        result = manager.handle_task(CURRENT_TASK)

        assert result.succeeded
        assert result.payload["tool_source"] == "live_information"


# ---------------------------------------------------------------------------
# 18-20. Existing Alfred / Arthur / voice behaviour unchanged
# ---------------------------------------------------------------------------


class TestExistingBehaviourUnchanged:
    def test_alfred_status_still_works(self):
        manager = manager_with_live_info()
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded and result.agent_name == "Alfred"

    def test_general_assistance_still_reports_its_own_missing_tool_honestly(self):
        manager = manager_with_live_info()  # live_information granted, web_search is not
        result = manager.handle_task(Task(description="research the history of bridges"))
        assert not result.succeeded
        assert "web_search" in result.error

    def test_small_talk_still_works(self):
        manager = manager_with_live_info()
        result = manager.handle_task(Task(description="thanks"))
        assert result.output == "You're welcome!"

    def test_arthur_email_behaviour_is_unaffected(self):
        manager = manager_with_live_info()
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"

    def test_alfred_and_arthur_voices_are_unaffected(self):
        manager = manager_with_live_info()
        alfred_voice = voice_for(manager.own_skills)
        arthur_voice = voice_for(manager.registry.get("Arthur"))
        assert alfred_voice.key == "alfred"
        assert arthur_voice.key == "arthur"
        assert alfred_voice != arthur_voice


# ---------------------------------------------------------------------------
# Mutation tests - the live-data boundary and the decision mechanism
# ---------------------------------------------------------------------------


class TestMutationTheFabricationGate:
    def test_half_brain_honesty_is_load_bearing(self):
        """Pins what a mutation of live_information's Brain-decided check
        (forcing it to treat every decision as usable) would break: a
        Half Brain answering a live-information request must fail
        honestly, never synthesize from nothing."""
        manager = manager_with_live_info()  # NullBrain
        result = manager.handle_task(CURRENT_TASK)
        assert not result.succeeded


class TestMutationTheToolGate:
    def test_the_tool_permission_gate_is_load_bearing(self):
        """Pins what a mutation removing the tools.permits() check would
        break: a Manager with no live_information tool granted must
        refuse, not silently proceed."""
        manager = real_manager()
        result = manager.handle_task(CURRENT_TASK)
        assert not result.succeeded
        assert "not installed" in result.error


class TestMutationTheDecisionWordSet:
    def test_the_word_set_is_load_bearing_for_routing(self):
        """Pins what emptying CURRENT_INFORMATION_WORDS would break: a
        current-information request must actually reach the live-data
        capability, not silently fall through to open_conversation."""
        manager = manager_with_live_info()
        with_scripted_brain(manager)
        result = manager.handle_task(CURRENT_TASK)
        assert result.payload["handled_by"] == "live_information"
