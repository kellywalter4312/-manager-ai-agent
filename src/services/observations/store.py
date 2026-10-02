"""The observation store (GM-01): time-indexed, persisted observations of
any entity's state or any signal, queryable by entity, attribute and time
window, with aggregation.

**Beside Memory, never inside it.** Memory holds what an agent learned, under
an admission policy built for knowledge about a person; an observation is a
reading - a temperature, a count, a position, a progress figure - and a store
that received one every few seconds would drown every recall. So it has its
own store, its own retention, and its own queries, and it is shared by every
source that observes: a watched connection, a perception run over a video, a
stream, a capture pipeline, a supervised device task.

**Append-only, one file per entity.** ``<root>/observations/<entity>.jsonl``,
one compact line per observation, appended - so recording is cheap and a
torn last line (a crash mid-append) costs that one line, never the file. A
catalog (``catalog.json``, written atomically) lists every entity and
attribute seen, with counts and time range, so "what do you observe?" never
has to open every file.

**Values are data, not subjects.** A value is a number, a boolean, a short
string, or a small structured value (a position with ``lat``/``lon`` or
``x``/``y``). Aggregation is decided by the values themselves: numbers get
min/max/mean/change/rate, anything else gets counts and the most frequent
value. No attribute name is special.

**Sensitivity travels with the reading (GM-13).** Each observation may carry
a sensitivity level inherited from its source; ``series``/``summarize``
callers that are not entitled to a level ask with ``max_sensitivity`` and
simply do not receive what exceeds it.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.core.sensitivity import rank as sensitivity_rank
from src.providers.connection import is_json_value

SCHEMA_VERSION = 1
# Retention by count, per entity: an entity observed every second for a year
# would otherwise grow without bound. Oldest lines go first, on compaction.
DEFAULT_MAX_PER_ENTITY = 200_000
MAX_STRING_VALUE = 200

_LOCK = threading.RLock()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def entity_key(name: str) -> str:
    """How an entity is recognised whatever its capitalisation or spacing."""
    return " ".join(str(name or "").replace("_", " ").split()).lower()


def attribute_key(name: str) -> str:
    return ".".join(part for part in " ".join(str(name or "").split()).lower().replace(" ", "_").split(".") if part)


def _file_stem(key: str) -> str:
    safe = "".join(ch if ch.isalnum() else "_" for ch in key)[:48].strip("_") or "entity"
    return f"{safe}-{hashlib.sha1(key.encode('utf-8')).hexdigest()[:8]}"


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


@dataclass(frozen=True)
class Observation:
    entity: str
    attribute: str
    value: Any
    observed_at: datetime = field(default_factory=utc_now)
    source: str = ""
    unit: str = ""
    sensitivity: str = ""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self) -> None:
        entity = " ".join(str(self.entity or "").split())
        if not entity:
            raise ValueError("an observation needs the entity it is about")
        attribute = attribute_key(self.attribute)
        if not attribute:
            raise ValueError("an observation needs the attribute it measured")
        value = self.value
        if isinstance(value, str):
            value = " ".join(value.split())[:MAX_STRING_VALUE]
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("an observation's value must be a finite number")
        if not is_json_value(value) or isinstance(value, list):
            raise ValueError("an observation's value must be a number, boolean, short text or a small object")
        if self.observed_at.tzinfo is None:
            raise ValueError("an observation's time must carry its timezone")
        object.__setattr__(self, "entity", entity)
        object.__setattr__(self, "attribute", attribute)
        object.__setattr__(self, "value", value)
        object.__setattr__(self, "observed_at", self.observed_at.astimezone(timezone.utc))
        object.__setattr__(self, "source", " ".join(str(self.source or "").split()))
        object.__setattr__(self, "unit", " ".join(str(self.unit or "").split()))
        object.__setattr__(self, "sensitivity", str(self.sensitivity or "").strip().lower())

    def to_line(self) -> dict:
        line = {"a": self.attribute, "v": self.value, "t": self.observed_at.isoformat(), "i": self.id}
        if self.source:
            line["s"] = self.source
        if self.unit:
            line["u"] = self.unit
        if self.sensitivity:
            line["x"] = self.sensitivity
        return line

    @classmethod
    def from_line(cls, entity: str, line: Mapping[str, Any]) -> "Observation":
        return cls(
            entity=entity, attribute=str(line["a"]), value=line.get("v"),
            observed_at=datetime.fromisoformat(str(line["t"])), source=str(line.get("s", "")),
            unit=str(line.get("u", "")), sensitivity=str(line.get("x", "")), id=str(line.get("i") or uuid.uuid4().hex[:12]),
        )


@dataclass(frozen=True)
class SeriesSummary:
    """What a window of one attribute's values says, decided by the values."""

    entity: str
    attribute: str
    count: int
    since: "datetime | None"
    until: "datetime | None"
    first: Any = None
    last: Any = None
    first_at: "datetime | None" = None
    last_at: "datetime | None" = None
    numeric: bool = False
    minimum: "float | None" = None
    maximum: "float | None" = None
    mean: "float | None" = None
    change: "float | None" = None
    rate_per_hour: "float | None" = None
    most_frequent: Any = None
    distinct: int = 0
    unit: str = ""

    def to_dict(self) -> dict:
        def when(value):
            return value.isoformat() if value else None
        return {
            "entity": self.entity, "attribute": self.attribute, "count": self.count,
            "since": when(self.since), "until": when(self.until), "first": self.first, "last": self.last,
            "first_at": when(self.first_at), "last_at": when(self.last_at), "numeric": self.numeric,
            "min": self.minimum, "max": self.maximum, "mean": self.mean, "change": self.change,
            "rate_per_hour": self.rate_per_hour, "most_frequent": self.most_frequent,
            "distinct": self.distinct, "unit": self.unit,
        }

    def describe(self) -> str:
        """One plain paragraph a person can read - Half Brain, no model."""
        name = f"{self.entity} {self.attribute.replace('_', ' ')}"
        if not self.count:
            return f"No observations of {name} in that window."
        unit = f" {self.unit}" if self.unit else ""
        span = ""
        if self.first_at and self.last_at and self.first_at != self.last_at:
            span = f" between {self.first_at:%Y-%m-%d %H:%M} and {self.last_at:%Y-%m-%d %H:%M} UTC"
        if self.numeric:
            parts = [
                f"{self.count} reading{'s' if self.count != 1 else ''} of {name}{span}",
                f"min {_fmt(self.minimum)}{unit}, max {_fmt(self.maximum)}{unit}, mean {_fmt(self.mean)}{unit}",
                f"latest {_fmt(self.last)}{unit}",
            ]
            if self.change is not None and self.count > 1:
                direction = "up" if self.change > 0 else "down" if self.change < 0 else "unchanged"
                parts.append(
                    f"{direction} {_fmt(abs(self.change))}{unit} since the first" if self.change else "unchanged since the first"
                )
            return "; ".join(parts) + "."
        return (
            f"{self.count} observation{'s' if self.count != 1 else ''} of {name}{span}; "
            f"latest {self.last!r}, most often {self.most_frequent!r}, {self.distinct} distinct value(s)."
        )


def _fmt(value: Any) -> str:
    if value is None:
        return "?"
    if isinstance(value, float):
        return f"{value:.4g}" if abs(value) < 1e6 else f"{value:.6g}"
    return str(value)


def summarize(entity: str, attribute: str, observations: Sequence[Observation],
              *, since: "datetime | None" = None, until: "datetime | None" = None) -> SeriesSummary:
    ordered = sorted(observations, key=lambda o: o.observed_at)
    if not ordered:
        return SeriesSummary(entity=entity, attribute=attribute, count=0, since=since, until=until)
    values = [o.value for o in ordered]
    unit = next((o.unit for o in reversed(ordered) if o.unit), "")
    base = dict(
        entity=ordered[-1].entity, attribute=attribute, count=len(ordered), since=since, until=until,
        first=values[0], last=values[-1], first_at=ordered[0].observed_at, last_at=ordered[-1].observed_at, unit=unit,
    )
    counts: dict[str, int] = {}
    for value in values:
        counts[json.dumps(value, sort_keys=True)] = counts.get(json.dumps(value, sort_keys=True), 0) + 1
    most = max(counts.items(), key=lambda kv: kv[1])[0]
    base["most_frequent"] = json.loads(most)
    base["distinct"] = len(counts)
    if all(is_number(v) for v in values):
        numbers = [float(v) for v in values]
        hours = (ordered[-1].observed_at - ordered[0].observed_at).total_seconds() / 3600.0
        change = numbers[-1] - numbers[0]
        base.update(
            numeric=True, minimum=min(numbers), maximum=max(numbers),
            mean=sum(numbers) / len(numbers), change=change,
            rate_per_hour=(change / hours) if hours > 0 else None,
        )
    return SeriesSummary(**base)


class ObservationStore:
    """Every observation, persisted under ``root/observations``."""

    def __init__(self, root: "Path | str", *, max_per_entity: int = DEFAULT_MAX_PER_ENTITY) -> None:
        self.directory = Path(root) / "observations"
        self.max_per_entity = int(max_per_entity)
        self._catalog_path = self.directory / "catalog.json"

    # -- catalog -----------------------------------------------------------

    def _load_catalog(self) -> dict:
        if not self._catalog_path.exists():
            # Derived data: absent is "rebuild from the files", never "nothing observed".
            return self._rebuild_catalog()
        try:
            catalog = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._rebuild_catalog()
        if not isinstance(catalog, dict) or not isinstance(catalog.get("entities"), dict):
            return self._rebuild_catalog()
        return catalog

    def _save_catalog(self, catalog: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self._catalog_path.with_name("catalog.json.tmp")
        temporary.write_text(json.dumps(catalog, indent=1, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self._catalog_path)

    def _rebuild_catalog(self) -> dict:
        """The catalog is derived data: rebuilt from the files if it is ever lost."""
        catalog = {"schema_version": SCHEMA_VERSION, "entities": {}}
        if not self.directory.exists():
            return catalog
        for path in sorted(self.directory.glob("*.jsonl")):
            name = None
            for line in self._read_lines(path):
                if "entity" in line and "a" not in line:
                    name = line["entity"]
                    continue
                if name is None or "a" not in line:
                    continue
                self._note(catalog, name, path.name, line)
        return catalog

    @staticmethod
    def _note(catalog: dict, entity: str, filename: str, line: Mapping[str, Any]) -> None:
        entry = catalog["entities"].setdefault(entity_key(entity), {"name": entity, "file": filename, "attributes": {}})
        attribute = entry["attributes"].setdefault(line["a"], {"count": 0, "first": line["t"], "last": line["t"], "unit": ""})
        attribute["count"] += 1
        attribute["first"] = min(attribute["first"], line["t"])
        attribute["last"] = max(attribute["last"], line["t"])
        if line.get("u"):
            attribute["unit"] = line["u"]
        if line.get("x"):
            entry["sensitivity"] = max(entry.get("sensitivity", ""), line["x"], key=sensitivity_rank)

    # -- recording ---------------------------------------------------------

    def record(self, observations: Iterable[Observation]) -> int:
        """Append observations; returns how many were recorded."""
        by_entity: dict[str, list[Observation]] = {}
        for observation in observations:
            by_entity.setdefault(entity_key(observation.entity), []).append(observation)
        if not by_entity:
            return 0
        recorded = 0
        with _LOCK:
            self.directory.mkdir(parents=True, exist_ok=True)
            catalog = self._load_catalog()
            for key, group in by_entity.items():
                entry = catalog["entities"].get(key)
                filename = entry["file"] if entry else f"{_file_stem(key)}.jsonl"
                path = self.directory / filename
                new_file = not path.exists()
                with path.open("a", encoding="utf-8") as handle:
                    if new_file:
                        handle.write(json.dumps({"schema_version": SCHEMA_VERSION, "entity": group[0].entity}) + "\n")
                    for observation in group:
                        line = observation.to_line()
                        handle.write(json.dumps(line, separators=(",", ":")) + "\n")
                        self._note(catalog, group[0].entity if not entry else entry["name"], filename, line)
                        recorded += 1
                if catalog["entities"][key]["attributes"] and sum(
                    a["count"] for a in catalog["entities"][key]["attributes"].values()
                ) > self.max_per_entity:
                    self._compact(path, catalog, key)
            self._save_catalog(catalog)
        return recorded

    def _compact(self, path: Path, catalog: dict, key: str) -> None:
        lines = [line for line in self._read_lines(path) if "a" in line]
        keep = lines[-self.max_per_entity:]
        name = catalog["entities"][key]["name"]
        temporary = path.with_name(path.name + ".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps({"schema_version": SCHEMA_VERSION, "entity": name}) + "\n")
            for line in keep:
                handle.write(json.dumps(line, separators=(",", ":")) + "\n")
        os.replace(temporary, path)
        entry = {"name": name, "file": path.name, "attributes": {}}
        catalog["entities"][key] = entry
        for line in keep:
            self._note(catalog, name, path.name, line)

    # -- reading -----------------------------------------------------------

    @staticmethod
    def _read_lines(path: Path) -> "list[dict]":
        lines: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as handle:
                for raw in handle:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                    except ValueError:
                        continue  # a torn line from an interrupted append costs itself only
                    if isinstance(parsed, dict):
                        lines.append(parsed)
        except OSError:
            return []
        return lines

    def entities(self, *, max_sensitivity: "str | None" = None) -> "list[dict]":
        """Every observed entity with its attributes, counts and time range."""
        with _LOCK:
            catalog = self._load_catalog()
        result = []
        for key, entry in sorted(catalog["entities"].items()):
            if max_sensitivity is not None and sensitivity_rank(entry.get("sensitivity", "")) > sensitivity_rank(max_sensitivity):
                continue
            result.append({"key": key, **entry})
        return result

    def resolve_entity(self, name: str) -> "str | None":
        """The recorded name of an entity called ``name`` (any case), or None."""
        key = entity_key(name)
        with _LOCK:
            entry = self._load_catalog()["entities"].get(key)
        return entry["name"] if entry else None

    def attributes(self, entity: str) -> "list[str]":
        with _LOCK:
            entry = self._load_catalog()["entities"].get(entity_key(entity))
        return sorted(entry["attributes"]) if entry else []

    def series(
        self,
        entity: str,
        attribute: "str | None" = None,
        *,
        since: "datetime | None" = None,
        until: "datetime | None" = None,
        limit: "int | None" = None,
        max_sensitivity: "str | None" = None,
    ) -> "list[Observation]":
        """Observations of ``entity`` (one attribute, or every attribute, or
        every attribute under a prefix ending in "."), oldest first."""
        with _LOCK:
            entry = self._load_catalog()["entities"].get(entity_key(entity))
            if entry is None:
                return []
            lines = self._read_lines(self.directory / entry["file"])
        wanted = attribute_key(attribute) if attribute else None
        observations = []
        for line in lines:
            if "a" not in line or "t" not in line:
                continue
            if wanted and line["a"] != wanted and not line["a"].startswith(wanted + "."):
                continue
            try:
                observation = Observation.from_line(entry["name"], line)
            except (ValueError, KeyError, TypeError):
                continue
            if since and observation.observed_at < since:
                continue
            if until and observation.observed_at > until:
                continue
            if max_sensitivity is not None and sensitivity_rank(observation.sensitivity) > sensitivity_rank(max_sensitivity):
                continue
            observations.append(observation)
        observations.sort(key=lambda o: o.observed_at)
        if limit is not None:
            observations = observations[-int(limit):]
        return observations

    def latest(self, entity: str, *, max_sensitivity: "str | None" = None) -> "dict[str, Observation]":
        """The most recent observation of every attribute of ``entity``."""
        latest: dict[str, Observation] = {}
        for observation in self.series(entity, max_sensitivity=max_sensitivity):
            latest[observation.attribute] = observation
        return latest

    def summarize(
        self,
        entity: str,
        attribute: str,
        *,
        window: "timedelta | None" = None,
        now: "datetime | None" = None,
        max_sensitivity: "str | None" = None,
    ) -> SeriesSummary:
        until = now or utc_now()
        since = until - window if window else None
        found = self.series(entity, attribute, since=since, until=until, max_sensitivity=max_sensitivity)
        exact = [o for o in found if o.attribute == attribute_key(attribute)] or found
        return summarize(entity, attribute_key(attribute), exact, since=since, until=until)

    def forget(self, entity: str, *, before: "datetime | None" = None, sensitivity: "str | None" = None) -> int:
        """Remove observations of ``entity`` (all, or those before a moment,
        or those at a sensitivity level). Returns how many were removed."""
        with _LOCK:
            catalog = self._load_catalog()
            key = entity_key(entity)
            entry = catalog["entities"].get(key)
            if entry is None:
                return 0
            path = self.directory / entry["file"]
            lines = [line for line in self._read_lines(path) if "a" in line]

            def removed(line: dict) -> bool:
                if before is not None and datetime.fromisoformat(line["t"]) >= before:
                    return False
                if sensitivity is not None and str(line.get("x", "")) != sensitivity:
                    return False
                return True

            keep = [line for line in lines if not removed(line)]
            count = len(lines) - len(keep)
            if not count:
                return 0
            if not keep:
                path.unlink(missing_ok=True)
                del catalog["entities"][key]
            else:
                temporary = path.with_name(path.name + ".tmp")
                with temporary.open("w", encoding="utf-8") as handle:
                    handle.write(json.dumps({"schema_version": SCHEMA_VERSION, "entity": entry["name"]}) + "\n")
                    for line in keep:
                        handle.write(json.dumps(line, separators=(",", ":")) + "\n")
                os.replace(temporary, path)
                catalog["entities"][key] = {"name": entry["name"], "file": entry["file"], "attributes": {}}
                for line in keep:
                    self._note(catalog, entry["name"], entry["file"], line)
            self._save_catalog(catalog)
            return count


def parse_window(text: str) -> "timedelta | None":
    """A window a person names - "the last 2 hours", "past 30 minutes",
    "today", "this week" - as a timedelta. None when none is named."""
    import re

    lowered = str(text or "").lower()
    match = re.search(
        r"\b(?:last|past|previous|over the last|in the last|for the last)\s+(?:(\d+(?:\.\d+)?|an?|one|two|three|four|five|six|twelve|twenty-four)\s+)?"
        r"(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?)\b",
        lowered,
    )
    words = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "twelve": 12, "twenty-four": 24}
    if match:
        amount_text = match.group(1)
        amount = float(words.get(amount_text, amount_text)) if amount_text else 1.0
        unit = match.group(2)
        if unit.startswith("sec"):
            return timedelta(seconds=amount)
        if unit.startswith("min"):
            return timedelta(minutes=amount)
        if unit.startswith(("hour", "hr")):
            return timedelta(hours=amount)
        if unit.startswith("day"):
            return timedelta(days=amount)
        if unit.startswith("week"):
            return timedelta(weeks=amount)
        if unit.startswith("month"):
            return timedelta(days=30 * amount)
    if re.search(r"\btoday\b|\bthis morning\b", lowered):
        return timedelta(hours=24)
    if re.search(r"\bthis week\b", lowered):
        return timedelta(days=7)
    return None


def parse_duration(text: str) -> "timedelta | None":
    """"30s", "5 min", "2h", "1 day" -> timedelta; None when unreadable."""
    import re

    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?|w|weeks?)\s*",
        str(text or "").lower(),
    )
    if not match:
        return None
    amount, unit = float(match.group(1)), match.group(2)
    if unit.startswith("s"):
        return timedelta(seconds=amount)
    if unit in ("m",) or unit.startswith("min"):
        return timedelta(minutes=amount)
    if unit.startswith("h"):
        return timedelta(hours=amount)
    if unit.startswith("d"):
        return timedelta(days=amount)
    return timedelta(weeks=amount)
