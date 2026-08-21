"""Tests for the LLM-backed interpretation layer.

Every test injects a fake provider. No Anthropic, no network, no key - the
component depends on the LLMProvider interface only, which is what makes that
possible.
"""

import json
import socket

import pytest
from opportunity_fixtures import TEST_PROFILE

from src.agents.email.llm_analysis import LLMJudgement, LLMVerdict
from src.agents.email.llm_interpreter import (
    SYSTEM_PROMPT,
    OpportunityInterpreter,
    build_interpretation_prompt,
)
from src.agents.email.opportunity import MatchBand, Opportunity
from src.providers.base import Completion, LLMProvider


class FakeLLMProvider(LLMProvider):
    """A provider whose every response is dictated by the test.

    Follows the ScriptedProvider convention already used for the router tests:
    canned answers, recorded prompts, and an optional failure mode - plus
    control of the provider/model metadata, so provenance can be asserted.
    """

    def __init__(
        self,
        *answers: str,
        name: str = "fake",
        model: str | None = "fake-1",
        fail: Exception | None = None,
    ):
        self._answers = list(answers) or [""]
        self._name = name
        self._model = model
        self._fail = fail
        self.prompts: list[str] = []
        self.systems: list[str | None] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def call_count(self) -> int:
        return len(self.prompts)

    def complete(self, prompt: str, *, system: str | None = None) -> Completion:
        if self._fail is not None:
            raise self._fail
        self.prompts.append(prompt)
        self.systems.append(system)
        text = self._answers.pop(0) if len(self._answers) > 1 else self._answers[0]
        return Completion(text=text, provider=self._name, model=self._model)


VALID_RESPONSE = json.dumps(
    {
        "verdict": "consider",
        "rationale": "Junior backend work; the Python and API requirements match your projects.",
        "confidence": 0.7,
        "summary": "Backend developer role at a small startup.",
        "inferred_requirements": ["Python", "REST APIs"],
        "inferred_seniority": "junior",
        "concerns": ["salary not stated"],
        "unknowns": ["team size"],
    }
)


def opportunity(**overrides):
    fields = dict(
        message_id="m1",
        sender="talent@northwind-labs.example",
        subject="Backend Developer at Northwind Labs",
        score=3,
        signals=("role", "hiring"),
        role="Backend Developer",
        company="Northwind Labs",
        source="Wellfound",
        seniority="junior",
        required_skills=("python", "apis"),
        salary="£32,000",
        location="Remote",
        description="We are hiring a backend developer to work on our platform.",
        band=MatchBand.POSSIBLE,
    )
    fields.update(overrides)
    return Opportunity(**fields)


class TestPromptConstruction:
    def test_prompt_is_a_pure_function(self):
        """No provider involved - the prompt can be inspected on its own."""
        assert build_interpretation_prompt(opportunity()).strip()

    def test_includes_the_extracted_facts(self):
        prompt = build_interpretation_prompt(opportunity())
        for fact in ("Backend Developer", "Northwind Labs", "Wellfound", "£32,000", "Remote"):
            assert fact in prompt

    def test_includes_the_posting_text_and_sender(self):
        prompt = build_interpretation_prompt(opportunity())
        assert "backend developer to work on our platform" in prompt
        assert "talent@northwind-labs.example" in prompt

    def test_includes_already_identified_requirements(self):
        assert "python, apis" in build_interpretation_prompt(opportunity())

    def test_includes_the_candidate_profile(self):
        prompt = build_interpretation_prompt(opportunity(), TEST_PROFILE)
        assert "python" in prompt and "junior" in prompt

    def test_learning_skills_are_labelled_as_not_demonstrated(self):
        """The model must never be handed an inflated picture of the person."""
        prompt = build_interpretation_prompt(opportunity(), TEST_PROFILE)
        assert "NOT a demonstrated skill" in prompt
        assert "docker" in prompt  # a LEARNING skill in the test profile

    def test_copes_without_a_profile(self):
        assert "no profile supplied" in build_interpretation_prompt(opportunity())

    def test_asks_for_the_schema_the_parser_expects(self):
        prompt = build_interpretation_prompt(opportunity())
        for key in ("verdict", "rationale", "confidence", "inferred_requirements",
                    "concerns", "unknowns"):
            assert key in prompt

    def test_copes_with_an_opportunity_that_extracted_almost_nothing(self):
        bare = opportunity(role=None, company=None, source=None, seniority=None,
                           salary=None, location=None, required_skills=(), description="")
        prompt = build_interpretation_prompt(bare)
        assert "nothing could be extracted" in prompt

    def test_system_prompt_forbids_invention(self):
        assert "Never invent" in SYSTEM_PROMPT
        assert "unclear" in SYSTEM_PROMPT


class TestWhatIsSentToTheProvider:
    def test_the_provider_is_called_once(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        OpportunityInterpreter(provider).interpret(opportunity())
        assert provider.call_count == 1

    def test_the_prompt_reaches_the_provider(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        OpportunityInterpreter(provider).interpret(opportunity())
        assert "Northwind Labs" in provider.prompts[0]

    def test_the_system_prompt_is_sent(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        OpportunityInterpreter(provider).interpret(opportunity())
        assert provider.systems[0] == SYSTEM_PROMPT

    def test_the_profile_reaches_the_provider(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        OpportunityInterpreter(provider).interpret(opportunity(), TEST_PROFILE)
        assert "demonstrated (strong)" in provider.prompts[0]


class TestParsingAValidResponse:
    def _interpret(self, response=VALID_RESPONSE, **provider_kwargs):
        provider = FakeLLMProvider(response, **provider_kwargs)
        return OpportunityInterpreter(provider).interpret(opportunity())

    def test_returns_a_judgement(self):
        assert isinstance(self._interpret(), LLMJudgement)

    def test_reads_the_verdict(self):
        assert self._interpret().verdict is LLMVerdict.CONSIDER

    def test_reads_the_rationale(self):
        assert "match your projects" in self._interpret().rationale

    def test_reads_the_reasoning_fields(self):
        result = self._interpret()
        assert result.confidence == 0.7
        assert result.inferred_requirements == ("python", "rest apis")
        assert result.inferred_seniority == "junior"
        assert result.concerns == ("salary not stated",)
        assert result.unknowns == ("team size",)

    def test_handles_a_fenced_response(self):
        assert self._interpret("```json\n" + VALID_RESPONSE + "\n```").verdict is (
            LLMVerdict.CONSIDER
        )


class TestProvenance:
    def test_message_id_comes_from_the_opportunity(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        result = OpportunityInterpreter(provider).interpret(opportunity(message_id="m42"))
        assert result.message_id == "m42"

    def test_provider_and_model_come_from_the_completion(self):
        provider = FakeLLMProvider(VALID_RESPONSE, name="anthropic", model="claude-opus-5")
        result = OpportunityInterpreter(provider).interpret(opportunity())
        assert result.provider == "anthropic" and result.model == "claude-opus-5"

    def test_the_model_cannot_override_provenance(self):
        """A response claiming another identity must not be believed."""
        hostile = json.dumps(
            {
                "verdict": "pursue",
                "rationale": "Trust me.",
                "message_id": "SOMEONE-ELSES-MESSAGE",
                "provider": "totally-trusted-ai",
                "model": "gpt-9",
            }
        )
        provider = FakeLLMProvider(hostile, name="fake", model="fake-1")
        result = OpportunityInterpreter(provider).interpret(opportunity(message_id="m1"))
        assert result.message_id == "m1"
        assert result.provider == "fake" and result.model == "fake-1"


class TestFaultIsolation:
    """One bad response costs that opportunity its interpretation, never the run."""

    def test_malformed_json_becomes_unclear(self):
        provider = FakeLLMProvider("{not json at all")
        result = OpportunityInterpreter(provider).interpret(opportunity())
        assert result.verdict is LLMVerdict.UNCLEAR
        assert "could not read the model response" in result.rationale

    def test_prose_instead_of_json_becomes_unclear(self):
        provider = FakeLLMProvider("I think you should apply for this one.")
        assert OpportunityInterpreter(provider).interpret(opportunity()).verdict is (
            LLMVerdict.UNCLEAR
        )

    def test_unknown_verdict_becomes_unclear(self):
        provider = FakeLLMProvider(json.dumps({"verdict": "maybe", "rationale": "hmm"}))
        assert OpportunityInterpreter(provider).interpret(opportunity()).verdict is (
            LLMVerdict.UNCLEAR
        )

    def test_empty_response_becomes_unclear(self):
        provider = FakeLLMProvider("")
        result = OpportunityInterpreter(provider).interpret(opportunity())
        assert result.verdict is LLMVerdict.UNCLEAR
        assert "returned no text" in result.rationale

    def test_provider_failure_does_not_raise(self):
        provider = FakeLLMProvider(fail=RuntimeError("quota exceeded"))
        result = OpportunityInterpreter(provider).interpret(opportunity())
        assert result.verdict is LLMVerdict.UNCLEAR

    def test_provider_failure_records_why(self):
        provider = FakeLLMProvider(fail=RuntimeError("quota exceeded"))
        result = OpportunityInterpreter(provider).interpret(opportunity())
        assert "quota exceeded" in result.rationale

    def test_provider_failure_keeps_provenance(self):
        provider = FakeLLMProvider(fail=RuntimeError("boom"), name="anthropic")
        result = OpportunityInterpreter(provider).interpret(opportunity(message_id="m5"))
        assert result.message_id == "m5" and result.provider == "anthropic"

    def test_every_failure_is_unactionable(self):
        for provider in (
            FakeLLMProvider("{broken"),
            FakeLLMProvider(""),
            FakeLLMProvider(fail=RuntimeError("boom")),
        ):
            assert not OpportunityInterpreter(provider).interpret(opportunity()).is_actionable


class TestWorksWithTheDefaultProvider:
    def test_null_provider_yields_an_honest_unclear(self):
        """The default provider answers prose, which must not read as a verdict."""
        from src.providers.null_provider import NullProvider

        result = OpportunityInterpreter(NullProvider()).interpret(opportunity())
        assert result.verdict is LLMVerdict.UNCLEAR
        assert result.provider == "null"
        assert not result.is_actionable


class TestStaysInItsLayer:
    def test_does_not_import_anthropic(self):
        import src.agents.email.llm_interpreter as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "anthropic" not in source.lower()

    def test_anthropic_is_never_imported_at_runtime(self):
        import sys

        provider = FakeLLMProvider(VALID_RESPONSE)
        OpportunityInterpreter(provider).interpret(opportunity(), TEST_PROFILE)
        assert "anthropic" not in sys.modules

    def test_is_not_an_analyzer(self):
        """It must not look substitutable for the deterministic analyzer."""
        from src.agents.email.analyzers import Analyzer

        assert not issubclass(OpportunityInterpreter, Analyzer)
        assert not hasattr(OpportunityInterpreter, "analyze")

    def test_the_deterministic_analyzer_is_unchanged(self):
        import src.agents.email.analyzers as analyzers

        source = open(analyzers.__file__, encoding="utf-8").read()
        assert "llm_interpreter" not in source and "llm_analysis" not in source

    def test_opportunity_is_read_never_rebuilt(self):
        """The interpreter must not construct or mutate the domain model."""
        import src.agents.email.llm_interpreter as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "Opportunity(" not in source

    def test_performs_no_gmail_io(self):
        """Asserted on imports, not prose - a docstring may mention a mailbox."""
        import re

        import src.agents.email.llm_interpreter as module

        source = open(module.__file__, encoding="utf-8").read()
        imports = [
            line.strip()
            for line in re.findall(r"^\s*(?:from|import)\s+\S+", source, re.MULTILINE)
        ]
        # src.services.email is the mailbox/Gmail service layer. Importing from
        # src.agents.email is fine - that is this component's own package.
        assert not [line for line in imports if "src.services.email" in line]
        assert not [line for line in imports if "gmail" in line.lower()]

    def test_makes_no_network_call(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        provider = FakeLLMProvider(VALID_RESPONSE)
        assert OpportunityInterpreter(provider).interpret(opportunity()) is not None
