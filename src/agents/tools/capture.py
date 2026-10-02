"""The governed boundary to the consented capture pipeline (GM-12): the
owner's capture policy (consent per kind, pause, retention, redaction), what
the pipeline has done with each capture, running it now, and day summaries.

Changing consent, retention or redaction is the owner's to decide: the
capability that reaches this tool is Manager-only, and every change is a
governed, audited write.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from src.agents.tools.tool import Tool, ToolSpec
from src.services.capture.pipeline import CaptureLedger, capture_step, day_summaries, summarise_day
from src.services.capture.policy import CapturePolicy

CAPTURE_TOOL = "capture"
_OPERATIONS = ("policy", "consent", "pause", "resume", "retention", "redact", "status", "process", "summaries", "day")


class CaptureTool(Tool):
    def __init__(self, ecosystem: Any, *, owner_agent: str) -> None:
        self._ecosystem = ecosystem
        self._owner_agent = owner_agent

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=CAPTURE_TOOL,
            description=("The owner's capture policy - what may be captured, what is understood on its own, pauses, "
                         "retention and redaction - what has been understood, and summaries of each day."),
            schema={"operation": str},
            optional={"content": str},
        )

    def run(self, **arguments: str) -> str:
        operation = str(arguments.get("operation") or "").strip().lower()
        raw = str(arguments.get("content") or "").strip()
        content = json.loads(raw) if raw else {}
        if operation not in _OPERATIONS:
            raise ValueError(f"unknown capture operation {operation!r}; expected one of {', '.join(_OPERATIONS)}")
        policy = CapturePolicy(self._ecosystem.root)
        if operation == "policy":
            state = policy.state()
            return json.dumps({"policy": state.to_dict(), "text": state.describe()})
        if operation == "consent":
            state = policy.set_consent(str(content.get("kind") or ""), str(content.get("state") or ""))
            return json.dumps({"policy": state.to_dict(), "text": state.describe()})
        if operation == "pause":
            minutes = float(content.get("minutes") or 0)
            until = datetime.now(timezone.utc) + timedelta(minutes=minutes) if minutes > 0 else None
            if until is None:
                raise ValueError("say for how long to pause")
            state = policy.pause(until)
            return json.dumps({"policy": state.to_dict(), "text": state.describe(), "until": until.isoformat()})
        if operation == "resume":
            state = policy.pause(None)
            return json.dumps({"policy": state.to_dict(), "text": state.describe()})
        if operation == "retention":
            days = content.get("days")
            state = policy.set_retention(str(content.get("kind") or ""), int(days) if days not in (None, "", "off", 0) else None)
            return json.dumps({"policy": state.to_dict(), "text": state.describe()})
        if operation == "redact":
            state = policy.redact_word(str(content.get("word") or ""), remove=bool(content.get("remove")))
            return json.dumps({"policy": state.to_dict(), "text": state.describe()})
        ledger = CaptureLedger(self._ecosystem.root)
        if operation == "status":
            return json.dumps({"captures": ledger.all()[-int(content.get("limit") or 30):]})
        if operation == "process":
            return json.dumps({"report": capture_step(self._ecosystem, None, datetime.now(timezone.utc)) or "nothing to do"})
        if operation == "summaries":
            return json.dumps({"days": day_summaries(self._ecosystem.root)})
        day = str(content.get("day") or datetime.now(timezone.utc).date().isoformat())
        entries = [e for e in ledger.all() if e.get("day") == day]
        kept = next((d for d in day_summaries(self._ecosystem.root) if d.get("day") == day), None)
        return json.dumps({"day": day, "captures": len(entries),
                           "text": kept["text"] if kept else (summarise_day(day, entries, {}) if entries else ""),
                           "kept": kept is not None})
