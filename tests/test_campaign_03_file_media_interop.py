"""Pre-Stage-9 Campaign 3 - shared files and media are usable ecosystem
artifacts, whatever door they came through.

Through the real served system (``build_live_system``) over temp directories,
with real files only: an OOXML Word document, a real PNG, a real FFmpeg-made
MP4, and - on Windows - real speech synthesised and recognised by Windows' own
engine. Nothing here names a subject; the file contents are examples.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import subprocess
import sys
import zipfile

import pytest

import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.agents.tools.media_production import find_media_path, parse_clip_specs
from src.providers.document_knowledge_provider import DocumentKnowledgeProvider
from src.providers.media import local_transcription
from src.providers.media.factory import available_video_understanding_backends
from src.providers.media.transcription import TranscriptionError

ON_WINDOWS = sys.platform.startswith("win")
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _system(tmp_path, **overrides):
    settings = Settings.from_env(
        env={},
        memory=overrides.pop("memory", "ephemeral"),
        artifacts_dir=str(tmp_path / "artifacts"),
        media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"),
        documents_dir=str(tmp_path / "docs"),
        **overrides,
    )
    return su.build_live_system(settings)


def _agent(system) -> str:
    return system.manager.own_skills.name


def _upload(system, data: bytes, name: str, mime: str, source: str = "conversation"):
    status, body, _ = su.route_upload(_agent(system), name, mime, base64.b64encode(data).decode(), source, system)
    return status, json.loads(body)


def _say(system, text: str):
    return exchange(system.channel, system.conversation, text, addressed=_agent(system))


def _docx(*paragraphs: str) -> bytes:
    body = "".join(f'<w:p><w:r><w:t xml:space="preserve">{p}</w:t></w:r></w:p>' for p in paragraphs)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as package:
        package.writestr("[Content_Types].xml", (
            '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'
        ))
        package.writestr("word/document.xml", (
            '<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body>{body}</w:body></w:document>"
        ))
    return buffer.getvalue()


def _png() -> bytes:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (24, 24), (40, 160, 90)).save(buffer, format="PNG")
    return buffer.getvalue()


def _ffmpeg(tmp_path, args, suffix) -> bytes:
    from src.providers.media.video import resolve_ffmpeg

    out = tmp_path / f"made{suffix}"
    subprocess.run([resolve_ffmpeg(), "-y", *args, str(out)], check=True, capture_output=True)
    return out.read_bytes()


def _mp4(tmp_path) -> bytes:
    return _ffmpeg(tmp_path, [
        "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
    ], ".mp4")


def _speech_wav(tmp_path, sentence: str) -> bytes:
    """Real speech from Windows' own synthesiser - no network, no key."""
    path = tmp_path / "speech.wav"
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{path}'); $s.Speak('{sentence}'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=60)
    return path.read_bytes()


class TestWordDocumentsAreRead:
    def test_a_supplied_word_document_is_read_aloud_by_paragraph(self, tmp_path):
        system = _system(tmp_path)
        status, body = _upload(system, _docx("Quarterly plan", "Revenue grew in the second quarter."), "plan.docx", DOCX_MIME)
        assert status == 200 and body["readable_units"] == 2

        whole = _say(system, "read this file to me")
        assert whole.succeeded and "Revenue grew in the second quarter." in whole.outcome
        second = _say(system, "what does paragraph 2 say?")
        assert second.succeeded and "Revenue grew" in second.outcome and "Quarterly plan" not in second.outcome

    def test_a_malformed_word_document_is_skipped_not_fatal(self, tmp_path):
        folder = tmp_path / "docs"
        folder.mkdir()
        (folder / "broken.docx").write_bytes(b"not a zip")
        (folder / "fine.docx").write_bytes(_docx("Still readable."))
        provider = DocumentKnowledgeProvider(folder)
        assert provider.passages(folder / "broken.docx") == ()
        assert provider.passages(folder / "fine.docx") == (("paragraph 1", "Still readable."),)


class TestThisFileMeansTheFileSharedHere:
    def test_it_survives_the_history_window_and_never_reads_an_unshared_file(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, _docx("Shared in the conversation."), "shared.docx", DOCX_MIME)
        _upload(system, _docx("Only added in the Control Center."), "other.docx", DOCX_MIME, source="control_center")
        for filler in ("thanks", "that's great", "ok", "sounds good", "cool", "nice", "alright", "fine"):
            _say(system, filler)
        turn = _say(system, "read this file to me")
        assert turn.succeeded
        assert "Shared in the conversation." in turn.outcome
        assert "Control Center" not in turn.outcome

    def test_a_shared_file_with_no_text_is_named_honestly(self, tmp_path):
        system = _system(tmp_path)
        _upload(system, _docx("A readable document."), "notes.docx", DOCX_MIME)
        _upload(system, _mp4(tmp_path), "movie.mp4", "video/mp4")
        turn = _say(system, "read this file to me")
        assert not turn.succeeded
        assert "movie.mp4" in turn.outcome and "video" in turn.outcome
        assert "A readable document." not in turn.outcome

        named = _say(system, "read this document to me")
        assert named.succeeded and "A readable document." in named.outcome


class TestSharedMediaReachesTheCapabilities:
    def test_a_shared_image_reaches_image_analysis_with_its_real_bytes(self, tmp_path):
        system = _system(tmp_path)
        seen = {}

        def analyze(image_bytes, prompt):
            from src.manager.brain import BrainDecision

            seen["sha"] = hashlib.sha256(image_bytes).hexdigest()
            return BrainDecision.no_view("no vision model in this test")

        system.manager.own_skills._analyze_image = analyze
        png = _png()
        _upload(system, png, "pic.png", "image/png")
        turn = _say(system, "look at this image and tell me what you see")
        assert turn.capability == "image_analysis"
        assert seen.get("sha") == hashlib.sha256(png).hexdigest()
        assert "don't see an image" not in turn.outcome

    def test_a_shared_video_is_clipped_into_a_linked_artifact(self, tmp_path):
        from src.providers.media.video import probe_duration_seconds

        system = _system(tmp_path, media="local")
        _status, body = _upload(system, _mp4(tmp_path), "movie.mp4", "video/mp4")
        turn = _say(system, "clip this video from 0:00 to 0:01")
        assert turn.succeeded, turn.outcome
        assert turn.capability == "clip_video"
        clips = [a for a in system.runtime.artifacts.all() if a.type == "video"]
        assert len(clips) == 1
        assert body["artifact_id"] in clips[0].latest.related_artifact_ids
        clip_path = next(iter(clips[0].latest.renditions.values()))
        with open(clip_path, "rb") as handle:
            assert 0.5 <= probe_duration_seconds(handle.read()) <= 1.6

    def test_digits_in_the_source_path_are_never_read_as_a_time_range(self, tmp_path):
        """Found at runtime: a stored artifact path like ".../run-2079-4d6c/..."
        was read as the range 2079-4 when the path travelled with the request."""
        from src.providers.media.video import probe_duration_seconds

        folder = tmp_path / "run-2079-4d6c" / "v1"
        folder.mkdir(parents=True)
        source = folder / "movie.mp4"
        source.write_bytes(_mp4(tmp_path))
        system = _system(tmp_path, media="local")
        outcome = system.manager.own_skills.tools.invoke(
            "media_production", {"operation": "clip", "content": f"{source}\nclip this video from 0:00 to 0:01"},
        )
        assert outcome.ok, outcome.error
        assert "1 clip(s) produced" in outcome.output
        clip_path = outcome.output.splitlines()[1].split(" (")[0].strip()
        with open(clip_path, "rb") as handle:
            assert 0.5 <= probe_duration_seconds(handle.read()) <= 1.6

    def test_time_ranges_are_read_as_people_say_them(self):
        spec = parse_clip_specs("clip this video from 0:00 to 0:02")[0]
        assert (spec.start_seconds, spec.end_seconds) == (0.0, 2.0)
        assert find_media_path("transcribe C:/tmp/note.webm please") == "C:/tmp/note.webm"


class TestTranscription:
    def test_without_a_provider_it_is_refused_honestly(self, tmp_path):
        system = _system(tmp_path, media="local")
        _upload(system, _mp4(tmp_path), "movie.mp4", "video/mp4")
        turn = _say(system, "transcribe this video")
        assert turn.capability == "transcribe_media"
        assert not turn.succeeded and "movie.mp4" in turn.outcome
        assert "no speech-transcription provider is configured" in turn.outcome

    def test_the_local_backend_is_offered(self):
        assert "windows" in available_video_understanding_backends()

    def test_the_local_provider_reads_segments_through_its_seam(self, monkeypatch):
        lines = "\n".join((
            json.dumps({"start": 0.0, "end": 1.5, "text": "first phrase", "confidence": 0.9}),
            json.dumps({"start": 1.8, "end": 3.0, "text": "second phrase", "confidence": 0.8}),
        ))
        monkeypatch.setattr(local_transcription.sys, "platform", "win32")
        monkeypatch.setattr(
            local_transcription, "_run_powershell",
            lambda script, timeout: subprocess.CompletedProcess(args=[], returncode=0, stdout=lines, stderr=""),
        )
        transcript = local_transcription.WindowsSpeechTranscriptionProvider().transcribe(b"RIFF....WAVE")
        assert [s.text for s in transcript.segments] == ["first phrase", "second phrase"]
        assert transcript.segments[1].start_seconds == 1.8

        monkeypatch.setattr(
            local_transcription, "_run_powershell",
            lambda script, timeout: subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
        )
        with pytest.raises(TranscriptionError):
            local_transcription.WindowsSpeechTranscriptionProvider().transcribe(b"RIFF....WAVE")
        with pytest.raises(TranscriptionError):
            local_transcription.WindowsSpeechTranscriptionProvider().transcribe(b"")

    @pytest.mark.skipif(not ON_WINDOWS, reason="Windows' own speech engine")
    def test_shared_audio_and_the_latest_recording_are_really_transcribed(self, tmp_path):
        system = _system(tmp_path, media="local", video_understanding="windows")
        shared_speech = _speech_wav(tmp_path, "The meeting is moved to Friday afternoon.")
        _upload(system, shared_speech, "note.wav", "audio/wav")
        shared = _say(system, "transcribe this audio")
        assert shared.succeeded, shared.outcome
        assert "meeting" in shared.outcome.lower()

        recorded_speech = _speech_wav(tmp_path, "Please bring the quarterly report.")
        agent = _agent(system)
        su.route_recording_started(agent, "research", system)
        status, _body, _ = su.route_recording_save(
            agent, "research", base64.b64encode(recorded_speech).decode(), "audio/wav", 3.0, system,
        )
        assert status == 200
        recorded = _say(system, "transcribe my recording")
        assert recorded.succeeded, recorded.outcome
        # The recording, not the file shared earlier in the conversation.
        assert "report" in recorded.outcome.lower()
        assert "meeting" not in recorded.outcome.lower()
