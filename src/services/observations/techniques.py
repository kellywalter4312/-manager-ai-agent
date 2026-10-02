"""Series analysis techniques (GM-09) - what can be found in a history of
readings: one open, governed registry over GM-01 series, never a subsystem
per technique and never per subject.

**A technique is data plus one function**, exactly as an image technique is
(``src/providers/media/image_techniques.py``): an id, a label, what kind of
work it is (``purpose``: filter, transform, detect, spectral, relate - an open
label), the words a person uses for it, how many series it takes, and a
deterministic function over real readings. Nothing elsewhere knows what
"peaks" or "periodicity" means: a request is matched against the registry's
words and whatever technique owns them runs.

**Governed as a family** (``src/core/techniques.py``): the owner withdraws or
restores any technique, and composes a new one from existing ones with no code,
through Ecosystem Evolution -

    series_technique.<id> = enabled | disabled
                          | pipeline:<step>,<step>|<label>|<phrase>;<phrase>

- a pipeline's every step but the last must produce a series ("smoothed
peaks" = smooth, then peaks). A newly developed technique joins with
``register_series_technique`` and is governed through the same id.

**Evidence, never invention.** Every technique measures the readings it was
given and says what it measured; each refuses plainly when there are too few
readings for its answer to mean anything.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Mapping, Sequence

from src.core.techniques import (
    DISABLED,
    ENABLED,
    PIPELINE_PREFIX,
    TechniqueFamily,
    parse_pipeline,
    phrase_in,
    register_technique_family,
)

SERIES_FAMILY = "series_technique"
TECHNIQUE_COMPONENT_PREFIX = f"{SERIES_FAMILY}."

Series = "list[tuple[datetime, float]]"


class SeriesTechniqueError(ValueError):
    """A plain, person-facing reason a technique could not answer."""


@dataclass(frozen=True)
class SeriesResult:
    technique_id: str
    label: str
    detail: str
    derived: "tuple[tuple[datetime, float], ...]" = ()
    findings: "tuple[dict, ...]" = ()
    numbers: "Mapping[str, Any]" = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "technique": self.technique_id, "label": self.label, "detail": self.detail,
            "derived": [[t.isoformat(), round(float(v), 6)] for t, v in self.derived],
            "findings": [_plain(dict(f)) for f in self.findings], "numbers": _plain(dict(self.numbers)),
        }


def _plain(value: Any) -> Any:
    """Numbers as plain Python values (a numeric library's own booleans and
    floats are not JSON), infinities as None."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if hasattr(value, "item") and callable(value.item):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


# (derived series or None, findings, numbers, detail)
Outcome = "tuple[Series | None, list[dict], dict, str]"


@dataclass(frozen=True)
class SeriesTechnique:
    technique_id: str
    label: str
    purpose: str
    words: "tuple[str, ...]"
    run: "Callable[[list], tuple]" = field(repr=False, compare=False)
    inputs: int = 1
    produces_series: bool = False
    minimum: int = 5

    def apply(self, series: "Sequence[Series]") -> SeriesResult:
        if len(series) != self.inputs:
            raise SeriesTechniqueError(f"{self.label} works on {self.inputs} series, not {len(series)}")
        cleaned = [_clean(s) for s in series]
        for one in cleaned:
            if len(one) < self.minimum:
                raise SeriesTechniqueError(
                    f"{self.label} needs at least {self.minimum} readings to mean anything - there are {len(one)}"
                )
        derived, findings, numbers, detail = self.run(cleaned)
        return SeriesResult(self.technique_id, self.label, detail, tuple(derived or ()), tuple(findings), dict(numbers))


def _clean(series: "Sequence[tuple[datetime, Any]]") -> "list[tuple[datetime, float]]":
    out = []
    for at, value in sorted(series, key=lambda p: p[0]):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            continue
        out.append((at, float(value)))
    return out


def _np():
    import numpy as np

    return np


def _values(series):
    return _np().array([v for _t, v in series], dtype=float)


def _seconds(series):
    t0 = series[0][0]
    return _np().array([(t - t0).total_seconds() for t, _v in series], dtype=float)


def _mad(values) -> float:
    np = _np()
    return float(np.median(np.abs(values - np.median(values))))


def _fmt(value: float) -> str:
    return f"{value:.4g}"


def _human(seconds: float) -> str:
    for unit, size in (("day", 86400), ("hour", 3600), ("minute", 60)):
        if seconds >= size:
            return f"{seconds / size:.3g} {unit}(s)"
    return f"{seconds:.3g} second(s)"


# -- filters and transforms ----------------------------------------------------

def _smooth(series_list):
    np = _np()
    series = series_list[0]
    values = _values(series)
    k = max(3, min(25, (len(values) // 10) | 1))
    half = k // 2
    padded = np.pad(values, half, mode="edge")
    smoothed = np.convolve(padded, np.ones(k) / k, mode="valid")
    derived = [(t, float(v)) for (t, _), v in zip(series, smoothed)]
    removed = float(np.std(values - smoothed))
    return derived, [], {"window_readings": k, "noise_removed_std": removed}, (
        f"moving average over {k} readings - the wiggle removed had a spread of {_fmt(removed)}"
    )


def _rate(series_list):
    series = series_list[0]
    derived = []
    for (t0, v0), (t1, v1) in zip(series, series[1:]):
        hours = (t1 - t0).total_seconds() / 3600
        if hours > 0:
            derived.append((t1, (v1 - v0) / hours))
    if not derived:
        raise SeriesTechniqueError("the readings share one moment - there is no rate to measure")
    fastest = max(derived, key=lambda p: abs(p[1]))
    return derived, [{"at": fastest[0].isoformat(), "value": fastest[1], "what": "fastest change"}], {
        "fastest_per_hour": fastest[1]}, (
        f"change per hour between consecutive readings; fastest {_fmt(fastest[1])}/h at "
        f"{fastest[0].strftime('%Y-%m-%d %H:%M')}"
    )


# -- detection -----------------------------------------------------------------

def _prominent_extremes(series, sign: int, what: str):
    np = _np()
    values = _values(series) * sign
    spread = float(values.max() - values.min())
    if spread <= 0:
        return [], {"count": 0}, f"the readings never change - no {what}"
    # Noise in one reading, from how much consecutive readings differ (a
    # difference carries two readings' noise, hence the square root of two).
    noise = 1.4826 * _mad(np.diff(values)) / math.sqrt(2) if len(values) > 2 else 0.0
    floor = max(0.15 * spread, 6.0 * noise)
    found = []
    for i in range(1, len(values) - 1):
        if not (values[i] > values[i - 1] and values[i] >= values[i + 1]):
            continue
        left = values[:i][::-1]
        right = values[i + 1:]
        higher_left = next((j for j, v in enumerate(left) if v > values[i]), len(left))
        higher_right = next((j for j, v in enumerate(right) if v > values[i]), len(right))
        base = max(float(left[:higher_left].min()) if higher_left else values[i],
                   float(right[:higher_right].min()) if higher_right else values[i])
        prominence = float(values[i] - base)
        if prominence >= floor:
            at, value = series[i]
            found.append({"at": at.isoformat(), "value": value, "what": what, "prominence": prominence})
    found.sort(key=lambda f: -f["prominence"])
    return found, {"count": len(found), "floor": floor}, (
        f"{len(found)} {what}(s) standing out by at least {_fmt(floor)} (15% of the range, or six times the noise)"
    )


def _peaks(series_list):
    found, numbers, detail = _prominent_extremes(series_list[0], 1, "peak")
    return None, found, numbers, detail


def _troughs(series_list):
    found, numbers, detail = _prominent_extremes(series_list[0], -1, "dip")
    return None, found, numbers, detail


UNUSUAL_SCORE = 3.5


def _span(count: int) -> int:
    """How many neighbouring readings say what a reading "should" be."""
    return max(3, min(6, count // 4))


def _fit(xs, ys, x: float, degree: int) -> float:
    """Where a smooth curve through the neighbours puts ``x`` - a local fit
    rather than their median, so the top of a rhythm or the middle of a climb
    is expected, not surprising."""
    np = _np()
    if len(xs) <= degree + 1 or float(np.ptp(xs)) == 0:
        return float(np.median(ys))
    return float(np.polyval(np.polyfit(xs, ys, degree), x))


def _robust_z(residuals, current: float) -> float:
    np = _np()
    centre = float(np.median(residuals))
    # A floor far below any real reading's precision: a perfectly steady
    # series leaves only arithmetic dust, which is not "unusual".
    mad = max(_mad(residuals), 1e-9 * max(1.0, float(np.max(np.abs(residuals))) if len(residuals) else 1.0))
    return 0.6745 * (current - centre) / mad


def robust_score(history: "Sequence[float]", value: float) -> "float | None":
    """How far ``value`` departs from what the readings just before it said
    it should be, in robust units of how far readings usually depart - so a
    tide at high water, a daily peak or a slow climb is not "unusual", and a
    surge on top of any of them is. None when history is too short to say.
    The one definition of "unusual": the anomalies technique and the
    condition test "does anything unusual" both use it."""
    np = _np()
    values = np.array([float(v) for v in history if isinstance(v, (int, float)) and not isinstance(v, bool)])
    if len(values) < 8:
        return None
    k = _span(len(values))
    steps = np.arange(k, dtype=float)
    # What each reading should have been, from the line through the k before it.
    residuals = np.array([values[i] - _fit(steps, values[i - k:i], float(k), 1) for i in range(k, len(values))])
    return _robust_z(residuals, float(value) - _fit(steps, values[-k:], float(k), 1))


def _anomalies(series_list):
    np = _np()
    series = series_list[0]
    values = _values(series)
    seconds = _seconds(series)
    k = _span(len(values))
    # What each reading "should" be: a smooth curve through its neighbours on
    # both sides (itself left out), so a rhythm or a trend is followed, not
    # flagged. A reading found unusual is then left out of its neighbours'
    # curves too - one surge must not make the readings beside it look odd -
    # and the check repeats until what is unusual stops changing.
    unusual: "set[int]" = set()
    for _round in range(3):
        baseline = []
        for i in range(len(values)):
            around = [j for j in range(max(0, i - 2 * k), min(len(values), i + 2 * k + 1))
                      if j != i and j not in unusual]
            around = sorted(around, key=lambda j: abs(j - i))[:2 * k]
            # Neighbours on both sides can hold a curve; neighbours on one side
            # only (the first and last readings) are extended as a line.
            both_sides = any(j < i for j in around) and any(j > i for j in around)
            degree = 2 if both_sides and len(around) >= 5 else 1
            baseline.append(_fit(seconds[around] - seconds[i], values[around], 0.0, degree))
        baseline = np.array(baseline)
        residuals = values - baseline
        usual = np.array([residuals[i] for i in range(len(values)) if i not in unusual]) if len(unusual) < len(values) else residuals
        scores = {i: _robust_z(usual, float(residuals[i])) for i in range(len(values))}
        # The first and last few readings have neighbours on one side only -
        # what they "should" be is an extension, not an interpolation - so
        # they are held to twice the bar.
        found_now = {i for i, s in scores.items()
                     if abs(s) > UNUSUAL_SCORE * (2.0 if i < k or i >= len(values) - k else 1.0)}
        if found_now == unusual:
            break
        unusual = found_now
    unusual = sorted(unusual)
    found = [{"at": series[i][0].isoformat(), "value": series[i][1], "what": "unusual reading",
              "expected": float(baseline[i]), "score": scores[i] if math.isfinite(scores[i]) else None}
             for i in unusual]
    return None, found, {"count": len(found), "neighbours": k}, (
        f"{len(found)} reading(s) far from what the readings around them said they should be "
        f"(robust z-score beyond {UNUSUAL_SCORE}; twice that for the first and last {k}, which have neighbours "
        "on one side only)"
    )


def _change_points(series_list):
    np = _np()
    series = series_list[0]
    values = _values(series)
    noise = max(1e-12, 1.4826 * _mad(np.diff(values)) / math.sqrt(2)) if len(values) > 2 else 1e-12
    found: list[int] = []

    def split(lo: int, hi: int, depth: int) -> None:
        if depth > 4 or hi - lo < 10:
            return
        best, where = 0.0, None
        for i in range(lo + 5, hi - 5 + 1):
            left, right = values[lo:i], values[i:hi]
            shift = abs(float(right.mean() - left.mean()))
            strength = shift / (noise * math.sqrt(1 / len(left) + 1 / len(right)))
            if strength > best:
                best, where = strength, i
        if where is not None and best > 6.0:
            found.append(where)
            split(lo, where, depth + 1)
            split(where, hi, depth + 1)

    split(0, len(values), 0)
    findings = []
    for i in sorted(found):
        before = float(values[max(0, i - 5):i].mean())
        after = float(values[i:i + 5].mean())
        findings.append({"at": series[i][0].isoformat(), "value": series[i][1], "what": "level changed",
                         "from": before, "to": after})
    return None, findings, {"count": len(findings), "noise": noise}, (
        f"{len(findings)} lasting change(s) of level, each far beyond the reading-to-reading noise ({_fmt(noise)})"
    )


# -- spectral ------------------------------------------------------------------

def _uniform(series, step: "float | None" = None):
    np = _np()
    seconds = _seconds(series)
    values = _values(series)
    steps = np.diff(seconds)
    steps = steps[steps > 0]
    if not len(steps):
        raise SeriesTechniqueError("the readings share one moment - nothing repeats in no time")
    step = step or float(np.median(steps))
    grid = np.arange(0.0, seconds[-1] + step / 2, step)
    return grid, np.interp(grid, seconds, values), step


def _spectrum(series_list):
    np = _np()
    series = series_list[0]
    grid, values, step = _uniform(series)
    if len(grid) < 16:
        raise SeriesTechniqueError("finding a repeating pattern needs at least 16 evenly spread moments")
    x = np.arange(len(values))
    trend = np.polyfit(x, values, 1)
    residual = (values - np.polyval(trend, x)) * np.hanning(len(values))
    power = np.abs(np.fft.rfft(residual)) ** 2
    freqs = np.fft.rfftfreq(len(values), d=step)
    power[0] = 0.0
    if len(power) > 1:
        power[1] = 0.0  # one cycle across the whole span is the trend, not a rhythm
    total = float(power.sum())
    if total <= 0:
        return None, [], {"period_seconds": None, "share": 0.0}, "no repeating pattern - the readings are flat after the trend"
    peak = int(np.argmax(power))
    share = float(power[max(0, peak - 1):peak + 2].sum() / total)
    # The bins are one cycle-per-span apart; the true rhythm usually falls
    # between two. A zero-padded transform and a parabola through the three
    # highest points place it between them (on log power, where a windowed
    # peak is close to a parabola).
    padded = np.abs(np.fft.rfft(residual, n=len(residual) * 8)) ** 2
    fine = np.fft.rfftfreq(len(residual) * 8, d=step)
    lo = max(1, peak * 8 - 8)
    top = lo + int(np.argmax(padded[lo:peak * 8 + 9]))
    frequency = float(fine[top])
    if 0 < top < len(padded) - 1:
        a, b, c = (math.log(max(padded[top + k], 1e-300)) for k in (-1, 0, 1))
        bend = a - 2 * b + c
        if bend < 0:
            frequency = float(fine[top] + 0.5 * (a - c) / bend * (fine[1] - fine[0]))
    period = 1.0 / frequency if frequency > 0 else 1.0 / float(freqs[peak])
    cycles = (grid[-1] - grid[0]) / period
    clear = share >= 0.3 and cycles >= 2
    numbers = {"period_seconds": period, "share": share, "cycles_seen": cycles, "clear": clear}
    if clear:
        detail = (f"a clear repeating pattern about every {_human(period)} ({cycles:.1f} cycles seen; "
                  f"{share:.0%} of the variation is that rhythm)")
    else:
        detail = (f"no clear repeating pattern - the strongest candidate, about every {_human(period)}, "
                  f"explains only {share:.0%} of the variation" + ("" if cycles >= 2 else " and was seen fewer than twice"))
    return None, [], numbers, detail


# -- relating two series -------------------------------------------------------

def _correlation(series_list):
    np = _np()
    a, b = series_list
    start = max(a[0][0], b[0][0])
    end = min(a[-1][0], b[-1][0])
    if end <= start:
        raise SeriesTechniqueError("the two histories do not overlap in time, so they cannot be compared")
    sa, sb = _seconds(a), _seconds(b)
    steps = np.concatenate([np.diff(sa), np.diff(sb)])
    steps = steps[steps > 0]
    step = float(np.median(steps)) if len(steps) else 60.0
    span = (end - start).total_seconds()
    grid = np.arange(0.0, span + step / 2, step)
    if len(grid) < 5:
        raise SeriesTechniqueError("the two histories overlap for too short a time to compare")
    offset_a = (start - a[0][0]).total_seconds()
    offset_b = (start - b[0][0]).total_seconds()
    va = np.interp(grid + offset_a, sa, _values(a))
    vb = np.interp(grid + offset_b, sb, _values(b))

    def pearson(x, y):
        if np.std(x) == 0 or np.std(y) == 0:
            return None
        return float(np.corrcoef(x, y)[0, 1])

    r0 = pearson(va, vb)
    if r0 is None:
        return None, [], {"r": None}, "one of the two never changes over the time they share, so nothing can be related"
    # One follows the other: pairs (first at t+s, second at t). Among shifts
    # that relate them the same way round as they already are (a repeating
    # pair also matches upside-down half a cycle away - that is not a
    # follower), the best, and of near-equals the shortest.
    best_lag, best_r = 0, r0
    max_shift = max(1, len(grid) // 4)
    for shift in sorted(range(-max_shift, max_shift + 1), key=abs):
        if shift == 0:
            continue
        x, y = (va[shift:], vb[:-shift]) if shift > 0 else (va[:shift], vb[-shift:])
        if len(x) < 5:
            continue
        r = pearson(x, y)
        if r is None or (abs(r0) >= 0.2 and (r > 0) != (r0 > 0)):
            continue
        if abs(r) > abs(best_r) + 0.002:
            best_lag, best_r = shift, r

    def relation(r):
        size = abs(r)
        if size < 0.2:
            return "do not move together"
        word = "strongly" if size >= 0.7 else "moderately" if size >= 0.4 else "weakly"
        return f"move {word} {'together' if r > 0 else 'in opposite directions'}"

    lag_seconds = best_lag * step
    detail = f"over the {_human(span)} they share, they {relation(r0)} (r = {r0:.2f})"
    # Trying many shifts finds a weak match by chance; only a strong one is
    # worth saying one follows the other.
    if best_lag and abs(best_r) >= 0.5 and abs(best_r) > abs(r0) + 0.03:
        follower, leader = ("the first", "the second") if best_lag > 0 else ("the second", "the first")
        detail += (f"; {follower} follows {leader} by about {_human(abs(lag_seconds))} "
                   f"(matched that way, r = {best_r:.2f})")
    detail += ". Moving together is not the same as one causing the other."
    return None, [], {"r": r0, "best_lag_seconds": lag_seconds, "best_r": best_r, "overlap_seconds": span}, detail


BUILT_IN_TECHNIQUES: "tuple[SeriesTechnique, ...]" = (
    SeriesTechnique("smooth", "Smooth", "filter",
                    ("smooth", "smoothed", "smoothing", "moving average", "rolling average", "filter out the noise",
                     "without the noise", "trend line", "underlying trend"), _smooth, produces_series=True),
    SeriesTechnique("rate", "Rate of change", "transform",
                    ("rate of change", "how fast is it changing", "how fast it changed", "change per hour",
                     "derivative", "speed of change"), _rate, produces_series=True, minimum=2),
    SeriesTechnique("peaks", "Peaks", "detect",
                    ("peaks", "peak times", "spikes", "highest points", "high points", "maxima", "busiest moments"), _peaks),
    SeriesTechnique("troughs", "Dips", "detect",
                    ("dips", "troughs", "lowest points", "low points", "minima", "valleys", "quietest moments"), _troughs),
    SeriesTechnique("anomalies", "Unusual readings", "detect",
                    ("anomalies", "anomaly", "outliers", "outlier", "unusual readings", "odd readings", "abnormal",
                     "anything unusual", "anything odd", "strange readings"), _anomalies, minimum=8),
    SeriesTechnique("change_points", "Changes of level", "detect",
                    ("change points", "change point", "level shift", "step change", "sudden change", "when did it change",
                     "when did it shift", "regime change", "changed level"), _change_points, minimum=20),
    SeriesTechnique("spectrum", "Repeating pattern", "spectral",
                    ("spectrum", "frequency", "frequencies", "periodic", "periodicity", "cycle", "cycles", "rhythm",
                     "repeating pattern", "repeat", "how often does it repeat", "oscillation", "oscillate", "fft"),
                    _spectrum, minimum=16),
    SeriesTechnique("correlation", "Relationship", "relate",
                    ("correlate", "correlation", "correlated", "related to", "relationship between", "move together",
                     "moves together", "move with", "moves with", "linked to", "go together"), _correlation,
                    inputs=2, minimum=5),
)

_REGISTERED: "dict[str, SeriesTechnique]" = {}
_GOVERNED_SOURCE: "Callable[[], Mapping[str, str]]" = lambda: {}


def bind_governed_values(source: "Callable[[], Mapping[str, str]]") -> None:
    """Where the running system's governed values come from - bound once by
    the composition, so recognising a request and running it read the same,
    current techniques."""
    global _GOVERNED_SOURCE
    _GOVERNED_SOURCE = source


def register_series_technique(technique: SeriesTechnique) -> None:
    """Add a newly developed technique - the code half of the governed path."""
    _REGISTERED[technique.technique_id] = technique


def _pipeline(technique_id: str, value: str, known: "Mapping[str, SeriesTechnique]") -> "SeriesTechnique | None":
    steps, label, words = parse_pipeline(value)
    if not steps or not words or any(step not in known or step == technique_id for step in steps):
        return None
    chain = tuple(known[step] for step in steps)
    if any(step.inputs != 1 for step in chain) or any(not step.produces_series for step in chain[:-1]):
        return None

    def run(series_list):
        series = series_list[0]
        details = []
        derived = findings = numbers = None
        for step in chain:
            derived, findings, numbers, detail = step.run([series])
            details.append(f"{step.label}: {detail}")
            if derived:
                series = list(derived)
        return derived, findings, numbers, "; then ".join(details)

    return SeriesTechnique(technique_id, label or technique_id, "pipeline", words, run,
                           produces_series=chain[-1].produces_series, minimum=max(s.minimum for s in chain))


def validate_technique_value(technique_id: str, value: str) -> "str | None":
    value = str(value or "").strip()
    if value in (ENABLED, DISABLED):
        return None
    if value.startswith(PIPELINE_PREFIX):
        known = {t.technique_id: t for t in (*BUILT_IN_TECHNIQUES, *_REGISTERED.values())}
        if _pipeline(technique_id, value, known) is None:
            return ("a pipeline needs existing single-series steps (every step but the last producing a series) "
                    "and at least one phrase: pipeline:<step>,<step>|<label>|<phrase>;<phrase> with steps from "
                    + ", ".join(sorted(known)))
        return None
    return f"expected '{ENABLED}', '{DISABLED}' or 'pipeline:...'"


def series_techniques(governed: "Mapping[str, str] | None" = None) -> "tuple[SeriesTechnique, ...]":
    values = dict(governed or {})
    known: "dict[str, SeriesTechnique]" = {t.technique_id: t for t in BUILT_IN_TECHNIQUES}
    known.update(_REGISTERED)
    for key, value in values.items():
        if str(key).startswith(TECHNIQUE_COMPONENT_PREFIX) and str(value).startswith(PIPELINE_PREFIX):
            technique_id = str(key)[len(TECHNIQUE_COMPONENT_PREFIX):]
            composed = _pipeline(technique_id, str(value), known)
            if composed is not None:
                known[technique_id] = composed
    return tuple(t for tid, t in known.items() if values.get(TECHNIQUE_COMPONENT_PREFIX + tid) != DISABLED)


def current_techniques() -> "tuple[SeriesTechnique, ...]":
    try:
        values = dict(_GOVERNED_SOURCE())
    except Exception:  # noqa: BLE001 - an unreadable config still leaves the built-in techniques
        values = {}
    return series_techniques(values)


def technique_for_request(text: str, techniques: "Sequence[SeriesTechnique]") -> "SeriesTechnique | None":
    """The technique whose declared phrase the request uses - the longest wins."""
    best: "tuple[int, SeriesTechnique] | None" = None
    for technique in techniques:
        length = phrase_in(text, technique.words)
        if length and (best is None or length > best[0]):
            best = (length, technique)
    return best[1] if best is not None else None


register_technique_family(TechniqueFamily(
    SERIES_FAMILY, series_techniques, validate_technique_value,
    "what can be found in a history of readings - filters, peaks, anomalies, rhythms, relationships",
))
