"""Tests for parsing a model response into the LLMJudgement contract.

No provider, no network, no Anthropic. The parser takes text and returns a
contract, so every case here is a string literal.

The governing rule under test: transport damage is normalised, semantic
ambiguity is rejected.
"""

import json

import pytest

from src.agents.email.llm_analysis import (
    CALLER_OWNED_FIELDS,
    REQUIRED_FIELDS,
    LLMAnalysisError,
    LLMJudgement,
    LLMVerdict,
    parse_judgement,
    parse_judgement_or_unavailable,
)

VALID = {
    "verdict": "consider",
    "rationale": "Junior backend role; the Python and API work overlaps with your projects.",
    "confidence": 0.72,
    "summary": "Backend role at a small startup.",
    "inferred_requirements": ["Python", "REST APIs"],
    "inferred_seniority": "junior",
    "concerns": ["salary not stated"],
    "unknowns": ["team size"],
}


def parse(payload, **overrides):
    text = payload if isinstance(payload, str) else json.dumps(payload)
    kwargs = dict(message_id="m1", provider="anthropic", model="claude-opus-5")
    kwargs.update(overrides)
    return parse_judgement(text, **kwargs)


class TestHappyPath:
    def test_returns_a_judgement(self):
        assert isinstance(parse(VALID), LLMJudgement)

    def test_reads_the_verdict(self):
        assert parse(VALID).verdict is LLMVerdict.CONSIDER

    def test_reads_the_rationale(self):
        assert "overlaps" in parse(VALID).rationale

    def test_reads_confidence(self):
        assert parse(VALID).confidence == 0.72

    def test_reads_the_lists(self):
        result = parse(VALID)
        assert result.inferred_requirements == ("python", "rest apis")
        assert result.concerns == ("salary not stated",)
        assert result.unknowns == ("team size",)

    def test_reads_seniority(self):
        assert parse(VALID).inferred_seniority == "junior"

    def test_optional_fields_may_be_absent(self):
        minimal = {"verdict": "skip", "rationale": "Requires 8 years of Go."}
        result = parse(minimal)
        assert result.confidence == 0.0
        assert result.summary == "" and result.concerns == ()

    def test_explicit_nulls_are_treated_as_absent(self):
        payload = dict(VALID, summary=None, concerns=None, inferred_seniority=None)
        result = parse(payload)
        assert result.summary == "" and result.concerns == ()
        assert result.inferred_seniority is None


class TestTransportNoiseIsNormalised:
    """Wrapper damage: the JSON is intact, only its packaging is messy."""

    def test_markdown_fences(self):
        text = "```json\n" + json.dumps(VALID) + "\n```"
        assert parse(text).verdict is LLMVerdict.CONSIDER

    def test_fences_without_a_language_tag(self):
        assert parse("```\n" + json.dumps(VALID) + "\n```").confidence == 0.72

    def test_surrounding_prose(self):
        text = "Here is my assessment:\n" + json.dumps(VALID) + "\nHope that helps!"
        assert parse(text).verdict is LLMVerdict.CONSIDER

    def test_leading_and_trailing_whitespace(self):
        assert parse("\n\n   " + json.dumps(VALID) + "   \n\n").confidence == 0.72

    def test_verdict_case_and_padding(self):
        assert parse(dict(VALID, verdict="  PURSUE ")).verdict is LLMVerdict.PURSUE

    def test_a_bare_string_widens_to_a_list(self):
        assert parse(dict(VALID, concerns="only one concern")).concerns == (
            "only one concern",
        )

    def test_confidence_as_a_numeric_string(self):
        assert parse(dict(VALID, confidence="0.5")).confidence == 0.5

    def test_confidence_as_an_integer(self):
        assert parse(dict(VALID, confidence=1)).confidence == 1.0

    def test_unknown_extra_keys_are_ignored(self):
        assert parse(dict(VALID, invented_field="whatever")).verdict is LLMVerdict.CONSIDER

    def test_rationale_whitespace_is_collapsed(self):
        assert parse(dict(VALID, rationale="a   b\n\nc")).rationale == "a b c"


class TestSemanticAmbiguityIsRejected:
    """Repairing any of these would mean inventing what the model meant."""

    def test_invalid_json(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse("{not valid json")
        assert "not valid JSON" in str(exc.value)

    def test_no_json_at_all(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse("I think you should apply for this one.")
        assert "no JSON object" in str(exc.value)

    def test_empty_response(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse("   ")
        assert "empty response" in str(exc.value)

    @pytest.mark.parametrize("payload", ['[1, 2, 3]', '"just a string"', "42", "null"])
    def test_json_that_is_not_an_object(self, payload):
        """Rejected either at extraction or at the type check - both say why."""
        with pytest.raises(LLMAnalysisError) as exc:
            parse(payload)
        assert "JSON object" in str(exc.value)

    def test_missing_verdict(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse({"rationale": "looks fine"})
        assert "verdict" in str(exc.value)

    def test_missing_rationale(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse({"verdict": "pursue"})
        assert "rationale" in str(exc.value)

    def test_null_required_field_counts_as_missing(self):
        with pytest.raises(LLMAnalysisError):
            parse({"verdict": "pursue", "rationale": None})

    def test_blank_rationale(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse(dict(VALID, rationale="   "))
        assert "explain its verdict" in str(exc.value)

    @pytest.mark.parametrize("verdict", ["maybe", "yes", "strong match", "PURSUE-ish", ""])
    def test_unknown_verdict(self, verdict):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, verdict=verdict))

    @pytest.mark.parametrize("value", ["high", "very confident", "0.8x"])
    def test_non_numeric_confidence(self, value):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, confidence=value))

    @pytest.mark.parametrize("value", [-0.5, 1.5, 42])
    def test_out_of_range_confidence(self, value):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, confidence=value))

    def test_boolean_confidence(self):
        """True must not silently become certainty."""
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, confidence=True))

    def test_wrong_type_for_verdict(self):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, verdict=3))

    def test_wrong_type_for_rationale(self):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, rationale=["a", "b"]))

    def test_non_string_list_elements(self):
        with pytest.raises(LLMAnalysisError) as exc:
            parse(dict(VALID, concerns=["fine", 7]))
        assert "only strings" in str(exc.value)

    def test_wrong_type_for_a_list_field(self):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, inferred_requirements={"python": True}))

    def test_wrong_type_for_summary(self):
        with pytest.raises(LLMAnalysisError):
            parse(dict(VALID, summary=42))


class TestProvenanceComesFromTheCaller:
    def test_caller_provenance_is_recorded(self):
        result = parse(VALID, message_id="abc", provider="anthropic", model="claude-opus-5")
        assert result.message_id == "abc"
        assert result.provider == "anthropic"
        assert result.model == "claude-opus-5"

    def test_a_model_cannot_claim_a_different_message(self):
        result = parse(dict(VALID, message_id="SOMEONE-ELSES-MESSAGE"), message_id="m1")
        assert result.message_id == "m1"

    def test_a_model_cannot_claim_a_different_provider(self):
        result = parse(dict(VALID, provider="totally-trusted-ai"), provider="anthropic")
        assert result.provider == "anthropic"

    def test_a_model_cannot_claim_a_different_model_name(self):
        result = parse(dict(VALID, model="gpt-9"), model="claude-opus-5")
        assert result.model == "claude-opus-5"

    def test_those_fields_are_documented_as_caller_owned(self):
        assert CALLER_OWNED_FIELDS == {"message_id", "provider", "model"}

    def test_required_fields_are_documented(self):
        assert REQUIRED_FIELDS == ("verdict", "rationale")


class TestSafeParser:
    """The loop-friendly variant: never raises, always attributable."""

    def test_valid_response_parses_normally(self):
        result = parse_judgement_or_unavailable(
            json.dumps(VALID), message_id="m1", provider="anthropic"
        )
        assert result.verdict is LLMVerdict.CONSIDER

    def test_malformed_response_degrades_instead_of_raising(self):
        result = parse_judgement_or_unavailable(
            "{broken", message_id="m1", provider="anthropic"
        )
        assert result.verdict is LLMVerdict.UNCLEAR
        assert result.confidence == 0.0

    def test_the_failure_reason_is_preserved(self):
        result = parse_judgement_or_unavailable(
            "{broken", message_id="m1", provider="anthropic"
        )
        assert "could not read the model response" in result.rationale

    def test_provenance_survives_a_failure(self):
        result = parse_judgement_or_unavailable(
            "nonsense", message_id="m7", provider="anthropic", model="claude-opus-5"
        )
        assert result.message_id == "m7" and result.model == "claude-opus-5"

    def test_a_failed_parse_is_not_actionable(self):
        result = parse_judgement_or_unavailable("", message_id="m1", provider="null")
        assert not result.is_actionable

    def test_the_null_provider_response_degrades_cleanly(self):
        """NullProvider returns prose, not JSON - it must not look like a verdict."""
        from src.providers.null_provider import NullProvider

        completion = NullProvider().complete("any prompt")
        result = parse_judgement_or_unavailable(
            completion.text, message_id="m1", provider=completion.provider
        )
        assert result.verdict is LLMVerdict.UNCLEAR
        assert result.provider == "null"


class TestParserStaysInItsLayer:
    def test_does_not_import_the_opportunity_model(self):
        import src.agents.email.llm_analysis as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "from src.agents.email.opportunity import" not in source

    def test_does_not_import_any_provider(self):
        """Parsing takes text; wiring a provider is a later step."""
        import src.agents.email.llm_analysis as module

        source = open(module.__file__, encoding="utf-8").read()
        assert "src.providers" not in source

    def test_makes_no_network_call(self, monkeypatch):
        import socket

        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert parse(VALID).verdict is LLMVerdict.CONSIDER
