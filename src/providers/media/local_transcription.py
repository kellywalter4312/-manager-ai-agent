"""Local speech-to-text over real audio, with no vendor, key, network or new
dependency - Windows' own ``System.Speech`` recogniser reading a WAV file.

**Why this exists beside Whisper.** ``WhisperTranscriptionProvider`` is a
real adapter for a credentialed vendor; with no funded key it can never run.
Windows already ships an offline dictation recogniser on every machine this
project's own scripts target (``windows_speech_provider.py`` uses the same
engine for synthesis and live listening). Reading a *file* through it gives a
real, timestamped transcript of recorded or uploaded audio - honestly lower
accuracy than a vendor model, never fabricated.

**What is and is not verified.** Recognition of a real WAV file produced by a
real speech engine was verified directly against this recogniser. A human
voice recorded on a physical microphone has not been, and nothing here claims
it.

**The end of the audio is an exception, not a null.** ``Recognize()`` throws
"No audio input is supplied" once the file is consumed instead of returning
nothing; the script treats that as the end, bounded by a hard cap so a
malformed file can never loop.

**Timestamps accumulate.** Each recognised phrase reports its audio position
relative to where the previous recognition stopped, so segment boundaries are
accumulated rather than read as absolute offsets.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from src.providers.media.transcription import (
    Transcript,
    TranscriptionError,
    TranscriptionProvider,
    TranscriptSegment,
)

# A recording longer than this many recognised phrases is cut off honestly
# rather than processed without bound.
MAX_PHRASES = 400
_TIMEOUT_SECONDS = 600

_SCRIPT = r"""
Add-Type -AssemblyName System.Speech
$ErrorActionPreference = 'Stop'
$recognizer = New-Object System.Speech.Recognition.SpeechRecognitionEngine
try {
  $recognizer.LoadGrammar((New-Object System.Speech.Recognition.DictationGrammar))
  $recognizer.SetInputToWaveFile("__WAV__")
  $offset = 0.0
  for ($n = 0; $n -lt __MAX__; $n++) {
    try { $result = $recognizer.Recognize() } catch { break }
    if (-not $result) { break }
    $start = $offset + $result.Audio.AudioPosition.TotalSeconds
    $end = $start + $result.Audio.Duration.TotalSeconds
    $offset = $end
    [Console]::Out.WriteLine((@{ start = $start; end = $end; text = $result.Text; confidence = $result.Confidence } | ConvertTo-Json -Compress))
  }
} catch {
  [Console]::Error.WriteLine($_.Exception.Message)
  exit 1
} finally {
  $recognizer.Dispose()
}
"""


# PowerShell prints in the console code page unless told otherwise; every
# script here prints recognised speech (see _run_powershell).
_UTF8_PREFIX = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"


def _run_powershell(script: str, *, timeout: float) -> subprocess.CompletedProcess:
    """The one seam tests replace - never the real subprocess call.

    Transcribed speech is text a person dictated, so it can contain anything
    the recogniser's language covers. PowerShell writes its output in the
    console code page (437 here), and Python was decoding it in the system
    locale - two lossy steps that turned anything outside that page into '?'.
    Both ends are pinned to UTF-8 instead.
    """
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _UTF8_PREFIX + script],
        capture_output=True, text=True, timeout=timeout, check=False, encoding="utf-8", errors="replace",
    )


class WindowsSpeechTranscriptionProvider(TranscriptionProvider):
    """Real, offline transcription of WAV audio through Windows' recogniser."""

    @property
    def name(self) -> str:
        return "windows"

    @staticmethod
    def available() -> bool:
        """Only on Windows - everywhere else this backend honestly does not exist."""
        return sys.platform.startswith("win")

    def transcribe(self, audio: bytes) -> Transcript:
        if not audio:
            raise TranscriptionError("audio must not be empty")
        if not self.available():
            raise TranscriptionError("Windows speech recognition is only available on Windows")
        handle, raw_path = tempfile.mkstemp(suffix=".wav")
        path = Path(raw_path)
        try:
            with os.fdopen(handle, "wb") as out:
                out.write(audio)
            script = _SCRIPT.replace("__WAV__", str(path).replace('"', '`"')).replace("__MAX__", str(MAX_PHRASES))
            try:
                result = _run_powershell(script, timeout=_TIMEOUT_SECONDS)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise TranscriptionError(f"windows speech recognition could not run: {exc}") from exc
            if result.returncode != 0:
                reason = (result.stderr or "recognition failed").strip()
                raise TranscriptionError(f"windows speech recognition failed: {reason}")
            segments = []
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    data = json.loads(line)
                except ValueError:
                    continue
                text = " ".join(str(data.get("text") or "").split())
                if text:
                    segments.append(TranscriptSegment(
                        start_seconds=float(data.get("start") or 0.0),
                        end_seconds=float(data.get("end") or 0.0),
                        text=text,
                    ))
            if not segments:
                raise TranscriptionError("no speech was recognised in this audio")
            return Transcript(segments=tuple(segments), provider="windows", model="System.Speech dictation")
        finally:
            try:
                path.unlink()
            except OSError:
                pass
