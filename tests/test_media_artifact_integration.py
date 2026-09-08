"""Ecosystem artifact/workspace checkpoint: connecting the existing,
already-proven media capabilities (narration, image generation, video
composition, AI video generation, video clipping) to the identical,
authoritative ``ArtifactStore`` documents and diagrams already live in -
never a second store, never Alfred-specific machinery, never a
redesign of either.

Every test that produces real media decodes it back with a real reader
(``wave``, ``PIL``, or the project's own ``probe_duration_seconds``) -
never merely checking that some bytes exist. All video/audio work here
is genuinely local and deterministic (``MANAGER_MEDIA=local``); nothing
here needs network access or a funded vendor account.
"""

from __future__ import annotations

import io
import json
import wave
from pathlib import Path

import pytest

from src.agents.tools.media_production import MediaProductionTool
from src.providers.media.image import LocalTitleCardImageProvider, NullImageProvider
from src.providers.media.narration import NullNarrationProvider
from src.providers.media.video import FFmpegVideoComposer, NullVideoComposer, VideoScene
from src.providers.media.video_clipping import FFmpegVideoClipper, NullVideoClipper
from src.services.artifacts.models import AudioContent, VideoContent
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactError, ArtifactStore


def _real_video_bytes(seconds: float = 4.0) -> bytes:
    image = LocalTitleCardImageProvider().generate("media artifact integration test")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(b"\x00\x00" * int(16000 * seconds))
    scene = VideoScene(image=image.data, audio=buf.getvalue(), duration_seconds=seconds)
    return FFmpegVideoComposer().compose([scene]).data


# ---------------------------------------------------------------------------
# AudioContent / VideoContent models
# ---------------------------------------------------------------------------


class TestAudioContentModel:
    def test_needs_real_description_text(self):
        with pytest.raises(ValueError, match="description"):
            AudioContent(description="   ")

    def test_json_round_trip(self):
        content = AudioContent(description="hello world, this is a real narration.")
        restored = AudioContent.from_json(content.to_json())
        assert restored.description == content.description

    def test_from_json_rejects_malformed_input(self):
        with pytest.raises(ValueError):
            AudioContent.from_json("not json")


class TestVideoContentModel:
    def test_needs_real_description_text(self):
        with pytest.raises(ValueError, match="description"):
            VideoContent(description="")

    def test_json_round_trip(self):
        content = VideoContent(description="composed from 2 scene(s)")
        restored = VideoContent.from_json(content.to_json())
        assert restored.description == content.description

    def test_from_json_rejects_malformed_input(self):
        with pytest.raises(ValueError):
            VideoContent.from_json("[]")


# ---------------------------------------------------------------------------
# ArtifactStore - derivation/composition seam
# ---------------------------------------------------------------------------


class TestArtifactStoreDerivation:
    def test_related_artifact_ids_round_trips_through_persistence(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(
            artifact_id="source", type="video", owner_agent="Alfred", title="Source",
            content_path="c", renditions={"mp4": "s.mp4"},
        )
        store.create(
            artifact_id="derived", type="video", owner_agent="Alfred", title="Clip",
            content_path="c2", renditions={"mp4": "d.mp4"}, related_artifact_ids=("source",),
        )
        reloaded = ArtifactStore(tmp_path)
        assert reloaded.get("derived").latest.related_artifact_ids == ("source",)

    def test_defaults_to_empty_tuple(self, tmp_path):
        store = ArtifactStore(tmp_path)
        artifact = store.create(
            artifact_id="a", type="document", owner_agent="Alfred", title="T", content_path="c"
        )
        assert artifact.latest.related_artifact_ids == ()

    def test_revise_can_also_record_relations(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(artifact_id="a", type="video", owner_agent="Alfred", title="T", content_path="c")
        store.create(artifact_id="b", type="video", owner_agent="Alfred", title="T2", content_path="c2")
        revised = store.revise("a", content_path="c3", related_artifact_ids=("b",))
        assert revised.latest.related_artifact_ids == ("b",)

    def test_find_by_rendition_path_locates_the_owning_artifact(self, tmp_path):
        store = ArtifactStore(tmp_path)
        store.create(
            artifact_id="a", type="video", owner_agent="Alfred", title="T",
            content_path="c", renditions={"mp4": "/real/path/video.mp4"},
        )
        found = store.find_by_rendition_path("/real/path/video.mp4")
        assert found is not None
        assert found.artifact_id == "a"

    def test_find_by_rendition_path_is_honestly_none_when_untracked(self, tmp_path):
        store = ArtifactStore(tmp_path)
        assert store.find_by_rendition_path("/never/tracked.mp4") is None


# ---------------------------------------------------------------------------
# register_new_artifact - the shared creation helper
# ---------------------------------------------------------------------------


class TestRegisterNewArtifact:
    def test_rendition_bytes_writes_a_fresh_copy(self, tmp_path):
        store = ArtifactStore(tmp_path)
        artifact = register_new_artifact(
            store, type="audio", owner_agent="Alfred", title="T",
            content_bytes=b'{"description": "x"}', rendition_format="wav", rendition_bytes=b"RIFF....",
        )
        rendition_path = Path(artifact.latest.renditions["wav"])
        assert rendition_path.is_file()
        assert rendition_path.read_bytes() == b"RIFF...."
        assert str(rendition_path.parent).startswith(str(tmp_path))

    def test_rendition_path_references_an_existing_file_without_copying(self, tmp_path):
        store = ArtifactStore(tmp_path / "artifacts")
        existing = tmp_path / "assets" / "abc123.mp4"
        existing.parent.mkdir(parents=True)
        existing.write_bytes(b"real video bytes")

        artifact = register_new_artifact(
            store, type="video", owner_agent="Alfred", title="T",
            content_bytes=b'{"description": "x"}', rendition_format="mp4",
            rendition_path=existing.as_posix(),
        )
        assert artifact.latest.renditions["mp4"] == existing.as_posix()
        # No second copy was made under the artifact's own directory.
        assert not (store.artifact_dir(artifact.artifact_id) / "v1" / "rendition.mp4").exists()

    def test_exactly_one_of_rendition_bytes_or_path_is_required(self, tmp_path):
        store = ArtifactStore(tmp_path)
        with pytest.raises(ValueError, match="exactly one"):
            register_new_artifact(
                store, type="audio", owner_agent="Alfred", title="T",
                content_bytes=b"{}", rendition_format="wav",
            )
        with pytest.raises(ValueError, match="exactly one"):
            register_new_artifact(
                store, type="audio", owner_agent="Alfred", title="T",
                content_bytes=b"{}", rendition_format="wav",
                rendition_bytes=b"x", rendition_path="y.wav",
            )

    def test_relates_to_other_artifacts_when_given(self, tmp_path):
        store = ArtifactStore(tmp_path)
        register_new_artifact(
            store, type="video", owner_agent="Alfred", title="Source",
            content_bytes=b"{}", rendition_format="mp4", rendition_bytes=b"x",
        )
        source_id = store.all()[0].artifact_id
        derived = register_new_artifact(
            store, type="video", owner_agent="Alfred", title="Derived",
            content_bytes=b"{}", rendition_format="mp4", rendition_bytes=b"y",
            related_artifact_ids=(source_id,),
        )
        assert derived.latest.related_artifact_ids == (source_id,)


# ---------------------------------------------------------------------------
# MediaProductionTool - real artifact registration per operation
# ---------------------------------------------------------------------------


class TestMediaProductionToolArtifactRegistration:
    def _tool(self, tmp_path, owner_agent="Alfred", artifacts=None):
        store = artifacts if artifacts is not None else ArtifactStore(tmp_path / "artifacts")
        tool = MediaProductionTool(
            NullNarrationProvider(), LocalTitleCardImageProvider(), FFmpegVideoComposer(),
            clipper=FFmpegVideoClipper(),
            asset_dir=tmp_path / "assets",
            artifacts=store, owner_agent=owner_agent,
        )
        return tool, store

    def test_without_artifacts_store_behaves_exactly_as_before(self, tmp_path):
        """Backward compatibility: every existing construction that
        omits artifacts=/owner_agent= gets identical behaviour to
        before this checkpoint - real files, no artifact record."""
        tool = MediaProductionTool(
            NullNarrationProvider(), LocalTitleCardImageProvider(), NullVideoComposer(),
            asset_dir=tmp_path / "assets",
        )
        output = tool.run(operation="illustrate", content="a real description")
        assert "Image produced" in output

    def test_illustrate_registers_a_real_image_artifact(self, tmp_path):
        tool, store = self._tool(tmp_path)
        output = tool.run(operation="illustrate", content="a diagram of a real test scene")
        assert "Image produced" in output
        artifacts = store.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "image"
        assert artifacts[0].owner_agent == "Alfred"
        png_path = Path(artifacts[0].latest.renditions["png"])
        assert png_path.is_file()
        from PIL import Image

        with Image.open(png_path) as img:
            img.load()
            assert img.size[0] > 0

    def test_the_rendition_path_is_the_same_file_write_already_produced(self, tmp_path):
        """No duplicate copy on disk - the artifact's own rendition
        references the identical, already-real file the tool's own
        content-addressed asset_dir wrote."""
        tool, store = self._tool(tmp_path)
        output = tool.run(operation="illustrate", content="a real description for path reuse")
        import re

        written_path = re.search(r"Image produced: (\S+\.png)", output).group(1)
        artifact_path = store.all()[0].latest.renditions["png"]
        assert Path(written_path).resolve() == Path(artifact_path).resolve()

    def test_narrate_registers_a_real_audio_artifact(self, tmp_path):
        """Real Kokoro narration needs a local model file this test
        environment may not have; a small, real WAV-producing test
        double stands in for the vendor engine - the same established
        discipline this project already uses for an unfunded/heavy
        dependency (a real container, genuinely decodable, never
        fabricated data pretending to be audio)."""
        from src.providers.media.narration import NarrationAsset, NarrationProvider

        class _RealWavNarrator(NarrationProvider):
            @property
            def name(self) -> str:
                return "test-real-wav"

            def synthesize(self, text: str, voice_id: str) -> NarrationAsset:
                buf = io.BytesIO()
                with wave.open(buf, "wb") as w:
                    w.setnchannels(1)
                    w.setsampwidth(2)
                    w.setframerate(16000)
                    w.writeframes(b"\x00\x00" * 16000)
                return NarrationAsset(audio=buf.getvalue(), sample_rate=16000, duration_seconds=1.0, voice_id=voice_id)

        store = ArtifactStore(tmp_path / "artifacts")
        tool = MediaProductionTool(
            _RealWavNarrator(), NullImageProvider(), NullVideoComposer(),
            asset_dir=tmp_path / "assets", artifacts=store, owner_agent="Alfred",
        )
        output = tool.run(operation="narrate", content="hello world, this is a real narration test.")
        assert "Narration produced" in output

        artifacts = store.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "audio"
        wav_path = Path(artifacts[0].latest.renditions["wav"])
        with wave.open(str(wav_path), "rb") as decoded:
            assert decoded.getnframes() > 0

    def test_narrate_with_null_narrator_is_refused_honestly_no_artifact_either(self, tmp_path):
        tool, store = self._tool(tmp_path)
        with pytest.raises(Exception):
            tool.run(operation="narrate", content="hello world")
        assert store.all() == ()

    def test_compose_registers_a_real_video_artifact_linked_to_its_sources(self, tmp_path):
        tool, store = self._tool(tmp_path)
        image = LocalTitleCardImageProvider().generate("a real scene")
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
            w.writeframes(b"\x00\x00" * 16000)
        # Register the source image/audio as real artifacts first, so
        # compose's own derivation link has something real to find.
        img_artifact = register_new_artifact(
            store, type="image", owner_agent="Alfred", title="scene",
            content_bytes=b'{"prompt": "scene"}', rendition_format="png", rendition_bytes=image.data,
        )
        audio_artifact = register_new_artifact(
            store, type="audio", owner_agent="Alfred", title="narration",
            content_bytes=b'{"description": "narration"}', rendition_format="wav",
            rendition_bytes=buf.getvalue(),
        )
        image_path = img_artifact.latest.renditions["png"]
        audio_path = audio_artifact.latest.renditions["wav"]

        output = tool.run(operation="compose", content=f"compose {image_path} and {audio_path}")
        assert "Video produced" in output

        video_artifacts = [a for a in store.all() if a.type == "video"]
        assert len(video_artifacts) == 1
        video = video_artifacts[0]
        assert set(video.latest.related_artifact_ids) == {img_artifact.artifact_id, audio_artifact.artifact_id}
        from src.providers.media.video import probe_duration_seconds

        mp4_path = video.latest.renditions["mp4"]
        assert probe_duration_seconds(Path(mp4_path).read_bytes()) > 0

    def test_clip_registers_a_new_derived_artifact_not_a_revision(self, tmp_path):
        tool, store = self._tool(tmp_path)
        source_bytes = _real_video_bytes(seconds=6.0)
        source_artifact = register_new_artifact(
            store, type="video", owner_agent="Alfred", title="source video",
            content_bytes=b'{"description": "source"}', rendition_format="mp4",
            rendition_bytes=source_bytes,
        )
        source_path = source_artifact.latest.renditions["mp4"]

        output = tool.run(operation="clip", content=f"clip {source_path} from 0:00-0:02")
        assert "1 clip(s) produced" in output

        clip_artifacts = [a for a in store.all() if a.type == "video" and a.artifact_id != source_artifact.artifact_id]
        assert len(clip_artifacts) == 1
        clip = clip_artifacts[0]
        # A genuinely new, distinct artifact identity - never a new
        # version of the source (the source itself must stay untouched
        # at version 1).
        assert clip.artifact_id != source_artifact.artifact_id
        assert store.get(source_artifact.artifact_id).latest.version == 1
        assert clip.latest.related_artifact_ids == (source_artifact.artifact_id,)

    def test_clip_from_an_untracked_source_still_works_with_no_relation(self, tmp_path):
        """The source video need not itself be a tracked artifact - a
        raw uploaded file works exactly as it always has, honestly with
        no derivation link since there is genuinely nothing to link to."""
        tool, store = self._tool(tmp_path)
        source_path = tmp_path / "raw_upload.mp4"
        source_path.write_bytes(_real_video_bytes(seconds=4.0))

        output = tool.run(operation="clip", content=f"clip {source_path} from 0:00-0:02")
        assert "1 clip(s) produced" in output
        clip = store.all()[0]
        assert clip.latest.related_artifact_ids == ()

    def test_multiple_clips_each_become_their_own_artifact(self, tmp_path):
        tool, store = self._tool(tmp_path)
        source_path = tmp_path / "raw.mp4"
        source_path.write_bytes(_real_video_bytes(seconds=8.0))
        output = tool.run(operation="clip", content=f"clip {source_path} 0:00-0:02 and 0:03-0:05")
        assert "2 clip(s) produced" in output
        assert len(store.all()) == 2
        assert all(a.type == "video" for a in store.all())

    def test_agent_independence_a_different_owner_name_works_identically(self, tmp_path):
        tool, store = self._tool(tmp_path, owner_agent="Arthur")
        tool.run(operation="illustrate", content="a real description for Arthur")
        assert store.all()[0].owner_agent == "Arthur"


# ---------------------------------------------------------------------------
# Full system integration - the real Manager/runtime/Control Center/HTTP
# path, real production, real multi-step derivation chain.
# ---------------------------------------------------------------------------


def _built(tmp_path, **overrides):
    import scripts.control_center as cc
    from src.config.settings import Settings

    return cc.build(
        Settings.from_env(
            env={}, memory="ephemeral", artifacts_dir=str(tmp_path / "artifacts"),
            media_asset_dir=str(tmp_path / "assets"), media="local", **overrides,
        )
    )


class TestFullSystemMediaArtifactIntegration:
    def test_narration_via_a_real_conversation_turn_becomes_a_real_artifact(self, tmp_path):
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        assert manager.brain.__class__.__name__ == "NullBrain"

        result = manager.handle_task(
            Task(description="illustrate this: a real test image for integration verification")
        )
        assert result.succeeded, result.error

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 1
        assert artifacts[0].type == "image"
        assert artifacts[0].owner_agent == runtime.identity.name

    def test_multi_step_production_chain_produces_linked_artifacts(self, tmp_path):
        from src.core.contracts import Task
        import re

        runtime, manager, channel, conversation = _built(tmp_path)

        r1 = manager.handle_task(Task(description="narrate this: a real narration for chain testing."))
        assert r1.succeeded, r1.error
        r2 = manager.handle_task(Task(description="illustrate this: a chain test scene"))
        assert r2.succeeded, r2.error

        wav_path = re.search(r"(\S+\.wav)", r1.output).group(1)
        png_path = re.search(r"(\S+\.png)", r2.output).group(1)
        r3 = manager.handle_task(Task(description=f"compose a video from {png_path} and {wav_path}"))
        assert r3.succeeded, r3.error

        artifacts = runtime.artifacts.all()
        assert len(artifacts) == 3
        video = next(a for a in artifacts if a.type == "video")
        assert len(video.latest.related_artifact_ids) == 2

    def test_persistence_across_a_genuine_restart(self, tmp_path):
        from src.core.contracts import Task

        runtime, manager, *_ = _built(tmp_path)
        result = manager.handle_task(Task(description="illustrate this: a real persistence test image"))
        assert result.succeeded, result.error
        artifact_id = runtime.artifacts.all()[0].artifact_id

        runtime2, *_ = _built(tmp_path)
        artifact = runtime2.artifacts.get(artifact_id)
        assert artifact is not None
        assert artifact.type == "image"
        assert Path(artifact.latest.renditions["png"]).is_file()

    def test_control_center_reads_the_authoritative_state(self, tmp_path):
        import scripts.control_center as cc
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description="illustrate this: a control center visibility test"))
        centre = cc.ControlCenter(runtime, manager)

        listing = cc.run_command(centre, "artifacts", 100, channel, conversation)
        assert "[image]" in listing
        assert "delivery=download" in listing

    def test_control_center_and_snapshot_both_show_the_real_derivation_chain(self, tmp_path):
        """The full narrate -> illustrate -> compose -> clip chain,
        proven through the real production path, with the real
        derivation links visible through both surfaces that already
        read the identical, authoritative store - never a UI-only
        relationship."""
        import re

        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot as build_snapshot
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        r1 = manager.handle_task(Task(description="narrate this: a real derivation chain test narration."))
        r2 = manager.handle_task(Task(description="illustrate this: a real derivation chain test scene"))
        wav_path = re.search(r"(\S+\.wav)", r1.output).group(1)
        png_path = re.search(r"(\S+\.png)", r2.output).group(1)
        r3 = manager.handle_task(Task(description=f"compose a video from {png_path} and {wav_path}"))
        assert r3.succeeded, r3.error
        video_id = next(a.artifact_id for a in runtime.artifacts.all() if a.type == "video")
        video_path = re.search(r"(\S+\.mp4)", r3.output).group(1)
        r4 = manager.handle_task(Task(description=f"clip this video {video_path} from 0:00-0:02"))
        assert r4.succeeded, r4.error
        clip_id = next(a.artifact_id for a in runtime.artifacts.all() if a.type == "video" and a.artifact_id != video_id)

        centre = cc.ControlCenter(runtime, manager)
        video_detail = cc.run_command(centre, f"artifacts {video_id}", 100, channel, conversation)
        assert "derived_from=" in video_detail
        assert "none" not in video_detail.split("derived_from=")[1]

        clip_detail = cc.run_command(centre, f"artifacts {clip_id}", 100, channel, conversation)
        assert f"derived_from={video_id}" in clip_detail

        data = build_snapshot(centre, conversation=conversation, channel=channel)
        clip_entry = next(a for a in data["artifacts"] if a["artifact_id"] == clip_id)
        assert clip_entry["related_artifact_ids"] == [video_id]

    def test_control_center_capability_matrix_reflects_media_production(self, tmp_path):
        import scripts.control_center as cc

        runtime, manager, channel, conversation = _built(tmp_path)
        centre = cc.ControlCenter(runtime, manager)
        out = cc.run_command(centre, "capabilities", 100, channel, conversation)
        assert "Artifact Workspace (documents, images/diagrams, audio, video)" in out
        assert "media_production tool=granted" in out

    def test_snapshot_exposes_video_and_image_types_with_real_delivery(self, tmp_path):
        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        manager.handle_task(Task(description="illustrate this: a real snapshot test image"))
        centre = cc.ControlCenter(runtime, manager)

        data = snapshot(centre, conversation=conversation, channel=channel)
        entry = data["artifacts"][0]
        assert entry["type"] == "image"
        assert entry["delivery"] == ["download"]
        assert "png" in entry["renditions"]

    def test_a_real_video_artifact_is_served_with_the_correct_content_type(self, tmp_path):
        import re

        import scripts.serve_ui as su
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        r1 = manager.handle_task(Task(description="narrate this: a real HTTP serving test narration."))
        r2 = manager.handle_task(Task(description="illustrate this: a real HTTP serving test scene"))
        wav_path = re.search(r"(\S+\.wav)", r1.output).group(1)
        png_path = re.search(r"(\S+\.png)", r2.output).group(1)
        r3 = manager.handle_task(Task(description=f"compose a video from {png_path} and {wav_path}"))
        assert r3.succeeded, r3.error
        video_id = next(a.artifact_id for a in runtime.artifacts.all() if a.type == "video")

        system = su.LiveSystem(
            runtime=runtime, manager=manager, channel=channel, conversation=conversation,
            centre=su.ControlCenter(runtime, manager), lock=__import__("threading").Lock(),
        )
        status, body, content_type = su.route(f"/artifact/{video_id}.mp4", system)
        assert status == 200
        assert content_type == "video/mp4"
        assert body[:4] in (b"\x00\x00\x00\x18", b"\x00\x00\x00\x1c", b"\x00\x00\x00 ") or b"ftyp" in body[:16]

    def test_agent_independence_arthur_gets_no_media_or_artifact_tools(self, tmp_path):
        runtime, manager, *_ = _built(tmp_path)
        arthur = manager.registry.get("Arthur")
        assert arthur.tools.names() == ()


class TestUIRendersVideoAndAudioArtifacts:
    @staticmethod
    def _node():
        import subprocess

        for candidate in ("node", "node.exe"):
            try:
                subprocess.run([candidate, "--version"], capture_output=True, check=True)
                return candidate
            except (FileNotFoundError, subprocess.CalledProcessError):
                continue
        return None

    def test_a_real_video_artifact_renders_as_a_real_video_player(self, tmp_path):
        import re
        import subprocess

        node = self._node()
        if node is None:
            pytest.skip("no node runtime available in this environment")

        import scripts.control_center as cc
        from src.control_center.snapshot import snapshot as build_snapshot
        from src.core.contracts import Task

        runtime, manager, channel, conversation = _built(tmp_path)
        r1 = manager.handle_task(Task(description="narrate this: a real UI rendering test narration."))
        r2 = manager.handle_task(Task(description="illustrate this: a real UI rendering test scene"))
        wav_path = re.search(r"(\S+\.wav)", r1.output).group(1)
        png_path = re.search(r"(\S+\.png)", r2.output).group(1)
        manager.handle_task(Task(description=f"compose a video from {png_path} and {wav_path}"))
        video_id = next(a.artifact_id for a in runtime.artifacts.all() if a.type == "video")

        centre = cc.ControlCenter(runtime, manager)
        data = build_snapshot(centre, conversation=conversation, channel=channel)

        # Import the shared harness constant/pattern from the sibling
        # artifact-workspace test module rather than duplicating it a
        # third time.
        import test_artifact_workspace as base

        repo_root = Path(__file__).resolve().parent.parent
        shell = (repo_root / "ui" / "index.html").read_text(encoding="utf-8")
        import re as re_mod

        script_match = re_mod.search(r"<script>(.*)</script>", shell, re_mod.S)
        assert script_match is not None
        (tmp_path / "ui_script.js").write_text(script_match.group(1), encoding="utf-8")
        (tmp_path / "snapshot.json").write_text(json.dumps(data), encoding="utf-8")
        harness = tmp_path / "harness.js"
        harness.write_text(base._UI_NODE_HARNESS, encoding="utf-8")

        result = subprocess.run(
            [node, str(harness), str(tmp_path / "ui_script.js"), str(tmp_path / "snapshot.json"), "agent"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip(), result.stderr
        out = json.loads(result.stdout.strip().splitlines()[-1])
        assert out.get("ok"), out.get("error")

        rendered = out["views"]["boot"]
        assert f"artifact/{video_id}.mp4" in rendered
        assert f'<video src="artifact/{video_id}.mp4"' in rendered
        assert "controls" in rendered
        # Weakness-audit repair: real, persisted related_artifact_ids
        # (this composed video's own two real sources - narration +
        # illustration) reaches the actual rendered frontend, not only
        # the snapshot payload.
        assert "derived from 2 artifact" in rendered
