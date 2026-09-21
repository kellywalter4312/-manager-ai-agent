"""Defects found by the 2026-09-21 runtime re-verification of the closure
items, each repaired at its source and held here with fresh phrasings:

1. clipping: "cut seconds 2 to 4 out of that video" was not recognised - the
   filter that keeps "5 to 10 miles" from becoming a clip read "2 to 4 out"
   as a range of *outs*;
2. 3D: a part placed "on top of it" / "on top of that" was silently laid
   beside the previous part and reported as built as asked;
3. workspaces: a scoped workspace refused the agent's own personal memory
   ("what is my goal?");
4. routing: "read my latest recording to me" went to live information, and
   "an interactive 3D view of ..." to open conversation;
5. recordings: a transcript was shown once and thrown away, so nothing could
   later read, quote or answer from a recording's words;
6. workspaces could only be composed from five fixed panel kinds - an agent's
   own screens (the open, registered surfaces) could not be a panel.
"""

from __future__ import annotations

import json

import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.agents.tools.artifact_production import ArtifactProductionTool
from src.agents.tools.tool import Tool, ToolSpec
from src.agents.tools.toolset import ToolSet
from src.config.settings import Settings
from src.core.contracts import Task
from src.control_center.workspace_catalog import WorkspaceType
from src.manager.capabilities import (
    MANAGER_CAPABILITIES,
    clip_video_intent,
    create_3d_model_intent,
    select_capability,
)
from src.manager.knowledge import recording_transcript_source, resolve_content_sources
from src.providers.documents.pdf import NullDocumentRenderer
from src.providers.media.image import NullImageProvider
from src.providers.media.model3d import parse_part_description
from src.services.artifacts.registration import register_new_artifact
from src.services.artifacts.store import ArtifactStore


# ---------------------------------------------------------------------------
# 1. clipping, both directions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Cut seconds 2 to 4 out of that video for me",
    "Cut seconds 2 to 4 from that video for me",
    "Trim minutes 1 to 2 from the footage please",
    "Please cut 3 to 7 seconds out of the video",
    "Could you clip the video between second 5 and second 9 for me?",
])
def test_a_genuine_clip_request_is_recognised(text):
    assert clip_video_intent(Task(description=text))


@pytest.mark.parametrize("text", [
    "I ran 5 to 10 miles, then watched a video clip",
    "My niece showed me a video clip from 2 to 4 years ago and we laughed all evening",
    "between 20 and 30 people watched the video, can you clip it",
    "I cut 2 to 4 onions while the cooking video played",
    "That video clip of the harbour was lovely, thanks",
])
def test_talk_about_a_video_clip_is_not_a_clip_request(text):
    assert not clip_video_intent(Task(description=text))


# ---------------------------------------------------------------------------
# 2. 3D placement by pronoun
# ---------------------------------------------------------------------------

def _parts(text):
    return {p.name: p for p in parse_part_description(text).parts}


def test_on_top_of_it_stacks_on_the_part_described_just_before():
    parts = _parts("a snowman: a big white sphere body, a smaller white sphere head on top of it, "
                   "and an orange cone nose in front of the head")
    assert parts["head"].position[1] > parts["body"].position[1]
    assert parts["head"].note == "on top body"
    assert parts["nose"].position[2] > parts["head"].position[2]


def test_that_and_this_one_refer_back_too():
    parts = _parts("a totem: a red box, a blue box on top of that one, a green box on top of this one")
    heights = [p.position[1] for p in parse_part_description(
        "a totem: a red box, a blue box on top of that one, a green box on top of this one").parts]
    assert heights == sorted(heights) and len(set(heights)) == 3
    assert not any(p.note.startswith("no position given") for p in parts.values())


def test_a_relation_word_is_never_taken_as_a_part_name():
    names = [p.name for p in parse_part_description("dice: a white cube; a white cube beside it").parts]
    assert "beside it" not in names and len(names) == 2


# ---------------------------------------------------------------------------
# 4. routing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "Show me an interactive 3D view of a water molecule I can rotate",
    "Build an interactive 3D map of a harbour town with day and night modes",
    "Make a 3D visualisation of a wind turbine",
])
def test_a_3d_view_or_map_is_a_3d_request(text):
    assert create_3d_model_intent(Task(description=text))


@pytest.mark.parametrize("text,expected", [
    ("Read my latest recording to me", "read_content"),
    ("What decisions were made in my recording?", "knowledge"),
    ("Summarize my latest recording", "knowledge"),
    ("list my recordings", "list_recordings"),
    ("transcribe my recording", "transcribe_media"),
    ("Start recording for the budget review", "start_recording"),
    # acting on a recording is not a question about it (caught by the full suite)
    ("Give me Japanese captions for this recording.", "translate_media"),
])
def test_recording_requests_reach_the_capability_that_owns_them(text, expected):
    assert select_capability(Task(description=text), MANAGER_CAPABILITIES).name == expected


def test_live_information_still_answers_the_present():
    assert select_capability(Task(description="What's the latest news on the election?"), MANAGER_CAPABILITIES).name == "live_information"


# ---------------------------------------------------------------------------
# 5. a recording's transcript is kept and read like a document
# ---------------------------------------------------------------------------

class _Transcriber(Tool):
    """A transcription engine at the tool boundary: returns timed lines."""

    def __init__(self, lines: str) -> None:
        self.calls = 0
        self._lines = lines

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="media_production", description="scripted", schema={"operation": str, "content": str})

    def run(self, **arguments):
        assert arguments["operation"] == "transcribe"
        self.calls += 1
        return self._lines


def _store_with_recordings(tmp_path):
    store = ArtifactStore(tmp_path / "artifacts")
    for purpose in ("harvest planning", "vendor interview"):
        register_new_artifact(
            store, type="voice_recording", owner_agent="Alfred", title=f"Voice recording ({purpose})",
            content_bytes=b"{}", rendition_format="wav", rendition_bytes=b"RIFF....",
            metadata={"purpose": purpose},
        )
    return store


def test_the_named_recording_is_transcribed_once_and_then_read_as_a_document(tmp_path):
    store = _store_with_recordings(tmp_path)
    transcriber = _Transcriber("0:00-0:03: We agreed the vendor starts in May\n0:03-0:06: Priya owns the contract")
    tools = ToolSet([ArtifactProductionTool(store, NullDocumentRenderer(), NullImageProvider(), owner_agent="Alfred"),
                     transcriber])
    found, reason = recording_transcript_source("what was decided in my vendor interview recording?", tools)
    assert reason == "" and found is not None
    payload, units = found
    assert payload["title"] == "Transcript - Voice recording (vendor interview)"
    assert [u.text for u in units][0].endswith("We agreed the vendor starts in May")
    # asked again: the kept transcript is read, not transcribed a second time
    again, _ = recording_transcript_source("read my vendor interview recording", tools)
    assert again is not None and transcriber.calls == 1
    # and the ordinary document resolution reaches it for any content question
    sources = resolve_content_sources("what does my vendor interview recording say?", tools=tools)
    assert sources and sources[0][0]["artifact_id"] == payload["artifact_id"]


def test_no_transcription_tool_is_said_plainly(tmp_path):
    store = _store_with_recordings(tmp_path)
    tools = ToolSet([ArtifactProductionTool(store, NullDocumentRenderer(), NullImageProvider(), owner_agent="Alfred")])
    found, reason = recording_transcript_source("what was decided in my recording?", tools)
    assert found is None and "no transcription tool" in reason


# ---------------------------------------------------------------------------
# 3 and 6. workspaces, through the real served system
# ---------------------------------------------------------------------------

@pytest.fixture
def system(tmp_path, monkeypatch):
    for name, value in (
        ("DEFAULT_ACCOUNTS_PATH", tmp_path / "accounts.json"), ("DEFAULT_CONFIG_PATH", tmp_path / "config.json"),
        ("DEFAULT_LEDGER_PATH", tmp_path / "state.json"), ("DEFAULT_STAGING_DIR", tmp_path / "staging"),
        ("DEFAULT_BACKUPS_DIR", tmp_path / "backups"),
    ):
        monkeypatch.setattr(cc, name, value)
    return su.build_live_system(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))


def _cmd(system, command):
    return cc.run_command(system.centre, command, 100, system.channel, system.conversation)


def _say(system, text):
    return exchange(system.channel, system.conversation, text)


def test_personal_memory_is_answered_inside_a_scoped_workspace(system):
    _say(system, "My goal is to open a bakery by next summer.")
    _cmd(system, "workspace create seedbank Seed bank|tasks|create_task,list_tasks")
    assert "Seed bank" in _say(system, "Go to the seed bank workspace").outcome
    assert "bakery" in _say(system, "What is my goal?").outcome
    taught = _say(system, "My sister's name is Wanjiru.")
    assert "isn't work for" not in taught.outcome
    # ordinary work outside the profile is still scoped
    assert "isn't work for" in _say(system, "Make a 3D model of a crate: 1 brown box crate 1 x 1 x 1").outcome


def test_an_agent_screen_is_a_workspace_panel(system):
    out = _cmd(system, "workspace create inbox_desk Inbox desk|surface:Arthur/mailbox,tasks")
    assert "Created the workspace 'inbox_desk'" in out
    assert WorkspaceType("any", "Any", "", ("surface:opportunities",)).panels == ("surface:opportunities",)
    with pytest.raises(ValueError):
        WorkspaceType("bare", "Bare", "", ("surface",))
    page = (su.Path(su.__file__).resolve().parent.parent / "ui" / "index.html").read_text(encoding="utf-8")
    assert 'if(kind === "surface")' in page and "surfaceBody(named, screen)" in page
