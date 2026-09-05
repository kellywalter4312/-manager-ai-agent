"""Stage 14 finish - a real live-information source, actually reachable
through Alfred's real production conversation path.

The prior Stage 14 turns built the whole architecture (LiveInfoProvider,
LiveInfoResult, NullLiveInfoProvider, LiveInformationTool, the governed
capability, provenance, Brain-gating, Half-Brain honesty) but left it
opt-in-only: nothing in the real production path ever equipped Alfred
with the tool, and the only implementation was the honestly-empty Null
default. This file proves the two things that changed:

    WikipediaLiveInfoProvider   a real, keyless, generic source - one
                                fixed, read-only, public REST endpoint,
                                the Python standard library only, no new
                                dependency
    scripts/control_center.py  the real production conversation path
                                (also what scripts/serve_ui.py builds
                                through) now equips Alfred with the
                                live_information tool, backed by
                                whatever MANAGER_LIVE_INFO_PROVIDER
                                resolves to - honestly empty by default,
                                genuinely real once configured

No test here makes a real network call - this project's own
``conftest.py`` blocks outbound sockets in every test, on purpose, to
keep the suite hermetic. ``WikipediaLiveInfoProvider`` was verified
against the real, live Wikipedia API by hand outside the test suite
(confirmed reachable in this environment); the automated tests below
exercise its parsing and error-handling logic through a mocked HTTP
layer, the same discipline every other network-adjacent test in this
project already follows.
"""

from __future__ import annotations

import ast
import inspect
import json
import urllib.error
from pathlib import Path

import pytest

import scripts.control_center as control_center
from src.core.contracts import Task
from src.manager.llm_brain import LLMManagerBrain
from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now
from src.providers.live_info_factory import build_live_info_provider
from src.providers.null_live_info_provider import NullLiveInfoProvider
from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider
from src.services.identity.voice import voice_for

REPO_ROOT = Path(__file__).resolve().parent.parent

NEW_FILES = (
    REPO_ROOT / "src" / "providers" / "wikipedia_live_info_provider.py",
    REPO_ROOT / "src" / "providers" / "live_info_factory.py",
)

CURRENT_TASK = Task(description="what is the latest Arsenal transfer news")
KNOWLEDGE_TASK = Task(description="explain how a car engine works")

ACCEPTANCE_EXAMPLES = (
    "What's going on in the football world right now?",
    "Are Arsenal linked with any wingers right now?",
    "What's the latest on Malick Fofana?",
    "What's the latest with Gakpo?",
    "What happened with Elliott?",
    "What's happening in the world right now?",
)

# "Are Liverpool looking for midfielders?" is deliberately not in the list
# above - see TestKnownLimitation below. It carries no recency word
# ("now"/"today"/"latest"/...) and no "happened with/to X" marker shape,
# so the current minimal, generic heuristic honestly does not route it to
# live_information. Adding a bare "looking for" marker would catch it,
# but "looking for" is common in ordinary, non-time-sensitive questions
# ("what should I be looking for in a used car") and would then wrongly
# pull those into the live-data path too - the exact over-triggering
# requirement #7 (ordinary knowledge requests must not unnecessarily
# require live data) exists to prevent. A real limitation, honestly left
# as one rather than patched with an imprecise marker.


# ---------------------------------------------------------------------------
# Mocking Wikipedia's HTTP layer - never a real socket
# ---------------------------------------------------------------------------


class FakeHTTPResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def wikipedia_payload(results: list[dict]) -> bytes:
    return json.dumps({"query": {"search": results}}).encode("utf-8")


def patch_wikipedia_response(monkeypatch, results: list[dict]):
    def fake_urlopen(request, timeout=None):
        return FakeHTTPResponse(wikipedia_payload(results))

    monkeypatch.setattr(
        "src.providers.wikipedia_live_info_provider.urllib.request.urlopen",
        fake_urlopen,
    )


def patch_wikipedia_failure(monkeypatch, exc: Exception):
    def fake_urlopen(request, timeout=None):
        raise exc

    monkeypatch.setattr(
        "src.providers.wikipedia_live_info_provider.urllib.request.urlopen",
        fake_urlopen,
    )


SAMPLE_RESULTS = [
    {
        "title": "Malick Fofana",
        "timestamp": "2026-08-27T01:53:11Z",
        "snippet": 'Malick Martin Fofana is a Belgian <span class="searchmatch">winger</span> at Lyon.',
    },
    {
        "title": "Arsenal F.C.",
        "timestamp": "2026-08-30T09:12:00Z",
        "snippet": "Arsenal have been linked with a move in the transfer window.",
    },
]


# ---------------------------------------------------------------------------
# 1-3. Real provider abstraction, real current information, provenance
# ---------------------------------------------------------------------------


class TestTheRealProvider:
    def test_wikipedia_provider_satisfies_the_provider_interface(self):
        assert isinstance(WikipediaLiveInfoProvider(), LiveInfoProvider)

    def test_it_returns_current_information_for_a_query(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        result = WikipediaLiveInfoProvider().fetch("Malick Fofana Arsenal")
        assert not result.is_empty
        assert "Malick Fofana" in result.content
        assert "Arsenal" in result.content

    def test_html_markup_is_stripped_from_the_content(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        result = WikipediaLiveInfoProvider().fetch("Malick Fofana")
        assert "<span" not in result.content
        assert "winger" in result.content

    def test_provenance_is_preserved(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        result = WikipediaLiveInfoProvider().fetch("Malick Fofana Arsenal")
        assert result.query == "Malick Fofana Arsenal"
        assert result.source == "wikipedia"
        assert result.retrieved_at is not None
        assert "last updated" in result.content  # each article's own recency signal

    def test_a_result_with_no_matches_is_honestly_empty(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, [])
        result = WikipediaLiveInfoProvider().fetch("a query nothing matches")
        assert result.is_empty

    def test_a_network_failure_raises_rather_than_returning_a_special_value(self, monkeypatch):
        patch_wikipedia_failure(monkeypatch, urllib.error.URLError("timed out"))
        with pytest.raises(RuntimeError):
            WikipediaLiveInfoProvider().fetch("anything")

    def test_the_factory_builds_it_by_name(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, live_info_provider="wikipedia")
        provider = build_live_info_provider(settings)
        assert isinstance(provider, WikipediaLiveInfoProvider)

    def test_the_factory_still_defaults_to_null(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={})
        provider = build_live_info_provider(settings)
        assert isinstance(provider, NullLiveInfoProvider)

    def test_only_one_fixed_host_is_ever_reached(self):
        """Structural: the endpoint is a literal constant, never built from
        the query or any caller-supplied value - this is one governed
        call, never browsing."""
        source = (REPO_ROOT / "src" / "providers" / "wikipedia_live_info_provider.py").read_text(encoding="utf-8")
        assert "en.wikipedia.org/w/api.php" in source
        assert source.count("urlopen(") == 1


def real_manager_with(*, provider_name: str = "null", monkeypatch=None, results=None, **overrides):
    """The real production path - scripts/control_center.py's own build(),
    the same function scripts/serve_ui.py builds through - never a
    hand-assembled substitute."""
    from src.composition import build_channel  # noqa: F401 - documents the same seam
    from src.config.settings import Settings

    if provider_name == "wikipedia" and monkeypatch is not None:
        patch_wikipedia_response(monkeypatch, results if results is not None else SAMPLE_RESULTS)

    settings = Settings.from_env(
        env={}, log_level="CRITICAL", memory="ephemeral",
        live_info_provider=provider_name, **overrides,
    )
    _runtime, manager, channel, conversation = control_center.build(settings)
    return manager, channel, conversation


class RecordingProvider:
    """A real LLMProvider double that records the exact prompt it was
    given - proves the Brain reasons over the retrieved evidence rather
    than the tool's output standing in as the final answer."""

    def __init__(self, text: str = "Composed answer, reasoned from the evidence."):
        self._text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "recording"

    def complete(self, prompt: str, *, system: str | None = None):
        from src.providers.base import Completion

        self.prompts.append(prompt)
        return Completion(text=self._text, provider=self.name, model="x")


def with_recording_brain(manager, text: str = "Composed answer, reasoned from the evidence."):
    provider = RecordingProvider(text)
    brain = LLMManagerBrain(provider)
    manager.brain = brain
    manager.own_skills._converse = brain.converse
    return provider


# ---------------------------------------------------------------------------
# 4-9. Production path routing, reasoning, honesty
# ---------------------------------------------------------------------------


class TestProductionRoutingAndReasoning:
    def test_a_current_information_request_reaches_live_information(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert result.payload["handled_by"] == "live_information"

    def test_what_happened_with_elliott_is_handled_appropriately(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, [
            {"title": "Harvey Elliott", "timestamp": "2026-08-31T18:00:00Z",
             "snippet": "Harvey Elliott is an English footballer."},
        ])
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=None)
        with_recording_brain(manager)

        result = manager.handle_task(Task(description="What happened with Elliott?"))

        assert result.succeeded
        assert result.payload["handled_by"] == "live_information"

    def test_what_happened_in_a_historical_event_does_not_need_live_data(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)

        result = manager.handle_task(Task(description="what happened in the French Revolution"))

        assert result.payload.get("handled_by") != "live_information"

    @pytest.mark.parametrize("text", ACCEPTANCE_EXAMPLES)
    def test_acceptance_examples_reach_the_live_data_path_generically(self, text, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)

        result = manager.handle_task(Task(description=text))

        assert result.payload is not None
        assert result.payload.get("handled_by") == "live_information"

    def test_an_ordinary_knowledge_request_does_not_use_live_data(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)

        result = manager.handle_task(KNOWLEDGE_TASK)

        assert result.payload.get("handled_by") == "open_conversation"

    def test_the_brain_actually_reasons_over_the_retrieved_evidence(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        provider = with_recording_brain(manager)

        manager.handle_task(CURRENT_TASK)

        assert provider.prompts
        assert "Live information" in provider.prompts[0]
        assert "Arsenal" in provider.prompts[0]

    def test_raw_provider_output_is_not_returned_as_the_final_answer(self, monkeypatch):
        """The tool's own provenance-embedded text ("[source: wikipedia,
        retrieved: ...]") must never leak into the final answer - only
        the Brain's own composed reasoning does."""
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager, "A natural, composed answer.")

        result = manager.handle_task(CURRENT_TASK)

        assert result.output == "A natural, composed answer."
        assert "[source: wikipedia" not in result.output


class TestKnownLimitation:
    def test_a_recency_free_status_question_is_not_currently_caught(self, monkeypatch):
        """Documents the honest boundary of the minimal heuristic, rather
        than silently leaving it unverified: "Are Liverpool looking for
        midfielders?" has no recency word and no "happened with/to"
        shape, so it is answered by Alfred's ordinary open-ended
        reasoning today, not live_information. This is a known,
        deliberate limitation - not a bug - see this test's module-level
        ACCEPTANCE_EXAMPLES comment for why it was not force-fitted."""
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)

        result = manager.handle_task(Task(description="Are Liverpool looking for midfielders?"))

        assert result.payload.get("handled_by") != "live_information"


# ---------------------------------------------------------------------------
# 10-13. Honesty: provider failure, empty result, Half Brain, no fabrication
# ---------------------------------------------------------------------------


class TestHonesty:
    def test_provider_failure_is_honest(self, monkeypatch):
        patch_wikipedia_failure(monkeypatch, urllib.error.URLError("unreachable"))
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        _runtime, manager, *_ = control_center.build(settings)
        with_recording_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "live_information" in result.error

    def test_empty_provider_result_is_honest(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, [])
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=None)
        with_recording_brain(manager)

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded
        assert "no current information is available" in result.error

    def test_half_brain_is_honest(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        # default NullBrain - never wired to a real Brain

        result = manager.handle_task(CURRENT_TASK)

        assert not result.succeeded

    def test_no_fabricated_current_information(self, monkeypatch):
        """Structural: request.converse is only ever called after a
        successful tool call - the Brain cannot be asked to reason about
        current information that was never actually retrieved."""
        source = inspect.getsource(
            __import__("src.manager.capabilities", fromlist=["live_information"]).live_information
        )
        converse_index = source.index("request.converse(")
        outcome_check_index = source.index("if not outcome.ok:")
        assert outcome_check_index < converse_index


# ---------------------------------------------------------------------------
# 14. Stage 12 memory boundary
# ---------------------------------------------------------------------------


class TestStage12MemoryBoundary:
    def test_live_information_does_not_pollute_durable_memory(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)
        before = len(manager.memory.recall(limit=200))

        manager.handle_task(CURRENT_TASK)

        assert len(manager.memory.recall(limit=200)) == before

    def test_explicit_remember_still_works_after_a_live_information_answer(self, monkeypatch):
        from src.conversation.conversation import Conversation, Participant, ParticipantKind
        from scripts.exchange import exchange

        manager, channel, _conv = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager)
        conversation = Conversation(participants=(Participant("you", ParticipantKind.PERSON),))

        exchange(channel, conversation, CURRENT_TASK.description)

        before = len(manager.memory.recall(limit=50))
        exchange(channel, conversation, "remember that we prefer green")
        after = manager.memory.recall(limit=50)

        assert len(after) == before + 1
        assert not any("Arsenal" in record.content for record in after)


# ---------------------------------------------------------------------------
# 15. Existing Alfred / Arthur behaviour intact
# ---------------------------------------------------------------------------


class TestExistingBehaviourIntact:
    def test_alfred_status_still_works_on_the_production_path(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        result = manager.handle_task(Task(description="status"))
        assert result.succeeded and result.agent_name == "Alfred"

    def test_arthur_email_behaviour_is_unaffected(self, monkeypatch):
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        result = manager.handle_task(Task(description="check my email"))
        assert result.agent_name == "Arthur"


# ---------------------------------------------------------------------------
# 16. Stage 11.A / 11.B isolation
# ---------------------------------------------------------------------------


class TestStage11Isolation:
    def test_the_new_files_import_nothing_from_extensions_or_evolution(self):
        for path in NEW_FILES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    module = getattr(node, "module", "") or ""
                    names = ", ".join(getattr(n, "name", "") for n in node.names)
                    assert "extensions" not in module and "extensions" not in names
                    assert "evolution" not in module and "evolution" not in names


# ---------------------------------------------------------------------------
# 17. Stage 13 collaboration intact
# ---------------------------------------------------------------------------


class TestStage13Intact:
    def test_answer_with_collaboration_still_works_on_the_production_path(self, monkeypatch):
        from scripts.exchange import answer_with_collaboration

        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        with_recording_brain(manager, "synthesised via collaboration")

        result = answer_with_collaboration(manager, Task(description="email opportunities update"))

        assert result.succeeded


# ---------------------------------------------------------------------------
# 18. Voice / provider independence
# ---------------------------------------------------------------------------


class TestVoiceAndProviderIndependence:
    def test_switching_the_live_info_provider_leaves_brain_memory_voice_untouched(self, monkeypatch):
        for name in ("null", "wikipedia"):
            manager, *_ = real_manager_with(provider_name=name, monkeypatch=monkeypatch)
            brain_before, memory_before = manager.brain, manager.memory
            manager.handle_task(CURRENT_TASK)
            assert manager.brain is brain_before
            assert manager.memory is memory_before
            assert voice_for(manager.own_skills).key == "alfred"
            assert voice_for(manager.registry.get("Arthur")).key == "arthur"


# ---------------------------------------------------------------------------
# 19. No subject-specific routing
# ---------------------------------------------------------------------------


def _non_docstring_source(path: Path) -> str:
    """The file's source with every module/class/function docstring
    removed - so a docstring's own illustrative prose (e.g. explaining
    that a query is passed through "unchanged, whether it's 'Malick
    Fofana' or 'quantum computing'") cannot be mistaken for a hardcoded
    subject branch. Live code - identifiers, string literals actually
    used at runtime - is left untouched."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstring_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", [])
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                docstring_nodes.add((body[0].lineno, body[0].col_offset))
    lines = path.read_text(encoding="utf-8").splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if (node.lineno, node.col_offset) in docstring_nodes:
                for lineno in range(node.lineno, (node.end_lineno or node.lineno) + 1):
                    if 1 <= lineno <= len(lines):
                        lines[lineno - 1] = ""
    return "\n".join(lines)


class TestNoSubjectSpecificRouting:
    def test_no_subject_word_in_the_new_files(self):
        subjects = (
            "arsenal", "liverpool", "messi", "elliott", "gakpo", "fofana",
            "lyon", "football", "transfer",
        )
        for path in NEW_FILES:
            source = _non_docstring_source(path).lower()
            for subject in subjects:
                assert subject not in source, (path.name, subject)

    def test_the_marker_phrases_name_no_entity(self):
        import src.manager.capabilities as capabilities_module

        subjects = ("arsenal", "liverpool", "messi", "elliott", "gakpo", "fofana", "football")
        for marker in capabilities_module.CURRENT_INFORMATION_MARKERS:
            for subject in subjects:
                assert subject not in marker.lower()


# ---------------------------------------------------------------------------
# 20. No arbitrary execution
# ---------------------------------------------------------------------------


class TestNoArbitraryExecution:
    def test_no_execution_primitives_in_the_new_files(self):
        for path in NEW_FILES:
            source = path.read_text(encoding="utf-8")
            for forbidden in ("eval(", "exec(", "subprocess", "os.system", "__import__"):
                assert forbidden not in source, (path.name, forbidden)


# ---------------------------------------------------------------------------
# 21. The production path actually reaches the real provider
# ---------------------------------------------------------------------------


class TestTheProductionPathReachesTheRealProvider:
    def test_control_center_build_equips_alfred_with_the_configured_provider(self, monkeypatch):
        from src.config.settings import Settings

        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        runtime, manager, channel, conversation = control_center.build(settings)

        assert runtime.live_info.name == "wikipedia"
        assert manager.own_skills.tools.permits("live_information")

    def test_channel_ask_reaches_live_information_end_to_end(self, monkeypatch):
        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        _runtime, manager, channel, _conv = control_center.build(settings)
        with_recording_brain(manager)

        response = channel.ask(CURRENT_TASK.description)

        assert response.turn.succeeded
        assert response.turn.handled_by == "Alfred"

    def test_voice_attribution_is_correct_through_the_full_path(self, monkeypatch):
        from src.config.settings import Settings
        from src.channels.channel import VoiceChannel
        from src.channels.speech import SpeechAdapter
        from src.providers.availability import Availability, AvailabilityState

        class ScriptedSpeech(SpeechAdapter):
            def __init__(self):
                self._availability = Availability(state=AvailabilityState.AVAILABLE)
                self.spoken = []

            @property
            def name(self):
                return "scripted"

            @property
            def availability(self):
                return self._availability

            def listen(self, timeout=None):
                raise RuntimeError("not needed")

            def speak(self, text, voice=None):
                self.spoken.append((text, voice))
                return True

        patch_wikipedia_response(monkeypatch, SAMPLE_RESULTS)
        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        _runtime, manager, _channel, _conv = control_center.build(settings)
        with_recording_brain(manager)

        speech = ScriptedSpeech()
        voice_channel = VoiceChannel(manager, speech=speech)
        response = voice_channel.ask(CURRENT_TASK.description)
        voice_channel.deliver(response)

        assert speech.spoken[-1][1] == voice_for(manager.own_skills)


# ---------------------------------------------------------------------------
# 22. Tool permission/governance still enforced
# ---------------------------------------------------------------------------


class TestGovernanceStillEnforced:
    def test_web_search_is_now_genuinely_granted_reaching_the_same_governed_boundary(self, monkeypatch):
        """Phase 3 (Knowledge, Research & Live Information) deliberately
        closed this gap: `web_search` was ungranted here because nothing
        filled `general_assistance`'s own named tool - now `WebSearchTool`
        does, wrapping this exact same real, governed `live_info`
        provider `LiveInformationTool` already wraps (never a second
        backend). Research genuinely reaches it in production, and - with
        no Brain configured in this test's own settings - fails honestly
        one step later than it used to: not for a missing tool, but for
        having no way to reason over the real evidence it found. Both are
        real, governed refusals; the second is a strictly more capable
        system than the first."""
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=monkeypatch)
        result = manager.handle_task(Task(description="research the history of bridges"))
        assert not result.succeeded
        assert "couldn't properly reason" in result.error.lower()

    def test_a_bare_build_manager_call_still_grants_no_web_search_by_default(self):
        """The shared composition root's own default stays conservative -
        only scripts/control_center.py's specific call site equips Alfred
        with web_search, mirroring live_information's own identical rule
        immediately below."""
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(
            env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia"
        )
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert manager.own_skills.tools.is_empty

    def test_a_bare_build_manager_call_still_grants_nothing_by_default(self):
        """The shared composition root's own default stays conservative -
        only scripts/control_center.py's specific call site equips Alfred
        with live_information; every other caller of build_manager (and
        every test in this project) is unaffected."""
        from src.composition import build_manager, build_runtime
        from src.config.settings import Settings
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        runtime = build_runtime(settings)
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        assert manager.own_skills.tools.is_empty


# ---------------------------------------------------------------------------
# Mutation tests - the two new guarantees this turn adds
# ---------------------------------------------------------------------------


class TestMutationTheMarkerPhraseSignal:
    def test_the_elliott_class_phrasing_depends_on_the_marker_set(self, monkeypatch):
        """Pins what emptying CURRENT_INFORMATION_MARKERS would break: a
        bare "what happened with X" - no recency word at all - must
        still reach live_information."""
        patch_wikipedia_response(monkeypatch, [
            {"title": "Harvey Elliott", "timestamp": "2026-08-31T18:00:00Z",
             "snippet": "Harvey Elliott is an English footballer."},
        ])
        manager, *_ = real_manager_with(provider_name="wikipedia", monkeypatch=None)
        with_recording_brain(manager)
        result = manager.handle_task(Task(description="What happened with Elliott?"))
        assert result.payload["handled_by"] == "live_information"


class TestMutationTheProductionWiring:
    def test_control_center_actually_grants_the_tool(self, monkeypatch):
        """Pins what removing control_center.py's tools= wiring would
        break: the real production path must equip Alfred with
        live_information, not merely leave the architecture available."""
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, log_level="CRITICAL", memory="ephemeral", live_info_provider="wikipedia")
        _runtime, manager, *_ = control_center.build(settings)
        assert manager.own_skills.tools.permits("live_information")
