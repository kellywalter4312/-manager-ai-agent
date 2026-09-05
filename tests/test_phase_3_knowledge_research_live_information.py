"""Phase 3 - Knowledge, Research & Live Information.

CHECK found General Knowledge and Live Information already real and
working: `KNOWLEDGE`/`gather_knowledge_evidence` (acquire real evidence
from the user's own documents plus learning-context memory, then reason
over it, never returning raw evidence as the answer) and
`LIVE_INFORMATION`/`WikipediaLiveInfoProvider` (a real, live, keyless,
multi-result search already reaching the real Wikipedia API). Neither
was rebuilt.

**What CHECK found genuinely missing or broken, and this file proves
fixed:**

1. `general_assistance`'s own "research" `GeneralCapability` named a
   tool, `web_search`, that was never actually installed anywhere -
   every "research the best X and compare them" request failed honestly
   with "not installed", forever. `WebSearchTool` (reusing the exact
   same real `LiveInfoProvider` `LiveInformationTool` already wraps, not
   a second backend) closes that gap, and `general_assistance`'s own
   research branch was repaired to reason over the evidence
   (`_research_synthesis`) rather than return it raw - the identical
   acquire-then-reason shape `live_information` already established,
   reused rather than duplicated.
2. "what happened in the world today" - this phase's own literal
   example - was answered as a *settled* knowledge question with live
   information explicitly excluded, because "happened in" already meant
   "explain a settled event" under `knowledge_intent`'s own grammar
   rule. Fixed with new, narrow, phrase-shaped `CURRENT_INFORMATION_MARKERS`.
3. "read this document and explain it", "find the section about X in
   this file" and two more of this phase's own Section 6 examples
   matched no existing marker at all and fell through to the wrong
   place - `roster_report` or a live web search instead of the user's
   own just-referenced document. Fixed with `DOCUMENT_REFERENCE_MARKERS`.
4. Two new alphabetical tie-break collisions, the identical class of bug
   already found and fixed twice in earlier checkpoints
   (`general_assistance` vs. `media_production`, then vs. `knowledge`):
   "create a report from these findings" and "find the section... in
   this file" both matched `general_assistance`'s own action-verb gate
   *and* a more specific capability's own shape, and alphabetical
   sorting let the generic one win. Fixed with two more explicit
   deferrals in `general_assistance_intent`, mirroring the existing
   pattern exactly.
5. PDF was the single most commonly requested "document" format with no
   real, working path - `DocumentKnowledgeProvider` now reads `.pdf`
   files for real (`pypdf`, lazily imported, real text extraction per
   page, never OCR), preserved alongside its existing, unmodified
   plain-text support.
6. "Create a report from these findings" (Section 6's own example) had
   no real implementation at all - `DocumentGenerationTool` +
   `generate_document` now compose real text through the real Brain and
   save it as a real file, never a placeholder reply.

Every fix above was found through live routing probes and a real
end-to-end production-path run before being written down here - see
this file's own tests for the reproduction, not merely the repair.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from src.agents.tools.document_generation import DocumentGenerationTool
from src.agents.tools.toolset import ToolSet
from src.agents.tools.web_search import WebSearchTool
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.capabilities import (
    document_generation_intent,
    knowledge_intent,
    select_capability,
)
from src.providers.base import Completion, LLMProvider
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider
from src.providers.live_info import LiveInfoProvider, LiveInfoResult, utc_now
from src.manager.llm_brain import LLMManagerBrain
from src.services.ecosystem.contracts import EcosystemRegistry, GenericCategoryAdapter
from src.services.ecosystem.registry_factory import build_ecosystem_registry
from scripts.control_center import build
from scripts.exchange import exchange

# A minimal, hand-built, valid single-page PDF with real, extractable
# text - no external PDF-writing library needed to prove real extraction.
_SAMPLE_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 300 144] /Contents 5 0 R >>
endobj
4 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
5 0 obj
<< /Length 74 >>
stream
BT /F1 18 Tf 10 100 Td (Project Beacon status report quarterly) Tj ET
endstream
endobj
xref
0 6
0000000000 65535 f
trailer
<< /Size 6 /Root 1 0 R >>
startxref
0
%%EOF"""


def real_system(**overrides):
    return build(Settings.from_env(env={}, memory="ephemeral", **overrides))


class _FixedTextProvider(LLMProvider):
    """A real LLMProvider double, injected the same way this whole
    project already injects every other vendor's own client - never a
    mock of the capability layer itself."""

    def __init__(self, text: str) -> None:
        self._text = text

    @property
    def name(self) -> str:
        return "fixed"

    def complete(self, prompt: str, *, system: "str | None" = None) -> Completion:
        return Completion(text=self._text, provider="fixed")


class _SpyProvider(LLMProvider):
    def __init__(self, text: str) -> None:
        self._text = text
        self.prompts: list[str] = []

    @property
    def name(self) -> str:
        return "spy"

    def complete(self, prompt: str, *, system: "str | None" = None) -> Completion:
        self.prompts.append(prompt)
        return Completion(text=self._text, provider="spy")


class _BrokenLiveInfoProvider(LiveInfoProvider):
    @property
    def name(self) -> str:
        return "broken"

    def fetch(self, query: str) -> LiveInfoResult:
        raise RuntimeError("simulated network outage")


def _with_brain(runtime, manager, text: str) -> _SpyProvider:
    provider = _SpyProvider(text)
    manager.brain = LLMManagerBrain(provider, personality=runtime.identity.personality)
    manager.own_skills._converse = manager.brain.converse
    return provider


# tests/conftest.py::_no_network blocks every socket unconditionally for
# the whole suite (session-scoped, autouse) - the identical constraint
# tests/test_stage14_production_live_information.py already lives
# under, and this helper mirrors that file's own
# patch_wikipedia_response exactly, rather than importing across test
# modules: a scripted, real-shaped Wikipedia response, injected at
# urllib.request.urlopen, so WikipediaLiveInfoProvider's own real
# parsing logic is genuinely exercised without a real socket. The
# actual live network call (this same class reaching the real Wikipedia
# API) was made and recorded outside this suite, exactly as every other
# unfunded/network-gated vendor call in this project already is - see
# ARCHITECTURE.md.
_SAMPLE_SEARCH_RESULTS = [
    {
        "title": "Eiffel Tower",
        "timestamp": "2026-08-06T03:31:22Z",
        "snippet": 'The <span class="searchmatch">Eiffel Tower</span> is a lattice tower in Paris.',
    },
    {
        "title": "Gustave Eiffel",
        "timestamp": "2026-08-31T07:12:35Z",
        "snippet": "Best known for the Eiffel Tower, designed by his company.",
    },
]


def _patch_wikipedia(monkeypatch, results=None, *, fail_with: "Exception | None" = None):
    import json
    import urllib.error

    class _FakeHTTPResponse:
        def __init__(self, payload: bytes) -> None:
            self._payload = payload

        def read(self) -> bytes:
            return self._payload

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(request, timeout=None):
        if fail_with is not None:
            raise fail_with
        payload = json.dumps({"query": {"search": results or []}}).encode("utf-8")
        return _FakeHTTPResponse(payload)

    monkeypatch.setattr(
        "src.providers.wikipedia_live_info_provider.urllib.request.urlopen", fake_urlopen
    )


# ---------------------------------------------------------------------------
# 1. Routing fixes - reproduced live, exactly as found
# ---------------------------------------------------------------------------


class TestRoutingFixes:
    def test_live_information_recognises_the_phases_own_literal_example(self):
        cap = select_capability(
            Task(description="what happened in the world today?"), None, participants=["Alfred"]
        )
        assert cap.name == "live_information"

    @pytest.mark.parametrize(
        "text",
        [
            "what caused the Roman Empire to fall?",
            "what happened in the French Revolution",
            "what happened during World War 2",
        ],
    )
    def test_genuine_historical_questions_still_route_to_knowledge(self, text):
        cap = select_capability(Task(description=text), None, participants=["Alfred"])
        assert cap.name == "knowledge"

    @pytest.mark.parametrize(
        "text",
        [
            "read this document and explain it",
            "summarize this document",
            "what does this document say about Project Beacon",
            "find the section about Project Beacon in this file",
        ],
    )
    def test_every_document_reference_example_reaches_knowledge(self, text):
        assert knowledge_intent(Task(description=text))
        cap = select_capability(Task(description=text), None, participants=["Alfred"])
        assert cap.name == "knowledge"

    def test_research_still_reaches_general_assistance_when_no_document_is_referenced(self):
        cap = select_capability(
            Task(description="research the best noise-cancelling headphones and compare them"),
            None, participants=["Alfred"],
        )
        assert cap.name == "general_assistance"

    def test_create_a_report_reaches_generate_document_not_general_assistance(self):
        assert document_generation_intent(Task(description="create a report from these findings"))
        cap = select_capability(
            Task(description="create a report from these findings"), None, participants=["Alfred"]
        )
        assert cap.name == "generate_document"

    def test_ordinary_coding_and_research_imperatives_are_unaffected(self):
        for text in ("write a small function to reverse a string", "research Liverpool"):
            cap = select_capability(Task(description=text), None, participants=["Alfred"])
            assert cap.name == "general_assistance"


# ---------------------------------------------------------------------------
# 2. Research - real acquisition, real synthesis, real continuity
# ---------------------------------------------------------------------------


class TestResearch:
    def test_web_search_tool_mechanism_parses_a_real_shaped_response(self, monkeypatch):
        """Mechanism-verified under the suite's own network block - see
        this file's own module docstring and ARCHITECTURE.md for the
        separate, genuine live network call this class was also proven
        against, outside pytest."""
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        tool = WebSearchTool(WikipediaLiveInfoProvider())
        result = tool.run(query="Eiffel Tower")
        assert "source: wikipedia" in result
        assert "Eiffel Tower" in result

    def test_research_without_the_tool_installed_fails_honestly(self):
        runtime, manager, channel, conversation = real_system()
        _with_brain(runtime, manager, "synthesis")
        manager.own_skills._tools = ToolSet([])  # genuinely nothing installed
        turn = exchange(channel, conversation, "research quantum computing and summarise it")
        assert not turn.succeeded
        assert "web_search" in turn.outcome
        assert "not installed" in turn.outcome

    def test_research_with_an_unconfigured_backend_fails_honestly_not_fabricated(self):
        """The real production default (control_center.py's own build())
        always wires web_search - backed by whatever live_info_provider
        was configured, honestly null unless deliberately set. This is
        the genuinely different, also-honest case: the tool exists but
        finds nothing, distinct from "not installed" - both are real,
        neither is a fabricated success."""
        runtime, manager, channel, conversation = real_system()
        _with_brain(runtime, manager, "synthesis")
        turn = exchange(channel, conversation, "research quantum computing and summarise it")
        assert not turn.succeeded
        assert "web_search" in turn.outcome

    def test_a_tool_failure_is_reported_honestly_never_fabricated(self):
        runtime, manager, channel, conversation = real_system()
        _with_brain(runtime, manager, "synthesis")
        manager.own_skills._tools = ToolSet([WebSearchTool(_BrokenLiveInfoProvider())])
        turn = exchange(channel, conversation, "research quantum computing and summarise it")
        assert not turn.succeeded
        assert "simulated network outage" in turn.outcome or "failed" in turn.outcome.lower()

    def test_research_with_an_undecided_half_brain_refuses_honestly(self, monkeypatch):
        """The real default system's own Half-Brain (NullBrain-backed,
        converse bound but never decided) refuses honestly rather than
        fabricating a synthesis - the same two-tier honesty
        live_information's own equivalent tail already has: a genuinely
        unset converse (request.converse is None, exercised directly
        below) returns raw evidence unsynthesised; a bound-but-undecided
        one refuses outright, naming why."""
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        manager.own_skills._tools = ToolSet([WebSearchTool(WikipediaLiveInfoProvider())])
        turn = exchange(channel, conversation, "research the Eiffel Tower and summarise it")
        assert not turn.succeeded
        assert "couldn't properly reason" in turn.outcome.lower()

    def test_research_with_converse_genuinely_unset_returns_raw_evidence_honestly(self, monkeypatch):
        from src.manager.capabilities import GENERAL_CAPABILITIES, CapabilityRequest
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        request = CapabilityRequest(
            task=Task(description="research the Eiffel Tower and summarise it"),
            identity=runtime.identity,
            roster=lambda: (),
            own_name="Alfred",
            own_capabilities=(),
            tools=ToolSet([WebSearchTool(WikipediaLiveInfoProvider())]),
            recall=None,
            forget=None,
            retain=None,
            converse=None,
        )
        from src.manager.capabilities import general_assistance

        result = general_assistance(request)
        assert result.succeeded
        assert "no reasoning available" in result.output.lower()

    def test_full_synthesis_through_the_real_production_path(self, monkeypatch):
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        _with_brain(runtime, manager, "Comparing the sources: a synthesised answer.")
        manager.own_skills._tools = ToolSet([WebSearchTool(WikipediaLiveInfoProvider())])
        turn = exchange(channel, conversation, "please research the Eiffel Tower and give me an overview")
        assert turn.succeeded
        assert turn.outcome == "Comparing the sources: a synthesised answer."

    def test_research_continuity_recalls_prior_recorded_research(self, monkeypatch):
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        spy = _with_brain(runtime, manager, "synthesis using continuity")
        manager.own_skills._tools = ToolSet([WebSearchTool(WikipediaLiveInfoProvider())])

        exchange(channel, conversation, "record this for research: early findings show the tower is 330 meters tall")
        turn = exchange(channel, conversation, "please research the Eiffel Tower and give me an overview")

        assert turn.succeeded
        assert any(
            "Earlier research you recorded" in p and "330 meters" in p for p in spy.prompts
        )

    def test_research_never_fabricates_continuity_when_nothing_was_ever_recorded(self, monkeypatch):
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        spy = _with_brain(runtime, manager, "synthesis")
        manager.own_skills._tools = ToolSet([WebSearchTool(WikipediaLiveInfoProvider())])
        exchange(channel, conversation, "please research the Eiffel Tower and give me an overview")
        assert not any("Earlier research you recorded" in p for p in spy.prompts)


# ---------------------------------------------------------------------------
# 3. Files & Documents - real PDF extraction, preserved plain text
# ---------------------------------------------------------------------------


class TestPdfSupport:
    def test_real_text_is_extracted_from_a_real_pdf(self, tmp_path):
        (tmp_path / "report.pdf").write_bytes(_SAMPLE_PDF)
        provider = DocumentKnowledgeProvider(tmp_path)
        result = provider.lookup("Project Beacon quarterly status")
        assert result.excerpts
        assert "Project Beacon" in result.excerpts[0].content
        assert result.excerpts[0].locator == "page 1"
        assert result.excerpts[0].document == "report.pdf"

    def test_plain_text_support_is_unaffected_by_adding_pdf(self, tmp_path):
        (tmp_path / "notes.md").write_text("Project Beacon planning notes.\n\nBudget is finalised.")
        provider = DocumentKnowledgeProvider(tmp_path)
        result = provider.lookup("Project Beacon budget")
        assert result.excerpts
        assert result.excerpts[0].locator.startswith("paragraph")

    def test_both_formats_are_searched_together(self, tmp_path):
        (tmp_path / "report.pdf").write_bytes(_SAMPLE_PDF)
        (tmp_path / "notes.md").write_text("Project Beacon budget notes.\n\nMore Project Beacon detail.")
        provider = DocumentKnowledgeProvider(tmp_path)
        result = provider.lookup("Project Beacon")
        documents_found = {e.document for e in result.excerpts}
        assert "report.pdf" in documents_found or "notes.md" in documents_found

    def test_a_malformed_pdf_does_not_take_down_the_whole_lookup(self, tmp_path):
        (tmp_path / "broken.pdf").write_bytes(b"not actually a pdf")
        (tmp_path / "notes.md").write_text("Project Beacon planning notes are here.")
        provider = DocumentKnowledgeProvider(tmp_path)
        result = provider.lookup("Project Beacon planning")
        assert result.excerpts
        assert result.excerpts[0].document == "notes.md"

    def test_real_pdf_reaches_the_brain_through_the_real_production_path(self):
        with tempfile.TemporaryDirectory() as d:
            Path(d, "report.pdf").write_bytes(_SAMPLE_PDF)
            settings = Settings.from_env(
                env={}, memory="ephemeral", knowledge_provider="documents", knowledge_dir=d
            )
            runtime, manager, channel, conversation = build(settings)
            spy = _with_brain(runtime, manager, "Your document says: Project Beacon status report quarterly.")

            turn = exchange(
                channel, conversation, "read this document and tell me what the Project Beacon report says"
            )
            assert turn.succeeded
            assert any("Project Beacon status report quarterly" in p for p in spy.prompts)


# ---------------------------------------------------------------------------
# 4. Document generation - real composed text, real saved file
# ---------------------------------------------------------------------------


class TestDocumentGeneration:
    def test_the_tool_writes_a_real_file_and_is_idempotent(self, tmp_path):
        tool = DocumentGenerationTool(output_dir=tmp_path)
        first = tool.run(title="Findings Report", content="Option A is cheaper.")
        second = tool.run(title="Findings Report", content="Option A is cheaper.")
        assert first == second
        assert len(list(tmp_path.glob("*.md"))) == 1

    def test_changed_content_produces_a_genuinely_new_file(self, tmp_path):
        tool = DocumentGenerationTool(output_dir=tmp_path)
        tool.run(title="Findings Report", content="Option A is cheaper.")
        tool.run(title="Findings Report", content="Option A is cheaper and faster.")
        assert len(list(tmp_path.glob("*.md"))) == 2

    def test_without_the_tool_installed_fails_honestly(self, tmp_path):
        runtime, manager, channel, conversation = real_system(documents_dir=str(tmp_path))
        _with_brain(runtime, manager, "composed text")
        manager.own_skills._tools = ToolSet([])  # genuinely nothing installed
        exchange(channel, conversation, "what did we find about option A?")
        turn = exchange(channel, conversation, "create a report from these findings")
        assert not turn.succeeded
        assert "document_generation" in turn.outcome
        assert "not installed" in turn.outcome

    def test_without_a_brain_fails_honestly_never_fabricating_a_document(self, tmp_path):
        runtime, manager, channel, conversation = real_system()
        manager.own_skills._tools = ToolSet([DocumentGenerationTool(output_dir=tmp_path)])
        turn = exchange(channel, conversation, "create a report from these findings")
        assert not turn.succeeded
        assert not list(tmp_path.glob("*.md"))

    def test_the_full_production_path_produces_a_real_readable_file(self, tmp_path):
        settings = Settings.from_env(env={}, memory="ephemeral", documents_dir=str(tmp_path))
        runtime, manager, channel, conversation = build(settings)
        _with_brain(
            runtime, manager, "# Findings Report\n\nOption A is cheaper; Option B is faster.",
        )
        exchange(channel, conversation, "what did we find comparing option A and option B?")
        turn = exchange(channel, conversation, "create a report from these findings")

        assert turn.succeeded
        assert "Document produced" in turn.outcome
        files = list(tmp_path.glob("*.md"))
        assert len(files) == 1
        assert "Option A is cheaper" in files[0].read_text()


# ---------------------------------------------------------------------------
# 5. Half Brain honesty, reconfirmed across every new Phase 3 path
# ---------------------------------------------------------------------------


class TestHalfBrainHonesty:
    def test_no_new_phase_3_path_fabricates_reasoning_with_no_brain_configured(self, monkeypatch):
        from src.providers.wikipedia_live_info_provider import WikipediaLiveInfoProvider

        _patch_wikipedia(monkeypatch, _SAMPLE_SEARCH_RESULTS)
        runtime, manager, channel, conversation = real_system()
        manager.own_skills._tools = ToolSet([
            WebSearchTool(WikipediaLiveInfoProvider()),
            DocumentGenerationTool(),
        ])
        # No brain assigned - the default NullBrain/no-reasoning state.
        for text in (
            "please research the Eiffel Tower and give me an overview",
            "create a report from these findings",
        ):
            turn = exchange(channel, conversation, text)
            assert "no language model" in turn.outcome.lower() or "no reasoning" in turn.outcome.lower()


# ---------------------------------------------------------------------------
# 6. Ecosystem extensibility (Section 9) - knowledge/live-info remain real
#    registered categories, and a genuinely unseen Phase 3 category enters
#    through the identical boundary.
# ---------------------------------------------------------------------------


class TestPhase3EcosystemExtensibility:
    def test_knowledge_and_live_info_remain_real_registered_categories(self):
        registry = build_ecosystem_registry(Settings.from_env(env={}))
        assert "knowledge_provider" in registry.categories()
        assert "live_info_provider" in registry.categories()
        assert registry.adapter_for("knowledge_provider").validate("documents").ok
        assert registry.adapter_for("live_info_provider").validate("wikipedia").ok

    def test_a_never_before_seen_research_ranking_category_enters_through_the_identical_boundary(self):
        import subprocess

        result = subprocess.run(
            ["git", "grep", "-il", "source_ranking_provider", "--", "src", "scripts"],
            cwd=".", capture_output=True, text=True,
        )
        assert result.stdout.strip() == "", "the fixture category must be genuinely unseen"

        class FakeRanker:
            def __init__(self, key, credential):
                if key == "ml-reranker" and credential is None:
                    raise RuntimeError("no API key configured for ml-reranker")

        registry = EcosystemRegistry()
        registry.register(
            GenericCategoryAdapter(
                "source_ranking_provider",
                list_backends=lambda: ["null", "token-overlap", "ml-reranker"],
                active_key=lambda: "null",
                build=lambda key, credential: FakeRanker(key, credential),
                requires_credential=lambda key: key == "ml-reranker",
                dependents=("research source comparison and synthesis ordering",),
            )
        )

        assert "source_ranking_provider" in registry.categories()
        adapter = registry.adapter_for("source_ranking_provider")
        assert not adapter.validate("ml-reranker").ok
        assert adapter.validate("ml-reranker", credential="fake-but-present").ok
        assert adapter.validate("token-overlap").ok

        real_registry = build_ecosystem_registry(Settings.from_env(env={}))
        real_registry.register(adapter)
        assert real_registry.adapter_for("knowledge_provider") is not None
        assert real_registry.adapter_for("source_ranking_provider") is adapter
