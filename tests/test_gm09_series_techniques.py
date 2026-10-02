"""GM-09: series analysis techniques - an open registry over GM-01 series,
governed exactly as image techniques are (one technique family among
others), reached in conversation, the Control Center and as a condition
test. Every series here is synthesised with a known truth; the subjects (a
river gauge, a kiln) appear nowhere in src/."""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

import pytest

from src.core.contracts import Task
from src.core.techniques import family_for, technique_families
from src.manager.ecosystem_capabilities import bind_ecosystem, observation_history_intent, series_analysis_intent
from src.services.automation.conditions import Condition
from src.services.ecosystem_state import build_ecosystem_state
from src.services.observations.store import Observation
from src.services.observations.techniques import (
    SeriesTechnique,
    SeriesTechniqueError,
    current_techniques,
    register_series_technique,
    robust_score,
    series_techniques,
    technique_for_request,
    validate_technique_value,
)
from tests.gm_support import System, isolate_owner_state, settings_for

T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    isolate_owner_state(monkeypatch, tmp_path)


def _by_id(governed=None):
    return {t.technique_id: t for t in series_techniques(governed or {})}


def _tide(n=216, surge_at=None, seed=11, start=T0):
    rng = random.Random(seed)
    return [(start + timedelta(minutes=20 * i),
             2.0 + 1.1 * math.sin(2 * math.pi * (20 * i / 60) / 12.42) + rng.gauss(0, 0.06) + (2.4 if i == surge_at else 0))
            for i in range(n)]


class TestTheTechniques:
    def test_a_rhythm_is_measured_between_bins(self):
        result = _by_id()["spectrum"].apply([_tide()])
        assert result.numbers["clear"] and abs(result.numbers["period_seconds"] / 3600 - 12.42) < 0.2
        assert "12.4 hour" in result.detail

    def test_noise_has_no_rhythm(self):
        rng = random.Random(3)
        noise = [(T0 + timedelta(minutes=10 * i), rng.gauss(0, 1)) for i in range(200)]
        assert not _by_id()["spectrum"].apply([noise]).numbers["clear"]

    def test_unusual_means_unusual_for_what_it_was_doing(self):
        found = _by_id()["anomalies"].apply([_tide(surge_at=150)]).findings
        assert [f["at"] for f in found] == [(T0 + timedelta(minutes=20 * 150)).isoformat()]
        assert _by_id()["anomalies"].apply([_tide()]).findings == ()   # a tide is not unusual at high water

    def test_a_steady_series_and_a_climb_are_not_unusual(self):
        steady = [(T0 + timedelta(minutes=i), 5.0) for i in range(40)]
        climb = [(T0 + timedelta(minutes=i), 10 + 0.3 * i) for i in range(40)]
        assert _by_id()["anomalies"].apply([steady]).findings == ()
        assert _by_id()["anomalies"].apply([climb]).findings == ()

    def test_peaks_dips_level_changes_rate_and_smoothing(self):
        rng = random.Random(4)
        bursts = [(T0 + timedelta(minutes=5 * i), 20 + rng.gauss(0, 2) + (60 if i in (30, 90, 150) else 0)) for i in range(200)]
        assert len(_by_id()["peaks"].apply([bursts]).findings) == 3
        shifted = [(T0 + timedelta(minutes=10 * i), (4.0 if i < 60 else 6.5) + rng.gauss(0, 0.15)) for i in range(120)]
        change = _by_id()["change_points"].apply([shifted]).findings
        assert len(change) == 1 and change[0]["at"] == (T0 + timedelta(minutes=600)).isoformat()
        assert _by_id()["smooth"].apply([shifted]).derived and _by_id()["rate"].apply([shifted]).derived
        dips = [(t, -v) for t, v in bursts]
        assert len(_by_id()["troughs"].apply([dips]).findings) == 3

    def test_two_series_relate_and_who_follows_is_said(self):
        rng = random.Random(5)
        a = [(T0 + timedelta(minutes=10 * i), math.sin(i / 8) + rng.gauss(0, 0.05)) for i in range(150)]
        b = [(T0 + timedelta(minutes=10 * i), 5 + 2 * math.sin((i - 3) / 8) + rng.gauss(0, 0.1)) for i in range(150)]
        result = _by_id()["correlation"].apply([a, b])
        assert result.numbers["r"] > 0.8 and "the second follows the first by about 30 minute" in result.detail
        unrelated = [(T0 + timedelta(minutes=10 * i), rng.gauss(0, 1)) for i in range(150)]
        assert "do not move together" in _by_id()["correlation"].apply([a, unrelated]).detail
        assert "not the same as one causing the other" in result.detail

    def test_too_few_readings_is_refused_plainly(self):
        with pytest.raises(SeriesTechniqueError, match="at least 16 readings"):
            _by_id()["spectrum"].apply([_tide(10)])
        with pytest.raises(SeriesTechniqueError, match="2 series"):
            _by_id()["correlation"].apply([_tide()])

    def test_non_numbers_are_ignored_not_guessed(self):
        mixed = [(T0 + timedelta(minutes=i), v) for i, v in enumerate([1, "on", 2, None, 3, True, 4, 5, 6])]
        assert _by_id()["smooth"].apply([mixed]).derived and len(_by_id()["smooth"].apply([mixed]).derived) == 6


class TestGovernance:
    def test_both_families_are_governed_by_one_handler(self):
        from src.services.evolution.handlers import TechniqueHandler, handler_for

        categories = {f.category for f in technique_families()}
        assert {"image_technique", "series_technique"} <= categories
        assert isinstance(handler_for("series_technique.peaks"), TechniqueHandler)
        assert isinstance(handler_for("image_technique.deblur"), TechniqueHandler)
        assert family_for("series_technique.peaks").category == "series_technique"

    def test_withdraw_compose_and_validate(self):
        assert "spectrum" not in _by_id({"series_technique.spectrum": "disabled"})
        composed = _by_id({"series_technique.smooth_peaks": "pipeline:smooth,peaks|Smoothed peaks|smoothed_peaks"})
        assert composed["smooth_peaks"].words == ("smoothed peaks",)
        assert "Smooth:" in composed["smooth_peaks"].apply([_tide()]).detail
        assert validate_technique_value("x", "pipeline:peaks,smooth|x|x") is not None   # peaks yields no series
        assert validate_technique_value("x", "pipeline:correlation|x|x") is not None    # two-series step
        assert validate_technique_value("x", "enabled") is None

    def test_a_new_technique_registers_in_code(self):
        register_series_technique(SeriesTechnique("span", "Span", "measure", ("span of values",),
                                                  lambda s: (None, [], {"span": 1}, "measured the span")))
        try:
            assert technique_for_request("what is the span of values of the gauge", series_techniques({})).technique_id == "span"
        finally:
            from src.services.observations import techniques as module
            module._REGISTERED.pop("span", None)


class TestRecognitionAndConditions:
    def test_a_technique_phrase_about_something_observed(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        ecosystem.observe([Observation("river gauge", "level_m", 2.0, observed_at=T0)])
        bind_ecosystem(ecosystem)
        try:
            ask = Task(description="were there any unusual readings in the river gauge level this week?")
            assert series_analysis_intent(ask) and not observation_history_intent(ask)
            assert not series_analysis_intent(Task(description="tell me if the river gauge does anything unusual"))
            assert not series_analysis_intent(Task(description="were there any unusual readings in the chiller?"))
        finally:
            bind_ecosystem(None)

    def test_unusual_is_a_condition_test(self, tmp_path):
        ecosystem = build_ecosystem_state(settings_for(tmp_path))
        history = _tide(60)
        ecosystem.observe([Observation("gauge", "level_m", v, observed_at=t) for t, v in history])
        condition = ecosystem.conditions.declare(Condition(condition_id="u", entity="gauge", attribute="level_m",
                                                           operator="unusual"))
        at = history[-1][0] + timedelta(minutes=20)
        assert ecosystem.engine.evaluate(condition, now=at) is None
        ecosystem.observe([Observation("gauge", "level_m", 7.5, observed_at=at)])
        assert any(e.condition_id == "u" for e in ecosystem.events.all())

    def test_robust_score_follows_the_trend(self):
        rising = [float(i) for i in range(30)]
        assert abs(robust_score(rising, 30.0)) < 3.5 and robust_score(rising, 60.0) > 3.5
        assert robust_score([1.0] * 5, 9.0) is None


class TestThroughTheRealSystem:
    def test_conversation_control_center_and_evolution(self, tmp_path):
        system = System(tmp_path)
        start = datetime.now(timezone.utc) - timedelta(minutes=20 * 216)
        system.ecosystem.observe([Observation("river gauge", "level_m", v, observed_at=t, unit="m")
                                  for t, v in _tide(surge_at=150, start=start)])
        turn = system.say("is there a rhythm in the river gauge level?")
        assert turn.capability == "series_analysis" and "12.4 hour" in turn.outcome, turn.outcome
        assert "spectrum" in system.cc("analyze")
        assert system.cc("analyze anomalies river gauge/level_m").startswith("Unusual readings: 1 reading")
        proposal = system.cc("ecosystem series_technique.spectrum propose disabled low tidy|less").split("'")[1]
        assert system.cc(f"ecosystem series_technique.spectrum run {proposal} yes").startswith("OK")
        assert system.say("is there a rhythm in the river gauge level?").capability != "series_analysis"
        assert system.cc(f"ecosystem series_technique.spectrum rollback {proposal} yes").startswith("OK")
        assert system.say("is there a rhythm in the river gauge level?").capability == "series_analysis"
        assert "series_technique.spectrum" in system.cc("ecosystem")
