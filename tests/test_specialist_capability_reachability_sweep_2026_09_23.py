"""Continuation of the final ecosystem closure (2026-09-23): representative
specialist runtime verification for capabilities the closure campaign
(section W, ARCHITECTURE.md Sec52.61) had not yet exercised end to end
through an authorised specialist - image_analysis, clip_video and
create_artifact - alongside W-READ-01 (read_content) and W-CAP-02
(create_3d_model), which already proved the generic authorisation
mechanism itself works.

**Genuine software gap found and repaired while doing this.** A
specialist's own Brain had no way to perform real visual reasoning at
all: ``SpecialistBrain`` (src/agents/brain.py) never declared an
``analyze_image`` method, and ``SpecialistCapabilities.request_for``
(scripts/control_center.py) never built one into ``CapabilityRequest`` -
so a specialist granted ``image_analysis``, even with a genuinely
vision-capable provider selected as its own Full Brain, could never be
answered by that reasoning; only the shared local-vision fallback
(``request.assist``, wired from the Manager's own skills) was ever
reachable. Repaired the same way ``converse`` already is for a
specialist: a concrete default on the ``SpecialistBrain`` ABC returning
an honest no-view (every existing implementation and test double keeps
working unchanged), a real override on ``LLMSpecialistBrain`` mirroring
``LLMManagerBrain.analyze_image``'s own honesty rules, and the missing
wire in ``request_for``.

Through the real served system (``scripts.serve_ui.build_live_system``),
real FFmpeg, a real PNG and the identical governed grant mechanism
(``capabilities grant/revoke``) the rest of the closure campaign already
proved generic.
"""

from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.config.settings import Settings
from src.providers.base import Completion, LLMProvider

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sandbox(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)


def _settings(tmp_path, **extra):
    return Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"), **extra,
    )


@pytest.fixture
def system(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch)
    return su.build_live_system(_settings(tmp_path, media="local"))


def _run(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _say(system, text, to=None):
    return exchange(system.channel, system.conversation, text, addressed=to)


def _upload(system, agent_name, data, name, mime, source="conversation"):
    status, body, _ = su.route_upload(agent_name, name, mime, base64.b64encode(data).decode(), source, system)
    return status, json.loads(body)


def _png(color=(255, 0, 0)) -> bytes:
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), color=color).save(buffer, format="PNG")
    return buffer.getvalue()


def _mp4(tmp_path) -> bytes:
    from src.providers.media.video import resolve_ffmpeg

    out = tmp_path / "made.mp4"
    subprocess.run([
        resolve_ffmpeg(), "-y",
        "-f", "lavfi", "-i", "testsrc=duration=3:size=160x120:rate=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-shortest", "-pix_fmt", "yuv420p",
        str(out),
    ], check=True, capture_output=True)
    return out.read_bytes()


class _VisionProbe(LLMProvider):
    """A real LLMProvider double that also exposes analyze_image - proving
    the mechanism, never claiming a live vendor call. The specialist
    counterpart of test_phase_3_files_documents_control_center.py's own
    _FakeVisionProvider."""

    def __init__(self, text: str = "I see a small green square.") -> None:
        self._text = text
        self.calls: list[tuple[bytes, str]] = []

    @property
    def name(self) -> str:
        return "closure_vision_probe"

    def complete(self, prompt, *, system=None):
        return Completion(text='{"should_retain": false, "content": null}', provider=self.name)

    def analyze_image(self, image_bytes: bytes, prompt: str, *, media_type: str = "image/png"):
        self.calls.append((image_bytes, prompt))
        return Completion(text=self._text, provider=self.name)


class TestASpecialistsOwnFullBrainNowReachesRealVisualReasoning:
    """The repaired gap: image_analysis granted to a specialist, with a
    vision-capable provider selected as that specialist's own Full Brain."""

    def test_half_brain_specialist_is_honest_not_silently_wrong(self, system):
        _run(system, "agent create iris Iris|image review|image_analysis")
        png = _png()
        _upload(system, "Iris", png, "pic.png", "image/png")
        turn = _say(system, "Iris, look at this image and tell me what you see", to="Iris")
        assert not turn.succeeded
        assert "couldn't analyze" in turn.outcome
        assert "small green square" not in turn.outcome

    def test_full_brain_specialist_gets_a_real_analysis_from_its_own_brain(self, tmp_path, monkeypatch):
        from src.providers import factory

        probe = _VisionProbe()
        monkeypatch.setitem(factory._BUILDERS, "closure_vision_probe", lambda settings, env=None, account=None: probe)
        _sandbox(tmp_path, monkeypatch)
        system = su.build_live_system(_settings(
            tmp_path, media="local", provider="closure_vision_probe", brain="llm", specialist_brain="llm",
        ))
        _run(system, "agent create iris Iris|image review|image_analysis")
        png = _png()
        _upload(system, "Iris", png, "pic.png", "image/png")
        turn = _say(system, "Iris, look at this image and tell me what you see", to="Iris")
        assert turn.succeeded, turn.outcome
        assert turn.outcome == "I see a small green square."
        assert probe.calls and probe.calls[0][0] == png
        # Never Alfred's own analyze_image - proof it ran as Iris, on
        # Iris's own brain, never a silent fallback to the Manager.
        iris = system.manager.registry.get("Iris")
        assert system.manager.own_skills._analyze_image is not iris.brain.analyze_image


class TestClipVideoReachesAnAuthorizedSpecialist:
    def test_a_granted_specialist_produces_a_real_distinct_clip(self, system, tmp_path):
        from src.providers.media.video import probe_duration_seconds

        _run(system, "agent create clara Clara|video editing|clip_video")
        source = _mp4(tmp_path)
        status, body = _upload(system, "Clara", source, "movie.mp4", "video/mp4")
        assert status == 200
        turn = _say(system, "Clara, clip this video from 0:00 to 0:01", to="Clara")
        assert turn.succeeded, turn.outcome
        clips = [a for a in system.runtime.artifacts.all() if a.type == "video"]
        assert len(clips) == 1
        assert clips[0].owner_agent == "Clara"
        assert body["artifact_id"] in clips[0].latest.related_artifact_ids
        clip_path = next(iter(clips[0].latest.renditions.values()))
        with open(clip_path, "rb") as handle:
            duration = probe_duration_seconds(handle.read())
        assert 0.5 <= duration <= 1.6

    def test_revoked_specialist_cannot_clip_and_nothing_clips_for_her(self, system, tmp_path):
        _run(system, "agent create clara Clara|video editing|clip_video")
        _run(system, "capabilities revoke Clara clip_video")
        source = _mp4(tmp_path)
        _upload(system, "Clara", source, "movie.mp4", "video/mp4")
        turn = _say(system, "Clara, clip this video from 0:00 to 0:01", to="Clara")
        assert not turn.succeeded
        assert "clip_video, which I'm not authorised for" in turn.outcome
        assert not [a for a in system.runtime.artifacts.all() if a.type == "video"]


class TestCreateArtifactReachesAnAuthorizedSpecialist:
    """Half-Brain-safe path: structured content given directly needs no
    model, so this is provable with no provider configured at all."""

    REQUEST = (
        "Clara, make a handout: TITLE: Garden shed plan "
        "SECTION: Materials: cedar boards and galvanised screws "
        "SECTION: Timeline: framed by the second weekend of October"
    )

    def test_a_granted_specialist_produces_a_real_pdf_artifact(self, system):
        _run(system, "agent create clara Clara|documentation|create_artifact")
        turn = _say(system, self.REQUEST, to="Clara")
        assert turn.succeeded, turn.outcome
        assert "Artifact created" in turn.outcome
        docs = [a for a in system.runtime.artifacts.all() if a.type == "document"]
        assert len(docs) == 1
        assert docs[0].owner_agent == "Clara"
        assert docs[0].latest.title == "Garden shed plan"

    def test_ungranted_specialist_is_refused_and_alfred_does_not_do_it_for_her(self, system):
        _run(system, "agent create clara Clara|documentation")
        turn = _say(system, self.REQUEST, to="Clara")
        assert not turn.succeeded
        assert "create_artifact, which I'm not authorised for" in turn.outcome
        assert not system.runtime.artifacts.all()
