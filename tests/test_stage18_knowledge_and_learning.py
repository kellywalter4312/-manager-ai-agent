"""Stage 18 - general knowledge, history, study, and the user's own books.

What this stage claims, and therefore what these tests have to prove:

  1. Alfred can take on a settled question about **anything** - a period,
     a person, an event, a war, a country, a science, a technology, an
     economy, a culture - and the mechanism that lets him is generic. The
     subjects below are *acceptance-test examples only*. Every one of them
     must be provably absent from the shipped source, and the tests below
     check that directly rather than trusting it.

  2. The four ways of knowing stay separate, and none of them is allowed
     to stand in for another:

         general model knowledge   the Brain's own, settled
         user documents/books      quotable, attributable, the user's own
         live/current information  LIVE_INFORMATION, and only there
         learning context          a fact about the user, in Memory

  3. Nothing is preloaded. There is no seeded historical content anywhere
     in this project; a document only exists because the user put it in
     their own folder, and the tests build those folders themselves.

  4. Half Brain stays honest. With no reasoning provider there is no path
     through the new capability that produces prose about the subject.

  5. Nothing existing was removed or rewritten. The canonical Agent,
     Brain, Memory, Identity, Capability and provider architecture is
     reused; live_information, general_assistance and open_conversation
     keep their own jobs.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.agents.tools.knowledge import KnowledgeLookupTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task, TaskStatus
from src.manager.brain import BrainAction, BrainDecision, ManagerBrain, NullBrain
from src.manager.capabilities import (
    GENERAL_ASSISTANCE,
    KNOWLEDGE,
    LEARNING_CONTEXT,
    LIVE_INFORMATION,
    MANAGER_CAPABILITIES,
    OPEN_CONVERSATION,
    RECALL_MEMORY,
    handled_words,
    knowledge_intent,
    select_capability,
)
from src.manager.knowledge import (
    KNOWLEDGE_TOOL,
    LEARNING_CONTEXT as LEARNING_CONTEXT_SOURCE,
    MODEL_KNOWLEDGE,
    SETTLED_KNOWLEDGE_NOTE,
    USER_DOCUMENTS,
    gather_knowledge_evidence,
)
from src.manager.manager import ManagerAgent
from src.manager.manager_skills import ManagerSkills
from src.manager.memory_policy import ManagerMemoryPolicy, MemoryCategory
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider
from src.providers.knowledge import (
    KnowledgeExcerpt,
    KnowledgeResult,
    KnowledgeSourceProvider,
)
from src.providers.knowledge_factory import (
    available_knowledge_providers,
    build_knowledge_provider,
)
from src.providers.null_knowledge_provider import NullKnowledgeProvider
from src.registry.agent_registry import AgentRegistry
from src.services.identity.identity import AgentIdentity, load_identity
from src.services.memory.ephemeral import EphemeralMemory
from src.services.memory.learning import (
    build_learning_record,
    extract_learning_subject,
    learning_intent,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"


# The whole point of the stage, expressed as data. These are *examples a
# person might ask about*, spread deliberately across periods, regions,
# people, wars, politics, science, technology, culture, geography,
# economics and education - and not one of them may appear anywhere in
# src/. TestNothingHereIsTopicSpecific enforces that.
ARBITRARY_SUBJECTS = [
    "Tell me about Napoleon",
    "Who was Alexander the Great",
    "What caused World War I",
    "Tell me about the causes of the Second World War",
    "What happened in the 1980s",
    "Tell me about the history of Ghana",
    "Who were the Ottomans",
    "What led to the fall of the Roman Republic",
    "Tell me about photosynthesis",
    "What caused the 2008 financial crisis",
    "How did the printing press change Europe",
    "Tell me about the geography of the Andes",
    "Why was the transistor important",
    "When was the Bretton Woods system created",
    "Tell me about Mussolini",
    "Tell me about Hitler's rise to power",
]

# Subjects that must never appear as an identifier, module name, capability
# word, marker or literal in shipped code. Every example this stage was
# specified with, plus the ones the earlier audit already pinned.
FORBIDDEN_SUBJECT_TOKENS = (
    "napoleon", "alexander", "hitler", "mussolini", "ottoman", "roman",
    "ghana", "andes", "bretton", "photosynthesis", "transistor",
    "world war", "wwi", "wwii", "1980s", "1984", "renaissance",
)


def settings(**overrides):
    from src.config.settings import Settings

    return Settings.from_env(env={}, log_level="CRITICAL", **overrides)


class ScriptedBrain(ManagerBrain):
    """A real ManagerBrain double that records what it was asked to
    reason over. Never a network call, never a provider."""

    def __init__(self, answer: str = "A genuine, composed answer."):
        self.answer = answer
        self.tasks: list[Task] = []
        self.notes: list[tuple[str, ...]] = []

    @property
    def name(self) -> str:
        return "scripted"

    def deliberate(self, task, agents=(), notes=()):
        return BrainDecision.no_view(reason="not used here", source=self.name)

    def converse(self, task, notes=()):
        self.tasks.append(task)
        self.notes.append(tuple(notes))
        return BrainDecision(
            action=BrainAction.REPORT, reason=self.answer, source=self.name
        )


class ScriptedKnowledgeProvider(KnowledgeSourceProvider):
    """A knowledge source that returns whatever a test handed it."""

    def __init__(self, *excerpts: KnowledgeExcerpt):
        self._excerpts = excerpts
        self.queries: list[str] = []

    @property
    def name(self) -> str:
        return "scripted"

    def lookup(self, query: str) -> KnowledgeResult:
        if not str(query).strip():
            raise ValueError("query must not be empty")
        self.queries.append(query)
        return KnowledgeResult(query=query, excerpts=self._excerpts, source=self.name)


def library(tmp_path: Path, **documents: str) -> DocumentKnowledgeProvider:
    """A folder of the user's own documents, built by the test.

    Nothing is preloaded anywhere in this project: a document exists
    because a test (or a person) wrote one.
    """
    folder = tmp_path / "knowledge"
    folder.mkdir(exist_ok=True)
    for name, body in documents.items():
        (folder / f"{name}.md").write_text(body, encoding="utf-8")
    return DocumentKnowledgeProvider(folder)


def skills(
    *,
    brain: ManagerBrain | None = None,
    tools: ToolSet | None = None,
    memory: EphemeralMemory | None = None,
) -> tuple[ManagerSkills, EphemeralMemory]:
    """A real ManagerSkills, wired the way composition.py wires one.

    Deliberately the canonical agent rather than a handler called
    directly: what is being tested is that the capability is reachable
    through the architecture that already exists.
    """
    store = memory if memory is not None else EphemeralMemory()
    policy = ManagerMemoryPolicy()

    def retain(record):
        verdict = policy.evaluate(record)
        if verdict.allowed:
            store.remember(record)
        return verdict

    agent = ManagerSkills(
        load_identity("IDENTITY.md"),
        tools=tools,
        converse=brain.converse if brain is not None else None,
        recall=store.recall,
        forget=store.forget,
        retain=retain,
    )
    return agent, store


# ---------------------------------------------------------------------------
# 1 - the contract: a knowledge source is provider-neutral and carries
#     provenance, exactly as the live-information contract already does
# ---------------------------------------------------------------------------


class TestTheKnowledgeContract:
    def test_an_excerpt_must_name_the_document_it_came_from(self):
        with pytest.raises(ValueError):
            KnowledgeExcerpt(document="", content="something")

    def test_an_excerpt_must_have_content(self):
        with pytest.raises(ValueError):
            KnowledgeExcerpt(document="notes.md", content="   ")

    def test_a_citation_carries_its_own_provenance(self):
        excerpt = KnowledgeExcerpt(
            document="my-notes.md", content="A passage.", locator="paragraph 4"
        )
        cited = excerpt.cite()
        assert "my-notes.md" in cited
        assert "paragraph 4" in cited
        assert "A passage." in cited

    def test_a_result_must_name_its_source(self):
        with pytest.raises(ValueError):
            KnowledgeResult(query="anything", source="")

    def test_a_result_with_no_excerpts_is_honestly_empty(self):
        result = KnowledgeResult(query="anything", source="test")
        assert result.is_empty
        assert result.citations() == ()

    def test_the_log_line_never_repeats_the_users_own_material(self):
        result = KnowledgeResult(
            query="anything",
            excerpts=(KnowledgeExcerpt(document="d.md", content="PRIVATE PASSAGE"),),
            source="test",
        )
        assert "PRIVATE PASSAGE" not in result.describe()

    def test_the_contract_names_no_vendor_and_no_subject(self):
        source = (SRC / "providers" / "knowledge.py").read_text(encoding="utf-8").lower()
        for vendor in ("anthropic", "openai", "wikipedia", "google", "requests", "urllib"):
            assert vendor not in source, vendor
        for subject in FORBIDDEN_SUBJECT_TOKENS:
            assert subject not in source, subject

    def test_every_shipped_provider_satisfies_the_one_contract(self, tmp_path):
        for provider in (NullKnowledgeProvider(), DocumentKnowledgeProvider(tmp_path)):
            assert isinstance(provider, KnowledgeSourceProvider)
            assert provider.name.strip()
            assert provider.lookup("anything at all").is_empty

    def test_an_empty_query_is_refused_by_every_provider(self, tmp_path):
        for provider in (NullKnowledgeProvider(), DocumentKnowledgeProvider(tmp_path)):
            with pytest.raises(ValueError):
                provider.lookup("   ")


# ---------------------------------------------------------------------------
# 2 - the real, user-supplied document source: generic over any subject,
#     deterministic, offline, and preloaded with nothing
# ---------------------------------------------------------------------------


class TestTheUsersOwnDocuments:
    def test_a_missing_folder_is_empty_rather_than_an_error(self, tmp_path):
        provider = DocumentKnowledgeProvider(tmp_path / "nothing-here")
        assert provider.documents() == ()
        assert provider.lookup("anything").is_empty

    def test_nothing_is_preloaded(self, tmp_path):
        """An empty folder holds nothing. This project ships no content."""
        (tmp_path / "knowledge").mkdir()
        provider = DocumentKnowledgeProvider(tmp_path / "knowledge")
        assert provider.documents() == ()

    @pytest.mark.parametrize(
        "subject,passage",
        [
            ("napoleon", "Napoleon reorganised French law through the civil code."),
            ("chemistry", "Covalent bonds share electron pairs between atoms."),
            ("negotiation", "Anchoring sets the reference point of a negotiation."),
            ("macroeconomics", "Interest rates were raised sharply to break inflation."),
        ],
    )
    def test_any_document_on_any_subject_is_found_the_same_way(
        self, tmp_path, subject, passage
    ):
        """One code path for every subject. The parametrisation is the
        proof: nothing in the provider distinguishes these four."""
        provider = library(tmp_path, **{f"{subject}-notes": passage})
        result = provider.lookup(passage.split(".")[0])
        assert not result.is_empty
        assert result.excerpts[0].document == f"{subject}-notes.md"
        assert passage in result.excerpts[0].content

    def test_a_book_the_user_added_later_is_found_without_a_restart(self, tmp_path):
        folder = tmp_path / "knowledge"
        folder.mkdir()
        provider = DocumentKnowledgeProvider(folder)
        assert provider.lookup("deliberate practice").is_empty

        (folder / "a-book.md").write_text(
            "Deliberate practice means working at the edge of your ability.",
            encoding="utf-8",
        )
        assert not provider.lookup("deliberate practice").is_empty

    def test_results_are_deterministic(self, tmp_path):
        provider = library(
            tmp_path,
            one="The treaty ended the war and redrew the borders.",
            two="The treaty was signed after long negotiation over borders.",
        )
        first = provider.lookup("treaty borders")
        second = provider.lookup("treaty borders")
        assert [e.cite() for e in first.excerpts] == [e.cite() for e in second.excerpts]

    def test_a_single_shared_word_is_not_a_citation(self, tmp_path):
        """One word in common is a coincidence. Quoting on it would fill a
        prompt with passages that merely contain 'the'."""
        provider = library(tmp_path, notes="The quick brown fox jumped over it.")
        assert provider.lookup("the origins of constitutional government").is_empty

    def test_excerpts_are_bounded_and_say_when_they_were_cut(self, tmp_path):
        provider = DocumentKnowledgeProvider(
            library(tmp_path, long="x").directory, max_excerpt_chars=40
        )
        (provider.directory / "long.md").write_text(
            "constitutional government " * 50, encoding="utf-8"
        )
        result = provider.lookup("constitutional government")
        assert result.excerpts[0].content.endswith("...")
        assert len(result.excerpts[0].content) <= 44

    def test_an_unreadable_document_does_not_take_the_others_down(self, tmp_path):
        provider = library(
            tmp_path,
            good="Marginal cost equals marginal revenue at the optimum.",
        )
        # A file with a suffix this provider does not read is simply not a
        # document, and a directory that looks like one is skipped.
        (provider.directory / "not-a-document.bin").write_text("noise", encoding="utf-8")
        result = provider.lookup("marginal cost revenue")
        assert not result.is_empty
        assert all(e.document.endswith(".md") for e in result.excerpts)

    def test_it_reaches_no_network(self, tmp_path):
        """conftest blocks every socket for the whole session, so this
        passing at all is the proof: a document source is local."""
        provider = library(tmp_path, notes="Local material only.")
        assert not provider.lookup("local material").is_empty

    def test_the_provider_names_no_subject_and_no_format_dependency(self):
        source = (
            SRC / "providers" / "document_knowledge_provider.py"
        ).read_text(encoding="utf-8").lower()
        for subject in FORBIDDEN_SUBJECT_TOKENS:
            assert subject not in source, subject


# ---------------------------------------------------------------------------
# 3 - the governed tool boundary: the same one every other tool crosses
# ---------------------------------------------------------------------------


class TestTheGovernedBoundary:
    def test_the_tool_is_named_for_the_capability_not_for_a_backend(self):
        tool = KnowledgeLookupTool(NullKnowledgeProvider())
        assert tool.spec.name == KNOWLEDGE_TOOL == "knowledge_lookup"

    def test_output_carries_the_document_each_passage_came_from(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, mybook="Trade follows the flag."))])
        outcome = tools.invoke(KNOWLEDGE_TOOL, {"query": "trade follows the flag"})
        assert outcome.ok
        assert "mybook.md" in outcome.output

    def test_finding_nothing_is_a_structured_failure_never_empty_text(self):
        """Silence must not be mistakable for material."""
        tools = ToolSet([KnowledgeLookupTool(NullKnowledgeProvider())])
        outcome = tools.invoke(KNOWLEDGE_TOOL, {"query": "anything"})
        assert not outcome.ok
        assert outcome.error

    def test_a_provider_that_raises_does_not_escape_the_boundary(self):
        class Exploding(KnowledgeSourceProvider):
            @property
            def name(self):
                return "exploding"

            def lookup(self, query):
                raise RuntimeError("disk on fire")

        tools = ToolSet([KnowledgeLookupTool(Exploding())])
        outcome = tools.invoke(KNOWLEDGE_TOOL, {"query": "anything"})
        assert not outcome.ok
        assert "disk on fire" in outcome.error

    def test_a_toolset_without_it_refuses_rather_than_finding_another_way(self):
        outcome = ToolSet().invoke(KNOWLEDGE_TOOL, {"query": "anything"})
        assert not outcome.ok

    def test_the_tool_holds_nothing_but_its_provider(self):
        tool = KnowledgeLookupTool(NullKnowledgeProvider())
        for forbidden in ("registry", "manager", "toolset", "memory", "brain"):
            assert not any(forbidden in name.lower() for name in vars(tool))


# ---------------------------------------------------------------------------
# 4 - the four kinds of knowing stay four
# ---------------------------------------------------------------------------


class TestTheFourSourcesStayDistinct:
    def test_with_nothing_equipped_it_is_honestly_empty_and_says_what_is_missing(self):
        evidence = gather_knowledge_evidence("anything at all")
        assert evidence.is_empty
        assert evidence.sources == ()
        assert any(USER_DOCUMENTS in entry for entry in evidence.unavailable)
        assert any(LEARNING_CONTEXT_SOURCE in entry for entry in evidence.unavailable)

    def test_user_documents_are_named_as_their_own_source(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="Rates rose to break inflation."))])
        evidence = gather_knowledge_evidence("rates rose inflation", tools=tools)
        assert USER_DOCUMENTS in evidence.sources
        assert any("b.md" in note for note in evidence.notes)

    def test_learning_context_is_named_as_its_own_source(self):
        store = EphemeralMemory()
        store.remember(
            build_learning_record(
                "I'm studying constitutional law", agent_name="Alfred",
                kind=MemoryCategory.LEARNING.value,
            )
        )
        evidence = gather_knowledge_evidence("constitutional law", recall=store.recall)
        assert LEARNING_CONTEXT_SOURCE in evidence.sources
        assert any("constitutional law" in note for note in evidence.notes)

    def test_live_information_is_never_gathered_here(self, tmp_path):
        """Structural. The assembler has no path to a live source, so a
        settled answer cannot silently become a current one."""
        source = (SRC / "manager" / "knowledge.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert "live_info" not in node.module, node.module
        assert "live_information" not in {
            n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
        }

    def test_evidence_is_stamped_as_settled_never_current(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="A settled fact about a thing."))])
        evidence = gather_knowledge_evidence("settled fact thing", tools=tools)
        notes = evidence.with_settlement_note()
        assert notes[-1] == SETTLED_KNOWLEDGE_NOTE
        assert "None of it is live or current information" in SETTLED_KNOWLEDGE_NOTE

    def test_an_empty_evidence_set_carries_no_lone_disclaimer(self):
        assert gather_knowledge_evidence("anything").with_settlement_note() == ()

    def test_a_memory_that_cannot_be_read_does_not_take_the_rest_down(self, tmp_path):
        def broken_recall(*args, **kwargs):
            raise RuntimeError("store unavailable")

        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="Bonds price off yields."))])
        evidence = gather_knowledge_evidence(
            "bonds price yields", tools=tools, recall=broken_recall
        )
        assert USER_DOCUMENTS in evidence.sources


# ---------------------------------------------------------------------------
# 5 - the capability: one handler for every subject there has ever been
# ---------------------------------------------------------------------------


class TestOneCapabilityForEverySubject:
    @pytest.mark.parametrize("question", ARBITRARY_SUBJECTS)
    def test_every_subject_reaches_the_same_handler(self, question):
        assert select_capability(Task(description=question), MANAGER_CAPABILITIES) is KNOWLEDGE

    @pytest.mark.parametrize("question", ARBITRARY_SUBJECTS)
    def test_every_subject_reaches_a_real_answer_when_a_brain_exists(self, question):
        brain = ScriptedBrain("Here is a genuine, reasoned answer.")
        agent, _ = skills(brain=brain)
        result = agent.execute(Task(description=question))
        assert result.succeeded, question
        assert result.output == "Here is a genuine, reasoned answer."

    def test_the_registry_is_the_extension_point_and_nothing_else_changed(self):
        assert MANAGER_CAPABILITIES["knowledge"] is KNOWLEDGE
        assert MANAGER_CAPABILITIES["learning_context"] is LEARNING_CONTEXT
        # The orchestrator still knows no capability by name.
        orchestrator = (SRC / "manager" / "manager.py").read_text(encoding="utf-8")
        for name in ("knowledge_inquiry", "learning_context", "KNOWLEDGE"):
            assert name not in orchestrator, name

    def test_it_defers_to_live_information_for_a_currency_signal(self):
        for current in (
            "What is happening in the region now",
            "Tell me about the latest developments",
            "What's the latest on the election",
            "Any update on the situation",
        ):
            assert not knowledge_intent(Task(description=current))
        assert (
            select_capability(
                Task(description="What is happening in the region now"), MANAGER_CAPABILITIES
            )
            is LIVE_INFORMATION
        )

    def test_it_defers_to_memory_commands(self):
        for memory_command in (
            "What do you remember about what caused that",
            "What do you know about who was involved",
            "Please forget what I said about why it was raised",
        ):
            assert not knowledge_intent(Task(description=memory_command))
        assert (
            select_capability(
                Task(description="What do you remember about what caused that"),
                MANAGER_CAPABILITIES,
            )
            is RECALL_MEMORY
        )

    def test_it_defers_to_an_explicit_capture_request(self):
        assert not knowledge_intent(Task(description="Record this: why did that happen"))

    def test_existing_handlers_keep_their_own_work(self):
        assert (
            select_capability(Task(description="research this topic"), MANAGER_CAPABILITIES)
            is GENERAL_ASSISTANCE
        )
        assert (
            select_capability(
                Task(description="Teach me how a car engine works."), MANAGER_CAPABILITIES
            )
            is OPEN_CONVERSATION
        )
        assert (
            select_capability(Task(description="status"), MANAGER_CAPABILITIES).name
            == "roster_report"
        )


# ---------------------------------------------------------------------------
# 6 - the user's own books actually reach the reasoning step
# ---------------------------------------------------------------------------


class TestTheUsersBooksReachTheAnswer:
    def test_a_supplied_document_becomes_evidence_the_brain_sees(self, tmp_path):
        brain = ScriptedBrain()
        tools = ToolSet(
            [KnowledgeLookupTool(library(tmp_path, mybook="Discipline compounds over time."))]
        )
        agent, _ = skills(brain=brain, tools=tools)

        result = agent.execute(Task(description="Tell me about discipline compounding over time"))

        assert result.succeeded
        assert any("Discipline compounds over time." in note for note in brain.notes[0])
        assert any("mybook.md" in note for note in brain.notes[0])
        assert USER_DOCUMENTS in result.payload["knowledge_sources"]

    def test_the_answer_reports_which_kinds_of_knowing_it_used(self, tmp_path):
        brain = ScriptedBrain()
        agent, _ = skills(brain=brain)
        result = agent.execute(Task(description="Tell me about a settled subject"))
        assert result.payload["knowledge_sources"] == (MODEL_KNOWLEDGE,)
        assert result.payload["currency"] == "settled"

    def test_a_document_is_not_copied_into_memory(self, tmp_path):
        """A book belongs in the user's folder, not in the memory store."""
        brain = ScriptedBrain()
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="A long passage of a book."))])
        agent, store = skills(brain=brain, tools=tools)
        agent.execute(Task(description="Tell me about a long passage of a book"))
        assert store.recall(limit=50) == ()

    def test_an_open_ended_question_also_draws_on_the_users_books(self, tmp_path):
        """The same evidence, offered to open_conversation - additive, so
        an unequipped Alfred is unchanged."""
        brain = ScriptedBrain()
        tools = ToolSet(
            [KnowledgeLookupTool(library(tmp_path, notes="An engine converts fuel to motion."))]
        )
        agent, _ = skills(brain=brain, tools=tools)
        agent.execute(Task(description="Teach me how an engine converts fuel to motion"))
        assert any("notes.md" in note for note in brain.notes[0])

    def test_an_unequipped_alfred_sends_exactly_what_he_always_did(self):
        """Byte-identical to the pre-Stage-18 call: recognition notes and
        nothing else. No knowledge tool is granted and no memory is wired,
        so the assembler contributes nothing and the settled-knowledge
        line - which qualifies evidence - is not appended to an empty
        evidence set."""
        brain = ScriptedBrain()
        task = Task(description="Teach me how a car engine works.")
        agent = ManagerSkills(load_identity("IDENTITY.md"), converse=brain.converse)

        agent.execute(task)

        assert brain.notes[0] == tuple(agent.recognise(task).notes())
        assert SETTLED_KNOWLEDGE_NOTE not in brain.notes[0]


# ---------------------------------------------------------------------------
# 7 - Half Brain stays honest
# ---------------------------------------------------------------------------


class TestHalfBrainRefusesRatherThanFabricates:
    def test_no_brain_and_no_documents_names_both_gaps(self):
        agent, _ = skills(brain=NullBrain())
        result = agent.execute(Task(description="Tell me about Napoleon"))
        assert not result.succeeded
        assert "no language model" in result.error
        assert KNOWLEDGE_TOOL in result.error

    @pytest.mark.parametrize("question", ARBITRARY_SUBJECTS)
    def test_it_never_invents_a_single_fact_about_the_subject(self, question):
        agent, _ = skills(brain=NullBrain())
        result = agent.execute(Task(description=question))
        assert not result.succeeded
        spoken = ((result.output or "") + (result.error or "")).lower()
        for invented in (
            "emperor", "waterloo", "1914", "1939", "chlorophyll", "empire",
            "treaty", "invaded", "born in", "died in",
        ):
            assert invented not in spoken, (question, invented)

    def test_documents_found_but_no_brain_is_still_a_refusal(self, tmp_path):
        tools = ToolSet([KnowledgeLookupTool(library(tmp_path, b="A passage about a thing."))])
        agent, _ = skills(brain=NullBrain(), tools=tools)
        result = agent.execute(Task(description="Tell me about a passage about a thing"))
        assert not result.succeeded
        assert "relevant passage" in result.error
        assert "can't reason" in result.error

    def test_a_manager_built_with_no_brain_at_all_refuses_rather_than_crashing(self):
        agent = ManagerSkills(AgentIdentity(name="Alfred", capabilities=frozenset({"roster"})))
        result = agent.execute(Task(description="Tell me about anything at all"))
        assert not result.succeeded
        assert "would rather say so than guess" in result.error

    def test_it_never_claims_currency(self, tmp_path):
        brain = ScriptedBrain()
        agent, _ = skills(brain=brain, tools=ToolSet([KnowledgeLookupTool(
            library(tmp_path, b="A settled statement about a thing.")
        )]))
        result = agent.execute(Task(description="Tell me about a settled statement"))
        assert result.payload["currency"] == "settled"
        assert SETTLED_KNOWLEDGE_NOTE in brain.notes[0]


# ---------------------------------------------------------------------------
# 8 - learning context: a fact about the user, in the canonical Memory
# ---------------------------------------------------------------------------


class TestLearningContextIsUserKnowledge:
    @pytest.mark.parametrize(
        "said,subject",
        [
            ("I'm studying the causes of the First World War", "the causes of the First World War"),
            ("I am learning about organic chemistry", "organic chemistry"),
            ("I'm reading a book on negotiation", "a book on negotiation"),
            ("I'm revising for my constitutional law paper", "my constitutional law paper"),
            ("Alfred, I'm preparing for a certification exam", "a certification exam"),
        ],
    )
    def test_any_field_of_study_is_recognised_the_same_way(self, said, subject):
        assert learning_intent(said)
        assert extract_learning_subject(said) == subject

    def test_an_ordinary_sentence_is_not_a_learning_statement(self):
        for ordinary in (
            "check my email",
            "Tell me about Napoleon",
            "I'm taking the bus",
            "thanks",
        ):
            assert not learning_intent(ordinary), ordinary

    def test_it_is_stored_through_the_one_governed_write_path(self):
        agent, store = skills(brain=ScriptedBrain())
        result = agent.execute(Task(description="I'm studying macroeconomics this term"))
        assert result.succeeded
        stored = store.recall(kind=MemoryCategory.LEARNING.value)
        assert len(stored) == 1
        assert "macroeconomics this term" in stored[0].content

    def test_the_policy_admits_it_as_its_own_category(self):
        record = build_learning_record(
            "I'm studying anything at all", agent_name="Alfred",
            kind=MemoryCategory.LEARNING.value,
        )
        verdict = ManagerMemoryPolicy().evaluate(record)
        assert verdict.allowed
        assert verdict.category == "learning"

    def test_it_stores_the_subject_and_not_the_material(self):
        record = build_learning_record(
            "I'm reading a book on negotiation", agent_name="Alfred",
            kind=MemoryCategory.LEARNING.value,
        )
        assert record.context["subject"] == "a book on negotiation"
        assert len(record.content) < 100

    def test_a_manager_with_no_memory_refuses_honestly(self):
        agent = ManagerSkills(load_identity("IDENTITY.md"))
        result = agent.execute(Task(description="I'm studying macroeconomics"))
        assert not result.succeeded
        assert "remember" in result.error

    def test_what_the_user_is_studying_reaches_a_later_explanation(self):
        """The loop that makes this worth storing at all."""
        brain = ScriptedBrain()
        agent, store = skills(brain=brain)
        agent.execute(Task(description="I'm studying the industrial revolution"))

        agent.execute(Task(description="Tell me about the industrial revolution"))

        assert any("industrial revolution" in note.lower() for note in brain.notes[-1])
        assert any("Learning context" in note for note in brain.notes[-1])

    def test_it_can_be_removed_like_any_other_memory(self):
        agent, store = skills(brain=ScriptedBrain())
        agent.execute(Task(description="I'm studying macroeconomics"))
        stored = store.recall(kind=MemoryCategory.LEARNING.value)
        assert store.forget(stored[0].id)
        assert store.recall(kind=MemoryCategory.LEARNING.value) == ()

    def test_the_extraction_module_knows_no_agent_and_no_subject(self):
        source = (SRC / "services" / "memory" / "learning.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert node.module in (
                    "__future__", "src.services.memory.memory",
                ), node.module
        for subject in FORBIDDEN_SUBJECT_TOKENS:
            assert subject not in source.lower(), subject


# ---------------------------------------------------------------------------
# 9 - provider neutrality and configuration
# ---------------------------------------------------------------------------


class TestItIsProviderNeutral:
    def test_a_provider_is_selected_by_name_like_every_other(self):
        assert available_knowledge_providers() == ["documents", "null"]

    def test_an_unknown_name_fails_loudly_at_startup(self):
        with pytest.raises(ValueError) as exc:
            build_knowledge_provider(settings(knowledge_provider="nonexistent"))
        assert "documents" in str(exc.value)

    def test_the_configured_provider_is_what_gets_built(self, tmp_path):
        assert build_knowledge_provider(settings(knowledge_provider="null")).name == "null"
        built = build_knowledge_provider(
            settings(knowledge_provider="documents", knowledge_dir=str(tmp_path))
        )
        assert built.name == "documents"

    def test_the_runtime_holds_exactly_one_knowledge_provider(self, tmp_path):
        from src.composition import build_runtime

        runtime = build_runtime(settings(knowledge_dir=str(tmp_path)))
        assert isinstance(runtime.knowledge, KnowledgeSourceProvider)
        assert "knowledge=" in runtime.describe()

    def test_swapping_the_backend_touches_no_capability_and_no_agent(self):
        """Structural: nothing under src/manager or src/agents names a
        concrete knowledge provider."""
        for path in list((SRC / "manager").rglob("*.py")) + list((SRC / "agents").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    assert "document_knowledge_provider" not in node.module, path.name
                    assert "null_knowledge_provider" not in node.module, path.name

    def test_the_default_installation_is_honest_rather_than_empty_by_policy(self, tmp_path):
        """"documents" pointed at a folder nobody filled answers exactly as
        "null" does - so the default is useful the moment a person adds a
        file, and claims nothing until they do."""
        documents = build_knowledge_provider(
            settings(knowledge_provider="documents", knowledge_dir=str(tmp_path / "absent"))
        )
        assert documents.lookup("anything").is_empty
        assert NullKnowledgeProvider().lookup("anything").is_empty


# ---------------------------------------------------------------------------
# 10 - nothing here is topic-specific, and nothing canonical was replaced
# ---------------------------------------------------------------------------


class TestNothingHereIsTopicSpecific:
    def test_no_shipped_identifier_or_literal_names_an_example_subject(self):
        """Parsed rather than grepped, the same instrument the existing
        architecture audit already uses and for the same stated reason: a
        subject in a comment is prose, and a subject in an identifier, a
        constant or a string literal is a topic that has been built into
        the software. This checks the second - names, assignment targets
        and every string the code actually carries."""
        for path in sorted(SRC.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.append(node.name)
                elif isinstance(node, ast.Name):
                    names.append(node.id)
                elif isinstance(node, ast.Attribute):
                    names.append(node.attr)
            docstrings = {
                id(node.body[0].value)
                for node in ast.walk(tree)
                if isinstance(
                    node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
                )
                and node.body
                and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)
            }
            carried = [
                node.value
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ]
            for subject in FORBIDDEN_SUBJECT_TOKENS:
                for name in names:
                    assert subject not in name.lower(), (path.name, name, subject)
                for text in carried:
                    assert subject not in text.lower(), (path.name, subject)

    def test_no_capability_word_is_a_subject(self):
        vocabulary = handled_words()
        for subject in FORBIDDEN_SUBJECT_TOKENS:
            for word in subject.split():
                assert word not in vocabulary, word

    def test_the_new_capability_words_are_kinds_of_knowing(self):
        assert KNOWLEDGE.capabilities == frozenset(
            {"knowledge", "background", "define", "definition", "meaning",
             "summarise", "summarize"}
        )

    def test_the_question_markers_are_grammar_not_topics(self):
        from src.manager.capabilities import KNOWLEDGE_QUESTION_MARKERS

        for marker in KNOWLEDGE_QUESTION_MARKERS:
            for subject in FORBIDDEN_SUBJECT_TOKENS:
                assert subject not in marker, marker
            # Every marker is an interrogative opening, not a noun phrase.
            assert marker.split()[0] in {
                "tell", "what", "who", "why", "how", "when", "give"
            }, marker

    def test_no_new_memory_system_and_no_second_knowledge_engine(self):
        """The canonical architecture is reused, not paralleled."""
        from src.services.memory.memory import Memory

        implementations = {
            node.name
            for path in SRC.rglob("*.py")
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
            if isinstance(node, ast.ClassDef)
            and {getattr(b, "id", getattr(b, "attr", "")) for b in node.bases} & {"Memory"}
        }
        assert implementations == {"EphemeralMemory", "PersistentMemory"}
        assert issubclass(EphemeralMemory, Memory)

    def test_the_new_capability_reaches_alfred_through_the_real_composition(self, tmp_path):
        from src.composition import build_manager, build_runtime
        from src.services.email.in_memory_mailbox import InMemoryMailbox

        runtime = build_runtime(
            settings(memory="ephemeral", knowledge_dir=str(tmp_path))
        )
        manager = build_manager(runtime, mailbox=InMemoryMailbox(), profile=None)
        result = manager.handle_task(Task(description="Tell me about anything at all"))
        assert result.status is not TaskStatus.NO_AGENT_FOUND
        assert result.agent_name == "Alfred"

    def test_the_production_conversation_path_equips_the_knowledge_tool(self):
        source = (REPO_ROOT / "scripts" / "control_center.py").read_text(encoding="utf-8")
        assert "KnowledgeLookupTool(runtime.knowledge)" in source
        # And it still equips what it already did.
        assert "LiveInformationTool(runtime.live_info)" in source
