"""Recording Session - the one live, in-process record of where a real,
user-directed audio recording currently stands for one agent, parallel to
(and modeled directly on) ``PresenceRegistry`` (``presence.py``) and
``ActivitySessions`` (``src/manager/activity_session.py``).

    Agent
    +-- ... (Brain, Memory, Identity, Personality, VoiceProfile,
    |        Capabilities, Connections, AgentPresence - unchanged)
    +-- RecordingSession (this module) - what a real recording, if any,
          is doing for this agent RIGHT NOW

**Why this is not Memory.** A recording *in progress* is exactly the kind
of "true for the length of one interaction, meaningless once it ends"
state ``ActivitySessions``' own docstring already describes for a pending
clarification - it does not belong in Memory, and putting it there would
be the same "widen the allow-list to admit scratch state" mistake that
module already refuses. Once a recording is genuinely finished and saved,
what is worth keeping - that it exists, its purpose, a reference to the
real artifact - crosses into real, governed Memory through the ordinary
``retain()`` path (see ``src/services/memory/audio_recording.py`` and the
``AUDIO_RECORDING`` category), exactly as that module's own docstring
describes for a resolved activity. This registry is where the fact lives
*before* that, never instead of it.

**Why this is not a second Brain and not a second artifact store.**
Nothing here decides whether a recording is a good idea, transcribes
it, or holds a single byte of audio. It remembers one small, transient
fact per agent - what a real recording is currently doing - the identical
"evidence, held only as long as it stays true" role ``PresenceRegistry``
already plays for SPEAKING/LISTENING.

**Why capture itself cannot live here.** Python has no route to a
browser's microphone. The actual ``getUserMedia``/``MediaRecorder`` call
is necessarily a frontend concern; this registry is the one shared place
a natural-language capability (``start_recording``/``stop_recording`` in
``src/manager/capabilities.py``) and the frontend's own upload route
(``scripts/serve_ui.py``) both read and write, so a voice-issued "stop
recording" and a clicked Stop button are read from the exact same state
by whichever side of the browser boundary acts on it next.

**Agent-scoped by construction, never Alfred-specific.** Keyed by
``agent_key`` exactly as ``PresenceRegistry``/``ActivitySessions`` already
are - there is no route by which one agent's recording session could be
read or mutated under another agent's key, and no agent name appears
anywhere in this module.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class RecordingState(str, Enum):
    """Where a real recording currently stands - the lifecycle Alfred's
    Real Audio Recording checkpoint requires, expressed as states rather
    than booleans so a screen can render one of these directly instead of
    reconstructing it from several flags.

        REQUESTED       an explicit "start recording [for X]" was heard;
                         no capture has started yet - a real click (or
                         voice-confirmed gesture) is still needed before
                         a browser will grant microphone access
        RECORDING       the frontend has confirmed genuine capture is
                         under way
        STOP_REQUESTED  "stop recording" was heard while RECORDING; the
                         frontend has not yet acted on it
        SAVING          the frontend is uploading the captured audio
        SAVED           a real artifact now exists - ``artifact_id`` names
                         it
        FAILED          capture, upload or persistence genuinely failed -
                         ``error`` says why, never fabricated
        CANCELLED       the person discarded the recording before saving
    """

    REQUESTED = "requested"
    RECORDING = "recording"
    STOP_REQUESTED = "stop_requested"
    SAVING = "saving"
    SAVED = "saved"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class RecordingSession:
    """One agent's own current recording session - a snapshot, not a
    handle. Frozen like every other contract in this project: a caller
    that wants a new state calls a registry method, which produces (and
    stores) a new one rather than mutating this in place.

    ``kind`` names which real capture this is - ``"audio"`` (a
    microphone), ``"screen"`` (``getDisplayMedia``) or ``"camera"``
    (a video-capable ``getUserMedia``) - T-REC-03's own addition. The
    lifecycle above is identical for all three: Python still never
    touches any of them, and this registry still only remembers which
    one is in progress. Defaults to ``"audio"`` so every caller written
    before this field existed keeps working unchanged.
    """

    agent_key: str
    state: RecordingState
    purpose: str = "general"
    requested_at: str = ""
    artifact_id: "str | None" = None
    error: str = ""
    kind: str = "audio"

    def to_dict(self) -> dict[str, object]:
        return {
            "agent_key": self.agent_key,
            "state": self.state.value,
            "purpose": self.purpose,
            "requested_at": self.requested_at,
            "artifact_id": self.artifact_id,
            "error": self.error,
            "kind": self.kind,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class RecordingSessionRegistry:
    """Genuinely in-process and genuinely momentary - the identical
    discipline ``PresenceRegistry`` already holds itself to. There is no
    persistence and no expiry timer: a session nobody has ever requested
    simply is not here (``get`` returns ``None``), which is what lets a
    fresh READY state be the honest default with nothing written.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, RecordingSession] = {}

    def get(self, agent_key: str) -> "RecordingSession | None":
        with self._lock:
            return self._sessions.get(str(agent_key))

    def request(self, agent_key: str, purpose: str, kind: str = "audio") -> RecordingSession:
        """A real "start recording [for X]" was heard. Replaces whatever
        session (if any) already existed for this agent - a fresh request
        always supersedes a stale prior one, the same "the newest real
        evidence wins" rule this project's other live signals already
        follow."""
        session = RecordingSession(
            agent_key=str(agent_key), state=RecordingState.REQUESTED,
            purpose=purpose, requested_at=_now_iso(), kind=str(kind or "audio"),
        )
        with self._lock:
            self._sessions[str(agent_key)] = session
        return session

    def mark_recording(
        self, agent_key: str, purpose: "str | None" = None, kind: "str | None" = None,
    ) -> RecordingSession:
        """The frontend confirms genuine capture has actually begun -
        the real user gesture only a browser can make. Works whether or
        not a natural-language "start recording" preceded it: a person
        is equally entitled to just press the real Start control with
        no prior request, and this must not refuse that. When a session
        was already REQUESTED (with its own purpose/kind), that purpose/
        kind is kept unless the caller explicitly overrides it; when
        nothing was requested, a fresh RECORDING session is opened
        directly with whatever the frontend's own real
        getUserMedia/getDisplayMedia call actually used.
        """
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            resolved_purpose = purpose or (existing.purpose if existing is not None else "general")
            resolved_kind = kind or (existing.kind if existing is not None else "audio")
            updated = RecordingSession(
                agent_key=key, state=RecordingState.RECORDING,
                purpose=resolved_purpose, kind=str(resolved_kind),
                requested_at=existing.requested_at if existing is not None else _now_iso(),
            )
            self._sessions[key] = updated
            return updated

    def request_stop(self, agent_key: str) -> bool:
        """A real "stop recording" was heard. Returns False, changing
        nothing, when this agent is not genuinely RECORDING right now -
        an honest refusal rather than inventing a session to stop."""
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            if existing is None or existing.state != RecordingState.RECORDING:
                return False
            self._sessions[key] = RecordingSession(
                agent_key=key, state=RecordingState.STOP_REQUESTED,
                purpose=existing.purpose, requested_at=existing.requested_at, kind=existing.kind,
            )
            return True

    def mark_saving(self, agent_key: str) -> None:
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            purpose = existing.purpose if existing is not None else "general"
            requested_at = existing.requested_at if existing is not None else _now_iso()
            kind = existing.kind if existing is not None else "audio"
            self._sessions[key] = RecordingSession(
                agent_key=key, state=RecordingState.SAVING,
                purpose=purpose, requested_at=requested_at, kind=kind,
            )

    def mark_saved(self, agent_key: str, artifact_id: str) -> RecordingSession:
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            purpose = existing.purpose if existing is not None else "general"
            requested_at = existing.requested_at if existing is not None else _now_iso()
            kind = existing.kind if existing is not None else "audio"
            session = RecordingSession(
                agent_key=key, state=RecordingState.SAVED,
                purpose=purpose, requested_at=requested_at, artifact_id=str(artifact_id), kind=kind,
            )
            self._sessions[key] = session
            return session

    def mark_failed(self, agent_key: str, reason: str) -> RecordingSession:
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            purpose = existing.purpose if existing is not None else "general"
            requested_at = existing.requested_at if existing is not None else _now_iso()
            kind = existing.kind if existing is not None else "audio"
            session = RecordingSession(
                agent_key=key, state=RecordingState.FAILED,
                purpose=purpose, requested_at=requested_at, error=str(reason), kind=kind,
            )
            self._sessions[key] = session
            return session

    def cancel(self, agent_key: str) -> bool:
        """Discard the in-progress session without saving. Returns False
        when nothing was in progress - cancelling a session that does
        not exist is an honest no-op, never an error a caller must
        guard against."""
        with self._lock:
            key = str(agent_key)
            existing = self._sessions.get(key)
            if existing is None or existing.state in (
                RecordingState.SAVED, RecordingState.CANCELLED,
            ):
                return False
            self._sessions[key] = RecordingSession(
                agent_key=key, state=RecordingState.CANCELLED, kind=existing.kind,
                purpose=existing.purpose, requested_at=existing.requested_at,
            )
            return True

    def clear(self, agent_key: str) -> None:
        """Return this agent to the honest READY default (no session at
        all) - used once a SAVED/FAILED/CANCELLED session has genuinely
        been acknowledged, so it does not linger forever as the
        "current" state of an agent that has moved on."""
        with self._lock:
            self._sessions.pop(str(agent_key), None)
