"""The owner's capture policy (GM-12): what may be captured, what is done
with it on its own, for how long raw media is kept, and what is never kept
in words.

**Consent is per kind of capture** - ``audio``, ``screen``, ``camera``, and
``device`` (media a connected device streams, GM-04) - and has three states:

    off        nothing of this kind is accepted; a capture offered is refused
    ask        captured only when a person starts it, and understood only
               when a person asks (how recording always worked)
    automatic  captured when started or streamed, and understood on its own:
               transcribed, kept as a document, organised into memory, and
               summarised by day

The default is ``ask`` for every kind: nothing becomes automatic unless the
owner says so. A **pause** stops everything - accepting and understanding
alike - until a moment the owner chose. **Retention** forgets the raw media
of a kind after N days (the transcript and what memory holds stay, under
GM-13's own retention). **Redaction** words are replaced in every
transcript and memory the pipeline writes, together with every word the
owner's sensitivity rules mark "restricted" (GM-13).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.services.declared_store import DeclaredDocument

CAPTURE_KINDS = ("audio", "screen", "camera", "device")
CONSENT_STATES = ("off", "ask", "automatic")
DEFAULT_CONSENT = "ask"
REDACTED = "[redacted]"


@dataclass(frozen=True)
class CapturePolicyState:
    consent: "Mapping[str, str]"
    paused_until: str
    retention_days: "Mapping[str, int]"
    redact: "tuple[str, ...]"

    def consent_for(self, kind: str) -> str:
        return self.consent.get(kind, DEFAULT_CONSENT)

    def paused(self, now: "datetime | None" = None) -> bool:
        if not self.paused_until:
            return False
        try:
            until = datetime.fromisoformat(self.paused_until)
        except ValueError:
            return False
        return (now or datetime.now(timezone.utc)) < until

    def describe(self) -> str:
        consent = ", ".join(f"{k} {self.consent_for(k)}" for k in CAPTURE_KINDS)
        pause = f"paused until {self.paused_until[:16].replace('T', ' ')} UTC" if self.paused() else "not paused"
        keep = ", ".join(f"{k} {d} day(s)" for k, d in self.retention_days.items()) or "raw media kept until removed"
        words = ", ".join(self.redact) or "none"
        return f"consent: {consent}; {pause}; retention: {keep}; redacted words: {words}"

    def to_dict(self) -> dict:
        return {"consent": {k: self.consent_for(k) for k in CAPTURE_KINDS}, "paused_until": self.paused_until,
                "paused": self.paused(), "retention_days": dict(self.retention_days), "redact": list(self.redact)}


class CapturePolicy:
    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "capture_policy.json", key="policy")

    def _raw(self) -> dict:
        items = self._document.load()
        return dict(items[0]) if items else {}

    def _write(self, change) -> None:
        def update(items: list) -> None:
            current = dict(items[0]) if items else {}
            change(current)
            items[:] = [current]
        self._document.update(update)

    def state(self) -> CapturePolicyState:
        raw = self._raw()
        consent = {k: v for k, v in dict(raw.get("consent") or {}).items() if k in CAPTURE_KINDS and v in CONSENT_STATES}
        retention = {k: int(v) for k, v in dict(raw.get("retention_days") or {}).items() if k in CAPTURE_KINDS and int(v) > 0}
        return CapturePolicyState(consent, str(raw.get("paused_until") or ""), retention,
                                  tuple(str(w) for w in raw.get("redact") or () if str(w).strip()))

    def set_consent(self, kind: str, state: str) -> CapturePolicyState:
        kind, state = kind.strip().lower(), state.strip().lower()
        if kind not in CAPTURE_KINDS and kind != "all":
            raise ValueError(f"a capture kind is one of {', '.join(CAPTURE_KINDS)} (or all) - not '{kind}'")
        if state not in CONSENT_STATES:
            raise ValueError(f"consent is one of {', '.join(CONSENT_STATES)} - not '{state}'")
        kinds = CAPTURE_KINDS if kind == "all" else (kind,)

        def change(raw: dict) -> None:
            consent = dict(raw.get("consent") or {})
            consent.update({k: state for k in kinds})
            raw["consent"] = consent
        self._write(change)
        return self.state()

    def pause(self, until: "datetime | None") -> CapturePolicyState:
        self._write(lambda raw: raw.__setitem__("paused_until", until.isoformat() if until else ""))
        return self.state()

    def set_retention(self, kind: str, days: "int | None") -> CapturePolicyState:
        if kind not in CAPTURE_KINDS:
            raise ValueError(f"a capture kind is one of {', '.join(CAPTURE_KINDS)} - not '{kind}'")

        def change(raw: dict) -> None:
            retention = dict(raw.get("retention_days") or {})
            if days:
                retention[kind] = int(days)
            else:
                retention.pop(kind, None)
            raw["retention_days"] = retention
        self._write(change)
        return self.state()

    def redact_word(self, word: str, *, remove: bool = False) -> CapturePolicyState:
        word = " ".join(str(word or "").split())
        if not word:
            raise ValueError("name the word or phrase")

        def change(raw: dict) -> None:
            words = [w for w in raw.get("redact") or () if w.lower() != word.lower()]
            if not remove:
                words.append(word)
            raw["redact"] = words
        self._write(change)
        return self.state()


def redact(text: str, words: "tuple[str, ...]") -> "tuple[str, int]":
    """``text`` with every whole-word occurrence of ``words`` replaced
    (case ignored), and how many were."""
    import re

    count = 0
    for word in sorted(set(words), key=len, reverse=True):
        pattern = re.compile(r"(?<![\w])" + re.escape(word) + r"(?![\w])", re.IGNORECASE)
        text, n = pattern.subn(REDACTED, text)
        count += n
    return text, count


def restricted_words(sensitivity: Any) -> "tuple[str, ...]":
    """Every word or phrase the owner's sensitivity rules (GM-13) mark
    "restricted" - never written into anything the pipeline keeps."""
    try:
        return tuple(r.match for r in sensitivity.rules() if r.kind == "word" and r.level == "restricted")
    except Exception:  # noqa: BLE001 - no rules is no extra redaction, never a failure
        return ()
