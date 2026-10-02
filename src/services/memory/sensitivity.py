"""Sensitivity of personal records and sources (GM-13) - how carefully
something is handled, declared by the owner and the person, enforced where
information is written and wherever it would leave the agent that holds it.

``src/core/sensitivity.py`` holds the scale (ordinary < personal < sensitive <
restricted). This module is the policy over it:

**Declared, never decided by topic.** Which information is sensitive is the
owner's rules (``SensitivityRule``: a word or phrase, a source - a connection,
a capture - or a memory kind, each mapped to a level) and the person's own
words at the moment ("remember privately ...", "this is confidential ...").
Nothing here knows that health, money or anything else is delicate; an owner
who says it is makes it so, and the same mechanism covers whatever they decide
next.

**Enforced at the few doors information passes through**, so no capability can
forget:

    written       ManagerAgent.retain stamps the record (``stamp``)
    recalled      capabilities get a recall gated to a ceiling (``gated_recall``):
                  "sensitive" when reasoning is local or absent, the owner's
                  cloud allowance (default "personal") when a vendor's model
                  could receive it; "restricted" only for a direct question
    shared        a note carried to another agent is screened
                  (``src/core/shared_context.py``) and withheld above "personal"
    shown         management surfaces show a sensitive record redacted
    kept          records and observations past their level's retention are
                  forgotten by the ecosystem tick, and the purge is recorded

Credentials remain above the scale: ``secrets.py`` refuses to store them at all.
"""

from __future__ import annotations

import contextvars
import re
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from src.core.sensitivity import LEVELS, normalise_level, rank
from src.services.declared_store import DeclaredDocument

RULE_KINDS = ("word", "source", "kind")
DEFAULT_CLOUD_CEILING = "personal"
LOCAL_CEILING = "sensitive"
LOCAL_PROVIDERS = frozenset({"", "null", "none", "local"})

# The level the person asked for in the request being handled right now -
# set around a request by the Manager's own execution, read by the one write
# path. A context variable, so concurrent requests on a live server never
# see each other's words.
_REQUESTED: "contextvars.ContextVar[str]" = contextvars.ContextVar("requested_sensitivity", default="")

# Phrases that say how to *keep* what is being said - never the bare words
# "sensitive" or "private", which a person also uses to talk *about* the rules
# ("what do you treat as sensitive?").
_EXPLICIT = (
    ("restricted", re.compile(
        r"(?:\b(?:this is|it's|it is)\s+restricted\b|\bonly (?:tell|show) me (?:this )?if i ask\b"
        r"|\bnever (?:mention|bring up) (?:this|it) unless i ask\b)", re.I)),
    ("sensitive", re.compile(
        r"(?:\bin confidence\b|\bconfidentially\b|\b(?:this is|it's|it is)\s+(?:confidential|sensitive)\b"
        r"|\bdon'?t (?:share|tell) (?:this|it|anyone)\b|\bkeep (?:this|it) (?:secret|confidential|to yourself)\b)", re.I)),
    ("personal", re.compile(
        r"(?:\bprivately\b|\b(?:this is|it's|it is)\s+private\b|\bkeep (?:this|it) private\b"
        r"|\bjust between us\b|\bbetween you and me\b)", re.I)),
)


def requested_level(text: str) -> str:
    """The level the person's own words ask for ("remember privately ...",
    "this is confidential: ...") - "" when they ask for none."""
    for level, pattern in _EXPLICIT:
        if pattern.search(str(text or "")):
            return level
    return ""


def without_markers(text: str) -> str:
    """``text`` with its handling markers removed - "remember privately that
    X" is "remember that X" said carefully, so every capability recognises
    what is being asked exactly as if the marker were not there."""
    cleaned = str(text or "")
    for _level, pattern in _EXPLICIT:
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\s+([,.;:!?])", r"\1", " ".join(cleaned.split()))
    return cleaned.strip(" ,;:-") or str(text or "")


class requested:  # noqa: N801 - used as a context manager
    """``with requested(level): ...`` - the level asked for while a request is handled."""

    def __init__(self, level: str) -> None:
        self._level = level
        self._token = None

    def __enter__(self):
        self._token = _REQUESTED.set(self._level)
        return self

    def __exit__(self, *exc):
        _REQUESTED.reset(self._token)
        return False


def current_request_level() -> str:
    return _REQUESTED.get()


def level_of(record: Any) -> str:
    context = getattr(record, "context", None) or {}
    return str(context.get("sensitivity", "") or "")


def is_cloud_provider(provider_name: str) -> bool:
    return str(provider_name or "").strip().lower() not in LOCAL_PROVIDERS


@dataclass(frozen=True)
class SensitivityRule:
    rule_id: str
    kind: str
    match: str
    level: str
    declared_by: str = ""
    declared_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in RULE_KINDS:
            raise ValueError(f"a sensitivity rule matches a {', '.join(RULE_KINDS)} - not '{self.kind}'")
        match = " ".join(str(self.match or "").split()).lower()
        if len(match) < 2:
            raise ValueError("a sensitivity rule needs something to match")
        level = normalise_level(self.level)
        if not level:
            raise ValueError("a rule that marks something ordinary changes nothing")
        object.__setattr__(self, "match", match)
        object.__setattr__(self, "level", level)

    def describe(self) -> str:
        what = {"word": f"anything mentioning '{self.match}'", "source": f"everything from '{self.match}'",
                "kind": f"every '{self.match}' record"}[self.kind]
        return f"{what} is {self.level}"

    def to_dict(self) -> dict:
        return {"rule_id": self.rule_id, "kind": self.kind, "match": self.match, "level": self.level,
                "declared_by": self.declared_by, "declared_at": self.declared_at}


class SensitivityPolicy:
    """The owner's declared rules, retention and cloud allowance
    (``sensitivity.json`` under the ecosystem root)."""

    def __init__(self, root: "Path | str") -> None:
        self._rules = DeclaredDocument(Path(root) / "sensitivity.json", key="rules")
        self._settings = DeclaredDocument(Path(root) / "sensitivity_settings.json", key="settings")

    # -- rules -------------------------------------------------------------

    def rules(self) -> "list[SensitivityRule]":
        found = []
        for item in self._rules.load():
            try:
                found.append(SensitivityRule(**{k: item.get(k, "") for k in
                                                ("rule_id", "kind", "match", "level", "declared_by", "declared_at")}))
            except (TypeError, ValueError):
                continue
        return found

    def declare(self, kind: str, match: str, level: str, *, declared_by: str = "") -> SensitivityRule:
        rule = SensitivityRule(uuid.uuid4().hex[:8], kind, match, level, declared_by,
                               datetime.now(timezone.utc).isoformat())

        def change(items: list) -> SensitivityRule:
            items[:] = [i for i in items if not (i.get("kind") == rule.kind and i.get("match") == rule.match)]
            items.append(rule.to_dict())
            return rule
        return self._rules.update(change)

    def remove(self, rule_id_or_match: str) -> bool:
        wanted = " ".join(str(rule_id_or_match).split()).lower()

        def change(items: list) -> bool:
            before = len(items)
            items[:] = [i for i in items if i.get("rule_id") != wanted and i.get("match") != wanted]
            return len(items) < before
        return self._rules.update(change)

    # -- settings: retention and cloud allowance ---------------------------

    def _setting(self, key: str, default: Any) -> Any:
        for item in self._settings.load():
            if item.get("key") == key:
                return item.get("value", default)
        return default

    def _set(self, key: str, value: Any) -> None:
        def change(items: list) -> None:
            items[:] = [i for i in items if i.get("key") != key]
            if value is not None:
                items.append({"key": key, "value": value})
        self._settings.update(change)

    def retention(self) -> "dict[str, int]":
        """Days each level is kept (a level absent is kept until forgotten)."""
        raw = self._setting("retention", {}) or {}
        return {normalise_level(k): int(v) for k, v in raw.items() if normalise_level(k) and int(v) > 0}

    def set_retention(self, level: str, days: "int | None") -> None:
        level = normalise_level(level)
        if not level:
            raise ValueError("retention applies to a sensitivity level above ordinary")
        current = {k: v for k, v in self.retention().items()}
        if days is None or int(days) <= 0:
            current.pop(level, None)
        else:
            current[level] = int(days)
        self._set("retention", current)

    def cloud_ceiling(self) -> str:
        """The highest level a vendor's (cloud) reasoning provider may receive."""
        return normalise_level(self._setting("cloud_ceiling", DEFAULT_CLOUD_CEILING)) or "ordinary"

    def set_cloud_ceiling(self, level: str) -> None:
        normalise_level(level)  # validates
        self._set("cloud_ceiling", level)

    def reasoning_ceiling(self, provider_name: str) -> str:
        return self.cloud_ceiling() if is_cloud_provider(provider_name) else LOCAL_CEILING

    # -- classification ----------------------------------------------------

    def classify(self, text: str = "", *, source: str = "", kind: str = "", explicit: str = "") -> str:
        """The most careful level any rule, or the person, asks for."""
        level = normalise_level(explicit) if explicit else ""
        lowered = " ".join(str(text or "").lower().split())
        source_key = " ".join(str(source or "").lower().replace("_", " ").split())
        kind_key = " ".join(str(kind or "").lower().split())
        for rule in self.rules():
            hit = False
            if rule.kind == "word":
                hit = bool(re.search(r"(?<![a-z0-9])" + re.escape(rule.match) + r"(?![a-z0-9])", lowered))
            elif rule.kind == "source":
                hit = bool(source_key) and (rule.match.replace("_", " ") in source_key)
            elif rule.kind == "kind":
                hit = kind_key == rule.match
            if hit and rank(rule.level) > rank(level):
                level = rule.level
        return level

    def stamp(self, record: Any, *, source: str = "", explicit: str = "") -> Any:
        """``record`` carrying its level in its context (unchanged when
        ordinary, and never lowered below a level it already carries)."""
        wanted = self.classify(getattr(record, "content", ""), source=source or getattr(record, "source", ""),
                               kind=getattr(record, "kind", ""), explicit=explicit or current_request_level())
        existing = level_of(record)
        level = wanted if rank(wanted) > rank(existing) else existing
        if not level or level == existing:
            return record
        context = dict(getattr(record, "context", None) or {})
        context["sensitivity"] = level
        return replace(record, context=context)


def gated_recall(recall: Callable[..., Any], *, ceiling: "str | Callable[[], str]") -> Callable[..., Any]:
    """``recall`` that returns only records at or below a ceiling - the
    default for every indirect use - and accepts ``max_sensitivity=`` for the
    direct question that may see more."""

    def gate(query: str = "", *, kind: "str | None" = None, tags: Iterable[str] = (), limit: int = 10,
             max_sensitivity: "str | None" = None) -> tuple:
        cap = max_sensitivity if max_sensitivity is not None else (ceiling() if callable(ceiling) else ceiling)
        found = recall(query, kind=kind, tags=tags, limit=max(limit * 3, limit + 20))
        allowed = tuple(r for r in found if rank(level_of(r)) <= rank(cap))
        return allowed[:limit]

    gate.gated = True  # type: ignore[attr-defined]
    return gate


def redact(content: str, level: str) -> str:
    """How a management surface shows a record above "personal"."""
    return f"[{level} - hidden; ask directly to see it]" if rank(level) >= rank("sensitive") else content


def retention_step(policy: SensitivityPolicy, memories: "Callable[[], Iterable[Any]]", observations: Any,
                   now: datetime) -> "list[str]":
    """Forget what has outlived its level's retention - memory records and
    observations alike. Returns one line per purge (the tick reports them)."""
    lines = []
    for level, days in policy.retention().items():
        cutoff = now - timedelta(days=days)
        removed = 0
        for memory in memories() or ():
            try:
                records = memory.recall(limit=100_000)
            except Exception:  # noqa: BLE001
                continue
            for record in records:
                if level_of(record) == level and record.created_at < cutoff and memory.forget(record.id):
                    removed += 1
        observed = 0
        if observations is not None:
            for entity in observations.entities():
                if rank(entity.get("sensitivity", "")) >= rank(level):
                    observed += observations.forget(entity["name"], before=cutoff, sensitivity=level)
        if removed or observed:
            lines.append(f"{level}: forgot {removed} record(s) and {observed} observation(s) older than {days} day(s)")
    return lines


def levels() -> "tuple[str, ...]":
    return LEVELS
