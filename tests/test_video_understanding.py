"""Video *understanding* - genuine content understanding derived from a
video's own real, actual audio, distinct from every prior video
checkpoint: video.py composes, video_generation.py synthesises,
video_clipping.py cuts at already-known timestamps. This is the missing
piece those three, together, could not provide: turning a video's own
real audio into a real, timestamped transcript
(``video.py::extract_audio_wav`` + ``transcription.py``), so
``clip_video`` can find moments *in the video itself* - not only in text
the user happened to type separately.

Every test that touches real media shells out to a genuine FFmpeg
binary (via imageio-ffmpeg, no network) - never a description of what
extraction or transcription would produce. The real vendor
(``WhisperTranscriptionProvider``) is exercised through its own
injectable transport, the same established discipline
``RunwayVideoProvider``/``GoogleVeoVideoProvider`` already use, since
this project holds no funded OpenAI account to verify a live response
against; that boundary is stated honestly wherever it applies, never
disguised as a live verification.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest

from src.agents.tools.media_production import MediaProductionTool
from src.agents.tools.toolset import ToolSet
from src.core.contracts import Task
from src.manager.capabilities import CapabilityRequest, clip_video
from src.providers.media.image import LocalTitleCardImageProvider, NullImageProvider
from src.providers.media.narration import NullNarrationProvider
from src.providers.media.video import (
    FFmpegVideoComposer,
    NullVideoComposer,
    VideoError,
    VideoScene,
    extract_audio_wav,
    resolve_ffmpeg,
    run_ffmpeg,
)
from src.providers.media.video_clipping import FFmpegVideoClipper, NullVideoClipper
from src.providers.media.transcription import (
    NullTranscriptionProvider,
    Transcript,
    TranscriptionError,
    TranscriptSegment,
    WhisperTranscriptionProvider,
    _HTTPResponse,
)


def _real_source_video(seconds: float = 6.0) -> bytes:
    """A real, genuine, playable video with a real (silent) audio
    track - the identical construction test_video_clipping.py's own
    ``_real_source_video`` already uses, so extract_audio_wav has a
    genuine audio stream to pull from."""
    image = LocalTitleCardImageProvider().generate("understanding test source")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    scene = VideoScene(image=image.data, audio=buf.getvalue(), duration_seconds=seconds)
    return FFmpegVideoComposer().compose([scene]).data


@pytest.fixture(scope="module")
def source_video() -> bytes:
    return _real_source_video()


def _silent_video_with_no_audio_stream(seconds: float = 2.0) -> bytes:
    """A real video with genuinely no audio stream at all - built with a
    direct ffmpeg call (``-an``), never through FFmpegVideoComposer,
    which always muxes a real audio track. Proves extract_audio_wav's
    own honest refusal is reached for a real absence, not merely a
    silent-but-present track."""
    import tempfile

    image = LocalTitleCardImageProvider().generate("no audio track")
    ffmpeg = resolve_ffmpeg()
    with tempfile.TemporaryDirectory(prefix="alfred_test_noaudio_") as workdir:
        work = Path(workdir)
        image_path = work / "still.png"
        image_path.write_bytes(image.data)
        output_path = work / "silent.mp4"
        run_ffmpeg(
            ffmpeg,
            [
                "-loop", "1", "-i", str(image_path),
                "-t", f"{seconds:.2f}",
                "-an",
                "-c:v", "libx264", "-pix_fmt", "yuv420p",
                str(output_path),
            ],
        )
        return output_path.read_bytes()


# ---------------------------------------------------------------------------
# extract_audio_wav - real, local, deterministic audio extraction
# ---------------------------------------------------------------------------


class TestExtractAudioWav:
    def test_extracts_a_real_decodable_wav_from_a_real_video(self, source_video):
        audio = extract_audio_wav(source_video)
        assert audio[:4] == b"RIFF"
        assert audio[8:12] == b"WAVE"
        with wave.open(io.BytesIO(audio), "rb") as w:
            assert w.getnframes() > 0
            assert w.getframerate() == 16000
            assert w.getnchannels() == 1

    def test_a_video_with_no_audio_stream_is_refused_honestly(self):
        video_bytes = _silent_video_with_no_audio_stream()
        with pytest.raises(VideoError, match="no audio track"):
            extract_audio_wav(video_bytes)

    def test_a_corrupt_file_is_refused_honestly(self):
        with pytest.raises(VideoError):
            extract_audio_wav(b"not a real video file at all")

    def test_the_source_bytes_are_never_mutated(self, source_video):
        before = bytes(source_video)
        extract_audio_wav(source_video)
        assert source_video == before


# ---------------------------------------------------------------------------
# Transcript / TranscriptSegment
# ---------------------------------------------------------------------------


class TestTranscript:
    def test_empty_segments_is_refused(self):
        with pytest.raises(TranscriptionError, match="must not be empty"):
            Transcript(segments=())

    def test_as_timestamped_text_renders_one_line_per_segment(self):
        transcript = Transcript(
            segments=(
                TranscriptSegment(start_seconds=0.0, end_seconds=3.2, text="  Hello there.  "),
                TranscriptSegment(start_seconds=65.0, end_seconds=70.0, text="A minute later."),
            )
        )
        rendered = transcript.as_timestamped_text()
        assert rendered == "0:00-0:03: Hello there.\n1:05-1:10: A minute later."


class TestNullTranscriptionProvider:
    def test_name_is_null(self):
        assert NullTranscriptionProvider().name == "null"

    def test_refuses_honestly_rather_than_inventing_text(self):
        with pytest.raises(TranscriptionError, match="no speech-transcription provider"):
            NullTranscriptionProvider().transcribe(b"some audio bytes")


# ---------------------------------------------------------------------------
# WhisperTranscriptionProvider - real mechanism, scripted transport
# ---------------------------------------------------------------------------


class TestWhisperTranscriptionProvider:
    def test_empty_api_key_is_refused_at_construction(self):
        with pytest.raises(TranscriptionError, match="API key"):
            WhisperTranscriptionProvider(api_key="   ")

    def test_empty_audio_is_refused(self):
        provider = WhisperTranscriptionProvider(api_key="k", transport=lambda *a: None)
        with pytest.raises(TranscriptionError, match="must not be empty"):
            provider.transcribe(b"")

    def test_a_successful_response_produces_a_real_timestamped_transcript(self):
        captured = {}

        def transport(method, url, headers, body):
            captured["method"] = method
            captured["url"] = url
            captured["headers"] = headers
            captured["body"] = body
            payload = {
                "language": "english",
                "segments": [
                    {"start": 0.0, "end": 2.5, "text": " Hi everyone."},
                    {"start": 2.5, "end": 6.0, "text": " Welcome to the show."},
                ],
            }
            return _HTTPResponse(status=200, headers={}, body=json.dumps(payload).encode("utf-8"))

        provider = WhisperTranscriptionProvider(api_key="sk-test", transport=transport)
        transcript = provider.transcribe(b"fake wav bytes")

        assert transcript.provider == "whisper"
        assert transcript.language == "english"
        assert len(transcript.segments) == 2
        assert transcript.segments[0].text.strip() == "Hi everyone."
        assert captured["method"] == "POST"
        assert captured["url"].endswith("/audio/transcriptions")
        assert captured["headers"]["Authorization"] == "Bearer sk-test"
        assert "multipart/form-data; boundary=" in captured["headers"]["Content-Type"]
        # The real, encoded multipart body genuinely contains the audio
        # bytes and the requested response format - not merely a
        # description of what would be sent.
        assert b"fake wav bytes" in captured["body"]
        assert b"verbose_json" in captured["body"]

    def test_an_http_error_is_a_genuine_failure(self):
        def transport(method, url, headers, body):
            return _HTTPResponse(status=401, headers={}, body=b'{"error": "bad key"}')

        provider = WhisperTranscriptionProvider(api_key="sk-bad", transport=transport)
        with pytest.raises(TranscriptionError, match="HTTP 401"):
            provider.transcribe(b"audio")

    def test_a_response_with_no_segments_is_a_genuine_failure_not_a_flat_guess(self):
        def transport(method, url, headers, body):
            return _HTTPResponse(status=200, headers={}, body=b'{"language": "en", "segments": []}')

        provider = WhisperTranscriptionProvider(api_key="sk-test", transport=transport)
        with pytest.raises(TranscriptionError, match="no timestamped segments"):
            provider.transcribe(b"audio")

    def test_unreadable_json_is_a_genuine_failure(self):
        def transport(method, url, headers, body):
            return _HTTPResponse(status=200, headers={}, body=b"not json at all")

        provider = WhisperTranscriptionProvider(api_key="sk-test", transport=transport)
        with pytest.raises(TranscriptionError, match="unreadable response"):
            provider.transcribe(b"audio")


# ---------------------------------------------------------------------------
# MediaProductionTool's new "transcribe" operation
# ---------------------------------------------------------------------------


class _ScriptedTranscriber:
    """A test double standing in for a real, funded vendor - the same
    established discipline test_video_clipping.py's own ScriptedProvider
    already uses for the Brain."""

    def __init__(self, transcript: Transcript):
        self._transcript = transcript
        self.calls: list[bytes] = []

    @property
    def name(self) -> str:
        return "scripted"

    def transcribe(self, audio: bytes) -> Transcript:
        self.calls.append(audio)
        return self._transcript


class TestMediaProductionToolTranscribeOperation:
    def _tool(self, tmp_path, transcriber=None):
        return MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            transcriber=transcriber or NullTranscriptionProvider(),
            asset_dir=tmp_path / "assets",
        )

    def test_missing_source_path_is_refused(self, tmp_path):
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="real source (audio or )?video path"):
            tool.run(operation="transcribe", content="transcribe this please")

    def test_nonexistent_source_file_is_refused(self, tmp_path):
        tool = self._tool(tmp_path)
        with pytest.raises(ValueError, match="does not exist"):
            tool.run(operation="transcribe", content=str(tmp_path / "ghost.mp4"))

    def test_the_null_transcriber_refuses_honestly_through_the_tool(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = self._tool(tmp_path)
        with pytest.raises(TranscriptionError, match="no speech-transcription provider"):
            tool.run(operation="transcribe", content=str(source_path))

    def test_a_scripted_transcriber_produces_real_timestamped_output(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        transcript = Transcript(segments=(TranscriptSegment(0.0, 3.0, "the opening line"),))
        scripted = _ScriptedTranscriber(transcript)
        tool = self._tool(tmp_path, transcriber=scripted)

        result = tool.run(operation="transcribe", content=str(source_path))

        assert result == "0:00-0:03: the opening line"
        assert len(scripted.calls) == 1
        # The transcriber genuinely received real, decoded audio bytes
        # extracted from the source - not the video bytes themselves.
        assert scripted.calls[0][:4] == b"RIFF"

    def test_the_source_file_is_left_completely_intact(self, tmp_path, source_video):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        transcript = Transcript(segments=(TranscriptSegment(0.0, 1.0, "x"),))
        tool = self._tool(tmp_path, transcriber=_ScriptedTranscriber(transcript))
        tool.run(operation="transcribe", content=str(source_path))
        assert source_path.read_bytes() == source_video

    def test_transcribe_is_a_recognised_operation_in_run_dispatch(self, tmp_path):
        tool = self._tool(tmp_path)
        try:
            tool.run(operation="bogus", content="x")
        except ValueError as exc:
            assert "transcribe" in str(exc)


# ---------------------------------------------------------------------------
# clip_video's new branch: real, media-derived understanding
# ---------------------------------------------------------------------------


def _clip_request(*, tools: ToolSet, converse, description: str) -> CapabilityRequest:
    """A hand-built CapabilityRequest - the identical minimal-construction
    pattern test_phase_4_real_world_communication.py's own tests already
    use to exercise one capability handler directly, without needing the
    full Manager/ControlCenter wiring."""
    from src.services.identity.identity import AgentIdentity

    return CapabilityRequest(
        task=Task(description=description),
        identity=AgentIdentity(name="Alfred", role="Manager", capabilities=frozenset({"clip"})),
        roster=lambda: (),
        own_name="Alfred",
        own_capabilities=(),
        tools=tools,
        converse=converse,
    )


class TestClipVideoDerivesRealUnderstandingFromActualMedia:
    def test_no_transcript_supplied_derives_one_from_the_videos_own_audio_and_clips_it(
        self, tmp_path, source_video
    ):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)

        transcript = Transcript(
            segments=(
                TranscriptSegment(0.0, 1.0, "a quiet opening"),
                TranscriptSegment(1.0, 3.0, "and then everyone laughs"),
                TranscriptSegment(3.0, 6.0, "a slow close"),
            )
        )
        scripted_transcriber = _ScriptedTranscriber(transcript)
        tool = MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=FFmpegVideoClipper(),
            transcriber=scripted_transcriber,
            asset_dir=tmp_path / "assets",
        )
        tools = ToolSet([tool])

        seen_notes: list[tuple] = []

        class FakeDecision:
            is_decided = True
            reason = "0:01-0:03"

        def converse(task, notes):
            seen_notes.append(notes)
            return FakeDecision()

        request = _clip_request(
            tools=tools, converse=converse,
            description=f"clip the video {source_path}, find the funniest moment",
        )
        result = clip_video(request)

        assert result.succeeded, result.error
        assert len(scripted_transcriber.calls) == 1, "the video's own audio must genuinely be transcribed"
        # The Brain was genuinely given the real, derived transcript to
        # reason over - not asked to guess from the bare instruction.
        assert "and then everyone laughs" in seen_notes[0][0]
        assert "derived from its actual audio" in seen_notes[0][0]
        produced = list((tmp_path / "assets").glob("*.mp4"))
        assert produced and produced[0].stat().st_size > 0

    def test_a_supplied_transcript_still_takes_priority_over_real_media_derivation(
        self, tmp_path, source_video
    ):
        """When the person already typed a real transcript/description,
        clip_video must reason over THAT directly rather than touching
        the actual media - cheaper, and the identical, already-proven
        path this capability held before this checkpoint."""
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)

        scripted_transcriber = _ScriptedTranscriber(
            Transcript(segments=(TranscriptSegment(0.0, 1.0, "should never be reached"),))
        )
        tool = MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=FFmpegVideoClipper(),
            transcriber=scripted_transcriber,
            asset_dir=tmp_path / "assets",
        )
        tools = ToolSet([tool])

        class FakeDecision:
            is_decided = True
            reason = "0:00-0:02"

        long_transcript = (
            "Transcript: the host opens with a greeting, the crowd erupts into "
            "laughter moments later at the first real joke, and the segment "
            "closes on applause well before the clip's own short runtime ends."
        )
        request = _clip_request(
            tools=tools, converse=lambda task, notes: FakeDecision(),
            description=f"clip the video {source_path} - find the funniest moment. {long_transcript}",
        )
        result = clip_video(request)

        assert result.succeeded, result.error
        assert scripted_transcriber.calls == [], "a supplied transcript must never trigger real media extraction"

    def test_derivation_failure_when_no_transcription_provider_is_configured_is_refused_honestly(
        self, tmp_path, source_video
    ):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        tool = MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=FFmpegVideoClipper(),
            transcriber=NullTranscriptionProvider(),
            asset_dir=tmp_path / "assets",
        )
        tools = ToolSet([tool])

        request = _clip_request(
            tools=tools,
            converse=lambda task, notes: (_ for _ in ()).throw(AssertionError("must not be reached")),
            description=f"clip the video {source_path}, find the best moments",
        )
        result = clip_video(request)

        assert not result.succeeded
        assert "speech-transcription provider" in result.error

    def test_a_malformed_or_undecodable_video_is_refused_honestly(self, tmp_path):
        source_path = tmp_path / "corrupt.mp4"
        source_path.write_bytes(b"this is not a real video file")
        tool = MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=FFmpegVideoClipper(),
            transcriber=_ScriptedTranscriber(Transcript(segments=(TranscriptSegment(0, 1, "x"),))),
            asset_dir=tmp_path / "assets",
        )
        tools = ToolSet([tool])

        request = _clip_request(
            tools=tools,
            converse=lambda task, notes: (_ for _ in ()).throw(AssertionError("must not be reached")),
            description=f"clip the video {source_path}, find the best moments",
        )
        result = clip_video(request)

        assert not result.succeeded
        assert "understanding its actual content" in result.error

    def test_no_reasoning_capability_at_all_is_refused_without_attempting_transcription(
        self, tmp_path, source_video
    ):
        source_path = tmp_path / "source.mp4"
        source_path.write_bytes(source_video)
        scripted_transcriber = _ScriptedTranscriber(
            Transcript(segments=(TranscriptSegment(0.0, 1.0, "should never be reached"),))
        )
        tool = MediaProductionTool(
            NullNarrationProvider(), NullImageProvider(), NullVideoComposer(),
            clipper=FFmpegVideoClipper(),
            transcriber=scripted_transcriber,
            asset_dir=tmp_path / "assets",
        )
        request = _clip_request(
            tools=ToolSet([tool]), converse=None,
            description=f"clip the video {source_path}, find the best moments",
        )
        result = clip_video(request)

        assert not result.succeeded
        assert "no reasoning capability" in result.error
        assert scripted_transcriber.calls == []


# ---------------------------------------------------------------------------
# Settings - the video_understanding axis
# ---------------------------------------------------------------------------


class TestSettingsVideoUnderstandingAxis:
    def test_defaults_to_null(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={})
        assert settings.video_understanding == "null"
        assert settings.whisper_model == "whisper-1"

    def test_env_var_reflection_picks_up_manager_video_understanding(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={"MANAGER_VIDEO_UNDERSTANDING": "whisper"})
        assert settings.video_understanding == "whisper"

    def test_env_var_reflection_picks_up_manager_whisper_model(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={"MANAGER_WHISPER_MODEL": "whisper-large-v3"})
        assert settings.whisper_model == "whisper-large-v3"

    def test_is_normalised_and_validated_like_every_sibling_axis(self):
        from src.config.settings import Settings

        settings = Settings.from_env(env={}, video_understanding="  WHISPER  ")
        assert settings.video_understanding == "whisper"

        with pytest.raises(ValueError, match="video_understanding"):
            Settings.from_env(env={}, video_understanding="   ")


# ---------------------------------------------------------------------------
# factory.py - the video_understanding provider builder
# ---------------------------------------------------------------------------


class TestBuildVideoUnderstanding:
    def test_available_backends(self):
        from src.providers.media.factory import available_video_understanding_backends

        # Post-Pre-Stage-9: local multilingual recognition for live translation.
        assert available_video_understanding_backends() == ["local_whisper", "null", "whisper", "windows"]

    def test_null_is_the_honest_default(self):
        from src.config.settings import Settings
        from src.providers.media.factory import build_media

        tools = build_media(Settings.from_env(env={}))
        assert isinstance(tools.transcriber, NullTranscriptionProvider)

    def test_whisper_without_a_credential_fails_fast_before_any_network_call(self):
        from src.config.settings import Settings
        from src.providers.factory import ProviderConfigurationError
        from src.providers.media.factory import build_media

        with pytest.raises(ProviderConfigurationError, match="WHISPER_API_KEY"):
            build_media(Settings.from_env(env={}, video_understanding="whisper"), env={})

    def test_whisper_with_a_credential_builds_the_real_provider(self):
        from src.config.settings import Settings
        from src.providers.media.factory import build_media

        tools = build_media(
            Settings.from_env(env={}, video_understanding="whisper"),
            env={"WHISPER_API_KEY": "sk-real-enough"},
        )
        assert isinstance(tools.transcriber, WhisperTranscriptionProvider)
        assert tools.transcriber._model == "whisper-1"

    def test_a_whisper_credential_never_leaks_from_the_llm_providers_own_key(self):
        """The identical per-axis isolation RUNWAY_KEY_ENV/VEO_KEY_ENV
        already require, applied to Whisper - OPENAI_API_KEY (the LLM
        provider's own credential) must never authorise Whisper."""
        from src.config.settings import Settings
        from src.providers.factory import ProviderConfigurationError
        from src.providers.media.factory import build_media

        with pytest.raises(ProviderConfigurationError):
            build_media(
                Settings.from_env(env={}, video_understanding="whisper"),
                env={"OPENAI_API_KEY": "sk-llm-key-only"},
            )

    def test_account_override_picks_the_vendor_independent_of_settings(self):
        from src.config.settings import Settings
        from src.providers.media.factory import build_media
        from src.services.credentials.account import ProviderAccount

        account = ProviderAccount(
            provider="whisper", account_id="acct", credential="sk-from-account", model="whisper-large-v3",
        )
        tools = build_media(
            Settings.from_env(env={}), video_understanding_account=account,
        )
        assert isinstance(tools.transcriber, WhisperTranscriptionProvider)
        assert tools.transcriber._api_key == "sk-from-account"
        assert tools.transcriber._model == "whisper-large-v3"

    def test_unknown_backend_is_refused(self):
        from src.config.settings import Settings
        from src.providers.media.factory import build_media

        with pytest.raises(ValueError, match="Unknown video-understanding provider"):
            build_media(Settings.from_env(env={}, video_understanding="not-a-real-vendor"))


class TestMediaToolsTranscriberWiring:
    def test_every_media_backend_gets_the_honest_null_transcriber_by_default(self):
        from src.config.settings import Settings
        from src.providers.media.factory import build_media

        for media in ("null", "local", "ai_image"):
            tools = build_media(Settings.from_env(env={}, media=media))
            assert isinstance(tools.transcriber, NullTranscriptionProvider), media

    def test_the_transcriber_axis_is_independent_of_the_media_axis(self):
        """Selecting a real transcription vendor must not depend on, or
        require, any particular `media` backend - the same
        "one deployment may want one without the other" independence
        video_generator already has from `media`."""
        from src.config.settings import Settings
        from src.providers.media.factory import build_media

        for media in ("null", "local", "ai_image"):
            tools = build_media(
                Settings.from_env(env={}, media=media, video_understanding="whisper"),
                env={"WHISPER_API_KEY": "sk-real-enough"},
            )
            assert isinstance(tools.transcriber, WhisperTranscriptionProvider), media

    def test_the_real_production_build_wires_it_through(self):
        import scripts.control_center as cc
        from src.config.settings import Settings

        runtime, manager, channel, conversation = cc.build(
            Settings.from_env(env={}, memory="ephemeral", media="local")
        )
        assert isinstance(runtime.media.transcriber, NullTranscriptionProvider)
        assert "media_production" in manager.own_skills.tools.names()


# ---------------------------------------------------------------------------
# Ecosystem registry - the governed provider lifecycle
# ---------------------------------------------------------------------------


class TestVideoUnderstandingProviderCategory:
    def test_registered_as_its_own_category(self):
        from src.config.settings import Settings
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        registry = build_ecosystem_registry(Settings.from_env(env={}))
        assert "video_understanding_provider" in registry.categories()

    def test_lists_the_real_backends(self):
        from src.config.settings import Settings
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("video_understanding_provider")
        assert {d.key for d in adapter.available()} == {"local_whisper", "null", "whisper", "windows"}

    def test_validate_requires_a_credential_for_whisper_but_not_null(self):
        from src.config.settings import Settings
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        registry = build_ecosystem_registry(Settings.from_env(env={}))
        adapter = registry.adapter_for("video_understanding_provider")
        assert adapter.validate("null").ok
        assert not adapter.validate("whisper").ok
        assert adapter.validate("whisper", credential="sk-fake-but-present").ok

    def test_a_credential_given_here_never_reaches_video_generation(self):
        from src.config.settings import Settings
        from src.services.ecosystem.registry_factory import build_ecosystem_registry

        registry = build_ecosystem_registry(Settings.from_env(env={}))
        registry.adapter_for("video_understanding_provider").validate("whisper", credential="sk-only-whisper")
        # video_generation_provider's own runway/veo validation is
        # entirely unaffected - structurally, since each adapter's build
        # closure only ever wraps its own category's real factory.
        gen_adapter = registry.adapter_for("video_generation_provider")
        assert not gen_adapter.validate("runway").ok


class TestControlCenterProviderAxisNames:
    def test_whisper_is_recognised_as_a_provider_backend_not_a_connection(self):
        import scripts.control_center as cc

        assert "whisper" in cc._provider_axis_backend_names()

    def test_provider_command_can_add_select_and_revoke_a_whisper_account(self, tmp_path, monkeypatch):
        import scripts.control_center as cc
        from src.config.settings import Settings

        monkeypatch.setattr(cc, "DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json")
        runtime, manager, channel, conversation = cc.build(Settings.from_env(env={}, memory="ephemeral"))
        centre = cc.ControlCenter(runtime, manager)

        added = cc.run_provider_command(
            centre, "video_understanding_provider add whisper acct sk-whisper-test"
        )
        assert "error" not in added.lower(), added

        selected = cc.run_provider_command(centre, "video_understanding_provider select whisper acct")
        assert "error" not in selected.lower(), selected

        runtime2, *_ = cc.build(Settings.from_env(env={}, memory="ephemeral", video_understanding="whisper"))
        assert runtime2.media.transcriber.name == "whisper"
        assert runtime2.media.transcriber._api_key == "sk-whisper-test"

        revoked = cc.run_provider_command(centre, "video_understanding_provider revoke whisper acct")
        assert "error" not in revoked.lower(), revoked
