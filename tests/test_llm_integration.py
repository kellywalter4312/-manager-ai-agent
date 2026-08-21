"""Integration tests for the optional LLM layer inside the opportunity pipeline.

The point under test is that the two layers coexist: the rules keep deciding
everything they decided before, and a model's opinion is attached beside them
without altering any of it.

No Anthropic, no network, no key - the interpreter is injected.
"""

import dataclasses
import json
import socket

import pytest
from opportunity_fixtures import (
    ALL_EMAILS,
    TEST_PROFILE,
    UNRELATED_EMAILS,
    WELLFOUND_DIGEST,
)
from test_llm_interpreter import FakeLLMProvider

from src.agents.email.llm_analysis import LLMJudgement, LLMVerdict
from src.agents.email.llm_interpreter import OpportunityInterpreter
from src.agents.email.opportunity import Opportunity
from src.agents.email.opportunity_analysis import analyze_opportunities

VALID_RESPONSE = json.dumps(
    {
        "verdict": "consider",
        "rationale": "Junior scope with a stack close to your demonstrated work.",
        "confidence": 0.6,
        "concerns": ["salary not stated"],
        "unknowns": ["team size"],
    }
)

# Fields the rules own. None of them may change when a model is consulted.
DETERMINISTIC_FIELDS = tuple(
    f.name for f in dataclasses.fields(Opportunity) if f.name != "judgement"
)


def run(messages=ALL_EMAILS, interpreter=None):
    return analyze_opportunities(
        list(messages), profile=TEST_PROFILE, interpreter=interpreter
    )


def all_of(report):
    return list(report.ranked) + list(report.ambiguous)


def interpreter_returning(*answers, **kwargs):
    return OpportunityInterpreter(FakeLLMProvider(*answers, **kwargs))


class TestPipelineWithoutAnInterpreter:
    """The default path must be exactly what it was."""

    def test_still_finds_opportunities(self):
        assert all_of(run())

    def test_no_judgement_is_attached(self):
        assert all(o.judgement is None for o in all_of(run()))

    def test_has_judgement_reports_false(self):
        assert not any(o.has_judgement for o in all_of(run()))

    def test_unrelated_mail_is_still_ignored(self):
        report = run(UNRELATED_EMAILS)
        assert not all_of(report) and report.ignored == len(UNRELATED_EMAILS)

    def test_ranking_is_unchanged(self):
        assert [o.headline() for o in run().ranked] == [o.headline() for o in run().ranked]


class TestPipelineWithAnInterpreter:
    def test_every_opportunity_receives_a_judgement(self):
        report = run(interpreter=interpreter_returning(VALID_RESPONSE))
        assert all_of(report)
        assert all(o.has_judgement for o in all_of(report))

    def test_the_judgement_is_the_models(self):
        report = run(interpreter=interpreter_returning(VALID_RESPONSE))
        judgement = all_of(report)[0].judgement
        assert isinstance(judgement, LLMJudgement)
        assert judgement.verdict is LLMVerdict.CONSIDER
        assert judgement.concerns == ("salary not stated",)

    def test_provenance_reaches_the_attached_judgement(self):
        report = run(
            interpreter=interpreter_returning(VALID_RESPONSE, name="fake", model="fake-1")
        )
        judgement = all_of(report)[0].judgement
        assert judgement.provider == "fake" and judgement.model == "fake-1"

    def test_the_interpreter_is_called_once_per_opportunity(self):
        provider = FakeLLMProvider(VALID_RESPONSE)
        report = run(interpreter=OpportunityInterpreter(provider))
        assert provider.call_count == len(all_of(report))

    def test_each_posting_in_a_digest_gets_its_own_judgement(self):
        """One email, several opportunities - the association must not collapse."""
        report = run([WELLFOUND_DIGEST], interpreter=interpreter_returning(VALID_RESPONSE))
        found = all_of(report)
        assert len(found) == 3
        assert len({id(o.judgement) for o in found}) == 3
        assert len({o.message_id for o in found}) == 1  # same message id throughout

    def test_ambiguous_opportunities_are_interpreted_too(self):
        """Where the rules are least certain is where an opinion helps most."""
        report = run(interpreter=interpreter_returning(VALID_RESPONSE))
        assert report.ambiguous
        assert all(o.has_judgement for o in report.ambiguous)


class TestDeterministicResultIsUntouched:
    def _paired(self, interpreter):
        plain = {o.headline(): o for o in all_of(run())}
        judged = {o.headline(): o for o in all_of(run(interpreter=interpreter))}
        assert plain and plain.keys() == judged.keys()
        return plain, judged

    def test_every_rule_derived_field_is_identical(self):
        plain, judged = self._paired(interpreter_returning(VALID_RESPONSE))
        for headline, before in plain.items():
            after = judged[headline]
            for name in DETERMINISTIC_FIELDS:
                assert getattr(before, name) == getattr(after, name), (headline, name)

    def test_bands_and_scores_are_unchanged(self):
        plain, judged = self._paired(interpreter_returning(VALID_RESPONSE))
        for headline, before in plain.items():
            assert judged[headline].band is before.band
            assert judged[headline].match_score == before.match_score

    def test_a_model_saying_pursue_does_not_promote_a_poor_match(self):
        """The model advises; it does not re-rank."""
        enthusiastic = json.dumps(
            {"verdict": "pursue", "rationale": "Apply immediately!", "confidence": 1.0}
        )
        plain, judged = self._paired(interpreter_returning(enthusiastic))
        for headline, before in plain.items():
            assert judged[headline].band is before.band
            assert judged[headline].judgement.verdict is LLMVerdict.PURSUE

    def test_report_counts_are_unchanged(self):
        interpreter = interpreter_returning(VALID_RESPONSE)
        assert run().considered == run(interpreter=interpreter).considered
        assert len(run().ranked) == len(run(interpreter=interpreter).ranked)


class TestFailuresDoNotDestroyTheDeterministicResult:
    @pytest.mark.parametrize(
        "response", ["{broken json", "", "I think you should apply.", '{"verdict": "maybe"}']
    )
    def test_bad_responses_become_unclear(self, response):
        report = run(interpreter=interpreter_returning(response))
        assert all_of(report)
        assert all(o.judgement.verdict is LLMVerdict.UNCLEAR for o in all_of(report))

    def test_provider_failure_leaves_the_opportunities_intact(self):
        interpreter = interpreter_returning(fail=RuntimeError("quota exceeded"))
        plain, judged = (
            {o.headline(): o for o in all_of(run())},
            {o.headline(): o for o in all_of(run(interpreter=interpreter))},
        )
        assert plain.keys() == judged.keys()
        for headline, before in plain.items():
            assert judged[headline].band is before.band
            assert judged[headline].judgement.verdict is LLMVerdict.UNCLEAR

    def test_a_rogue_interpreter_that_raises_is_contained(self):
        """The interpreter is injected; a caller may supply anything."""

        class ExplodingInterpreter:
            provider_name = "rogue"

            def interpret(self, opportunity, profile=None):
                raise RuntimeError("kaboom")

        report = run(interpreter=ExplodingInterpreter())
        assert all_of(report)
        for opportunity in all_of(report):
            assert opportunity.judgement.verdict is LLMVerdict.UNCLEAR
            assert "kaboom" in opportunity.judgement.rationale
            assert opportunity.judgement.provider == "rogue"

    def test_failed_judgements_are_not_actionable(self):
        report = run(interpreter=interpreter_returning("{broken"))
        assert not any(o.judgement.is_actionable for o in all_of(report))


class TestWorksWithTheDefaultProvider:
    def test_null_provider_attaches_honest_unclear(self):
        from src.providers.null_provider import NullProvider

        report = run(interpreter=OpportunityInterpreter(NullProvider()))
        assert all_of(report)
        for opportunity in all_of(report):
            assert opportunity.judgement.verdict is LLMVerdict.UNCLEAR
            assert opportunity.judgement.provider == "null"


class TestNoExternalCalls:
    def test_anthropic_is_never_imported(self):
        import sys

        run(interpreter=interpreter_returning(VALID_RESPONSE))
        assert "anthropic" not in sys.modules

    def test_no_network_access(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("network access attempted")

        monkeypatch.setattr(socket, "socket", explode)
        monkeypatch.setattr(socket, "create_connection", explode)
        assert all_of(run(interpreter=interpreter_returning(VALID_RESPONSE)))

    def test_the_deterministic_analyzer_was_not_wired_up(self):
        """JobOpportunityAnalyzer stays untouched; wiring it is a later step."""
        import src.agents.email.analyzers as analyzers

        source = open(analyzers.__file__, encoding="utf-8").read()
        assert "interpreter" not in source and "llm_" not in source

    def test_opportunity_holds_a_reference_not_a_copy(self):
        """Judgement fields must not be duplicated onto Opportunity."""
        names = {f.name for f in dataclasses.fields(Opportunity)}
        for judgement_field in ("verdict", "rationale", "inferred_requirements", "unknowns"):
            assert judgement_field not in names
        assert "judgement" in names
