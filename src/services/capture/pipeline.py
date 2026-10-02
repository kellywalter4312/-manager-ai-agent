"""The consented capture pipeline (GM-12): capture -> understanding ->
organised memory -> day summaries -> recall, under the owner's policy.

Every capture the system already makes - a voice, screen or camera recording
a person started (T-REC-03), or media a connected device streams to a watch
(GM-04, ``frames_to="capture"``) - is an artifact. On each ecosystem tick
this pipeline takes the ones whose kind the owner set to ``automatic``
(``policy.py``) and, one at a time:

1. **hears it** through the configured hearing axis (the same transcriber
   ``transcribe_media`` uses - the local model when it is installed); a
   video's own audio track is heard, and a camera recording is also looked
   at through the perception axis (GM-03) when one is bound;
2. **redacts** every word the owner listed, and every word their
   sensitivity rules mark "restricted" (GM-13), before anything is kept;
3. **keeps the words** as a transcript document derived from the capture
   (the capture itself untouched), exactly as asking for a transcript does;
4. **organises memory**: the capture's gist, stamped with its sensitivity,
   through the owning agent's own governed ``retain`` - so recall, correct
   and forget (P11F-06/07) work on it like on anything else;
5. **summarises each finished day**, deterministically (Half Brain), into
   one memory the owner can ask about.

It never invents: no hearing engine configured is reported as exactly that,
and the capture waits. A pause stops everything. Retention forgets raw media
only after its words are kept - never the only record of what was said.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from src.services.capture.policy import CapturePolicy, redact, restricted_words
from src.services.declared_store import DeclaredDocument

logger = logging.getLogger(__name__)

CAPTURE_TYPES = ("voice_recording", "video_recording", "device_capture")
MAX_PER_TICK = 3
GIST_CHARS = 700
_STOPWORDS = frozenset(
    "this that with have from they them their there what when where which while would could should about "
    "because been being into just like more most much only other over same some such than then these those "
    "very will your yours ours were also back well even here make made many must okay yeah right know think "
    "going want really thing things said says".split()
)
_LOCK = threading.Lock()


def capture_kind(artifact: Any) -> "str | None":
    """audio | screen | camera | device, from what the capture recorded about itself."""
    if artifact.type == "voice_recording":
        return "audio"
    if artifact.type == "device_capture":
        return "device"
    if artifact.type == "video_recording":
        try:
            content = json.loads(Path(artifact.latest.content_path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            content = {}
        source = str(content.get("source") or artifact.latest.metadata.get("source") or "").lower()
        return source if source in ("screen", "camera") else "screen"
    return None


class CaptureLedger:
    """What the pipeline has done with each capture (``capture_state.json``)."""

    def __init__(self, root: "Path | str") -> None:
        self._document = DeclaredDocument(Path(root) / "capture_state.json", key="captures")

    def all(self) -> "list[dict]":
        return self._document.load()

    def get(self, artifact_id: str) -> "dict | None":
        return next((e for e in self.all() if e.get("artifact_id") == artifact_id), None)

    def put(self, entry: dict) -> None:
        def change(items: list) -> None:
            items[:] = [i for i in items if i.get("artifact_id") != entry["artifact_id"]]
            items.append(entry)
        self._document.update(change)


def _media(artifact: Any) -> "tuple[bytes, str] | None":
    for fmt, path in dict(artifact.latest.renditions).items():
        candidate = Path(str(path))
        if candidate.is_file():
            return candidate.read_bytes(), fmt
    return None


def _hear(transcriber: Any, data: bytes, fmt: str, kind: str) -> "tuple[str, float]":
    """(timestamped text, seconds of speech heard). Raises for no engine,
    no audio track, or nothing said."""
    audio = data
    if kind in ("screen", "camera") or fmt in ("mp4", "webm", "mkv", "mov") and kind != "audio":
        from src.providers.media.video import VideoError, extract_audio_wav

        try:
            audio = extract_audio_wav(data)
        except VideoError as exc:
            raise ValueError(f"no sound to hear in it ({exc})") from exc
    transcript = transcriber.transcribe(audio)
    heard = max((s.end_seconds for s in transcript.segments), default=0.0)
    return transcript.as_timestamped_text(), float(heard)


def _look(ecosystem: Any, data: bytes, fmt: str, title: str) -> str:
    provider = ecosystem.provider("perception") if ecosystem is not None else None
    if provider is None:
        return ""
    from src.services.perception.analysis import analyse, frames_from_media

    try:
        report = analyse(frames_from_media(data, f"video/{fmt}", count=16), provider, source=title, draw=False,
                         label_tracks=0)
    except Exception:  # noqa: BLE001 - looking is extra; hearing stands on its own
        return ""
    return (f"{len(report.tracks)} moving thing(s) seen, at most {report.peak} at once" if report.tracks
            else "nothing moved in view")


def _gist(text: str) -> str:
    """The first sentences said, without time stamps, up to GIST_CHARS."""
    spoken = " ".join(re.sub(r"^\s*\d+:\d{2}(?::\d{2})?\s*-\s*\d+:\d{2}(?::\d{2})?\s*:\s*", "", line).strip()
                      for line in text.splitlines() if line.strip())
    return spoken if len(spoken) <= GIST_CHARS else spoken[:GIST_CHARS].rsplit(" ", 1)[0] + " ..."


def _keywords(texts: Iterable[str], limit: int = 8) -> "list[str]":
    counts = Counter(w for text in texts for w in re.findall(r"[a-z][a-z'-]{3,}", text.lower())
                     if w not in _STOPWORDS and w != "redacted")
    return [w for w, n in counts.most_common(limit) if n >= 2] or [w for w, _n in counts.most_common(min(limit, 4))]


def _day_of(artifact: Any) -> str:
    return artifact.latest.created_at.astimezone(timezone.utc).date().isoformat()


def understand(artifact: Any, ecosystem: Any, *, now: datetime) -> dict:
    """Run one capture through hearing, redaction, keeping and memory.
    Returns its ledger entry (status understood | waiting | failed)."""
    from src.agents.tools.artifact_production import TRANSCRIPT_TITLE_PREFIX
    from src.services.artifacts.models import DocumentContent, DocumentSection
    from src.services.memory.memory import MemoryRecord

    policy = CapturePolicy(ecosystem.root).state()
    kind = capture_kind(artifact) or "audio"
    title = artifact.latest.title
    entry = {"artifact_id": artifact.artifact_id, "kind": kind, "owner": artifact.owner_agent, "title": title,
             "day": _day_of(artifact), "at": now.isoformat()}
    media = _media(artifact)
    if media is None:
        return {**entry, "status": "failed", "detail": "its media file is gone"}
    data, fmt = media
    transcriber = ecosystem.provider("transcriber")
    if transcriber is None:
        return {**entry, "status": "waiting", "detail": "no hearing engine is bound to this process"}
    try:
        text, heard = _hear(transcriber, data, fmt, kind)
    except Exception as exc:  # noqa: BLE001 - every reason is reported, never a made-up transcript
        reason = str(exc)
        waiting = "not configured" in reason.lower() or "no transcription" in reason.lower() or "null" in reason.lower()
        return {**entry, "status": "waiting" if waiting else "failed", "detail": reason[:300]}
    text, redactions = redact(text, policy.redact + restricted_words(ecosystem.sensitivity))
    seen = _look(ecosystem, data, fmt, title) if kind == "camera" else ""
    sensitivity = ecosystem.sensitivity.classify(text, source=kind)
    entry.update(status="understood", seconds=round(heard, 1), redactions=redactions, sensitivity=sensitivity,
                 words=len(text.split()), keywords=_keywords([text]), seen=seen)

    tool_for = ecosystem.provider("artifact_tool_for")
    if tool_for is not None:
        sections = []
        for index, line in enumerate(l for l in text.splitlines() if l.strip()):
            timed = re.match(r"^\s*(\d+:\d{2}(?::\d{2})?\s*-\s*\d+:\d{2}(?::\d{2})?)\s*:\s*(.+?)\s*$", line)
            heading, body = (timed.group(1), timed.group(2)) if timed else (f"passage {index + 1}", line.strip())
            sections.append(DocumentSection(heading=heading, body=body))
        if sections:
            doc = DocumentContent(title=f"{TRANSCRIPT_TITLE_PREFIX}{title}", sections=tuple(sections))
            try:
                made = tool_for(artifact.owner_agent).run(operation="derive_document",
                                                           content=f"{artifact.artifact_id}\n{doc.to_json()}")
                found = re.search(r"\bid=([0-9a-f]{12})\b", made or "")
                entry["transcript_id"] = found.group(1) if found else ""
            except Exception as exc:  # noqa: BLE001
                entry["transcript_note"] = f"the transcript document could not be kept: {exc}"
    retain_for = ecosystem.provider("retain_for")
    retain = retain_for(artifact.owner_agent) if retain_for is not None else None
    if retain is None:
        entry["memory"] = "waiting: no agent memory is reachable from this process"
    else:
        gist = _gist(text) or "(nothing intelligible was said)"
        when = artifact.latest.created_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
        extra = f" Seen: {seen}." if seen else ""
        record = MemoryRecord(
            content=f"Heard in {title} ({when} UTC): {gist}{extra}"[:990],
            kind="recording",
            tags={"capture", f"capture:{artifact.artifact_id}", f"day:{entry['day']}", kind},
            context={"fact_type": "capture_understanding", "artifact_id": artifact.artifact_id,
                     "transcript_artifact_id": entry.get("transcript_id", ""), "day": entry["day"],
                     "capture_kind": kind, "sensitivity": sensitivity, "confidence": 1.0,
                     "transcription_status": "complete", "created_by": artifact.owner_agent},
        )
        verdict = retain(record)
        entry["memory"] = "kept" if getattr(verdict, "allowed", False) else f"refused: {getattr(verdict, 'reason', '')}"
    return entry


def summarise_day(day: str, entries: "list[dict]", transcripts: "Mapping[str, str]") -> str:
    """One day's captures in a few plain lines - Half Brain, no model."""
    understood = [e for e in entries if e.get("status") == "understood"]
    seconds = sum(float(e.get("seconds") or 0) for e in understood)
    kinds = Counter(e.get("kind") for e in understood)
    words = _keywords(transcripts.get(e["artifact_id"], " ".join(e.get("keywords") or ())) for e in understood)
    lines = [f"{day}: {len(understood)} capture(s) understood ({', '.join(f'{n} {k}' for k, n in kinds.items())}), "
             f"about {seconds / 60:.1f} minute(s) of speech."]
    if words:
        lines.append("Talked about: " + ", ".join(words) + ".")
    for entry in understood[:6]:
        lines.append(f"- {entry['title']}: " + ", ".join(entry.get("keywords") or ()) + (f"; {entry['seen']}" if entry.get("seen") else ""))
    return "\n".join(lines)[:990]


def _forget_raw(ecosystem: Any, artifacts: Any, ledger: CaptureLedger, policy, now: datetime) -> "list[str]":
    """Retention: raw media of a kind older than its days, only once its
    words are kept. Only files inside the artifact store are touched."""
    done = []
    root = Path(getattr(artifacts, "_root", "")).resolve() if getattr(artifacts, "_root", None) else None
    for entry in ledger.all():
        days = policy.retention_days.get(entry.get("kind", ""))
        if not days or entry.get("status") != "understood" or entry.get("raw_forgotten"):
            continue
        artifact = artifacts.get(entry["artifact_id"])
        if artifact is None or now - artifact.latest.created_at < timedelta(days=days):
            continue
        removed = 0
        for path in dict(artifact.latest.renditions).values():
            candidate = Path(str(path)).resolve()
            if root is not None and root not in candidate.parents:
                continue
            if candidate.is_file():
                candidate.unlink()
                removed += 1
        ledger.put({**entry, "raw_forgotten": now.isoformat(), "raw_files_removed": removed})
        done.append(entry["artifact_id"])
    return done


def capture_step(ecosystem: Any, connections: Any, now: datetime) -> "str | None":
    """The tick step: understand what consent allows, summarise finished
    days, apply retention. Returns a line for the tick report, or None."""
    artifacts = ecosystem.provider("artifacts")
    if artifacts is None:
        return None
    policy = CapturePolicy(ecosystem.root).state()
    if policy.paused(now):
        return None
    if not _LOCK.acquire(blocking=False):
        return None
    try:
        ledger = CaptureLedger(ecosystem.root)
        known = {e["artifact_id"]: e for e in ledger.all()}
        lines = []
        due = [a for a in artifacts.all() if a.type in CAPTURE_TYPES
               and policy.consent_for(capture_kind(a) or "audio") == "automatic"
               and known.get(a.artifact_id, {}).get("status") in (None, "waiting")]
        for artifact in sorted(due, key=lambda a: a.latest.created_at)[:MAX_PER_TICK]:
            entry = understand(artifact, ecosystem, now=now)
            ledger.put(entry)
            lines.append(f"{artifact.artifact_id} {entry['status']}"
                         + (f" ({entry.get('detail')})" if entry.get("detail") else ""))
        lines += [f"day {d} summarised" for d in _summarise_finished_days(ecosystem, ledger, now)]
        forgotten = _forget_raw(ecosystem, artifacts, ledger, policy, now)
        if forgotten:
            lines.append(f"raw media forgotten by retention: {', '.join(forgotten)}")
        return ("capture: " + "; ".join(lines)) if lines else None
    finally:
        _LOCK.release()


def _summarise_finished_days(ecosystem: Any, ledger: CaptureLedger, now: datetime) -> "list[str]":
    from src.services.memory.memory import MemoryRecord

    entries = ledger.all()
    summaries = DeclaredDocument(Path(ecosystem.root) / "day_summaries.json", key="days")
    done_days = {d.get("day") for d in summaries.load()}
    today = now.astimezone(timezone.utc).date().isoformat()
    made = []
    by_day: "dict[str, list[dict]]" = {}
    for entry in entries:
        if entry.get("status") == "understood":
            by_day.setdefault(entry["day"], []).append(entry)
    retain_for = ecosystem.provider("retain_for")
    for day, day_entries in sorted(by_day.items()):
        if day >= today or day in done_days:
            continue
        text = summarise_day(day, day_entries, {})
        owners = sorted({e.get("owner", "") for e in day_entries if e.get("owner")})
        kept = []
        for owner in owners:
            retain = retain_for(owner) if retain_for is not None else None
            if retain is None:
                continue
            verdict = retain(MemoryRecord(content=text, kind="recording", tags={"day_summary", f"day:{day}"},
                                          context={"fact_type": "day_summary", "day": day, "confidence": 1.0,
                                                   "created_by": owner}))
            if getattr(verdict, "allowed", False):
                kept.append(owner)
        if kept or retain_for is None:
            summaries.update(lambda items, d=day, t=text, k=kept: items.append(
                {"day": d, "text": t, "kept_by": k, "at": now.isoformat()}))
            made.append(day)
    return made


def day_summaries(root: "Path | str") -> "list[dict]":
    return DeclaredDocument(Path(root) / "day_summaries.json", key="days").load()


def capture_frames(watch: Any, ecosystem: Any, items: list) -> "str | None":
    """GM-04 -> GM-12: media a device streams (``frames_to="capture"``)
    becomes a capture artifact of kind "device" - only with the owner's
    consent for device capture, and never while paused."""
    policy = CapturePolicy(ecosystem.root).state()
    if policy.paused():
        return f"{len(items)} item(s) not captured - capture is paused"
    if policy.consent_for("device") == "off":
        return f"{len(items)} item(s) not captured - device capture is off"
    artifacts = ecosystem.provider("artifacts")
    if artifacts is None:
        return "no artifact store is bound - nothing captured"
    from src.services.artifacts.registration import register_new_artifact

    pieces = [i for i in items if getattr(i, "payload", None)]
    if not pieces:
        return None
    kind = str(pieces[0].content_type or "application/octet-stream").split(";")[0]
    extension = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "audio/ogg": "ogg",
                 "audio/mpeg": "mp3", "video/mp4": "mp4", "video/webm": "webm"}.get(kind, "bin")
    data = b"".join(i.payload for i in pieces) if extension != "wav" else pieces[-1].payload
    content = json.dumps({"source": "device", "watch": watch.watch_id, "entity": watch.entity}).encode("utf-8")
    artifact = register_new_artifact(
        artifacts, type="device_capture", owner_agent=ecosystem.provider("capture_owner") or "Alfred",
        title=f"{watch.entity} capture", content_bytes=content, rendition_format=extension, rendition_bytes=data,
        metadata={"content_kind": "audio" if kind.startswith("audio/") else "video", "media_type": kind,
                  "source": "device"},
    )
    return f"captured {len(pieces)} item(s) from {watch.entity} as {artifact.artifact_id}"
