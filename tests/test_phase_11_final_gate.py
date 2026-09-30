"""Phase 11 final gate - generalized goal -> work -> result, verified with the
real Manager (``handle_task``, the path a conversation turn takes).

Each class is one defect the gate reproduced at runtime and the generalized
repair that closed it - never a handler for an example sentence:

* Construction routing: "design an interactive diagram of ...", "draw me a
  labelled map showing ...", "design an interactive 3D diagram of ..." never
  reached the capabilities that make them (a fixed phrase list). Repaired with
  one "verb + descriptors + noun" shape shared by every construction request.
* False completion: a diagram with no image produced was reported completed.
* A pending clarification captured every later request (found with the real
  local model): a request a capability claims now drops a stale question.
* Echoed "clarifications" from a model are not put to the person.
* 3D content could not move. Motion is now an open registry of geometry over
  time, written as real glTF animations and read back by the inspector.

Subjects are deliberately unseen elsewhere (a lighthouse, a kite, a paper
boat, a comet) - the mechanisms know none of them.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

import scripts.control_center as cc
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.activity_session import PendingClarification
from src.manager.capabilities import create_3d_model_intent, create_diagram_intent, subject_without_connective
from src.manager.guided_interaction import _is_a_real_question
from src.providers.media import model3d
from src.providers.media.model3d import (
    MOTIONS,
    Model3DError,
    Motion,
    assemble_glb,
    inspect_glb,
    motion_in,
    parse_part_description,
    register_motion,
)


def _half_brain(tmp_path):
    runtime, manager, channel, conversation = cc.build(Settings.from_env(
        env={}, memory="ephemeral", profile_path="does-not-exist-USER.md",
        artifacts_dir=str(tmp_path / "artifacts"), media_asset_dir=str(tmp_path / "assets"),
        knowledge_dir=str(tmp_path / "knowledge"), documents_dir=str(tmp_path / "docs"),
    ))
    return runtime, manager


def _task(text: str) -> Task:
    return Task(description=text)


class TestConstructionRequestsReachTheirCapability:
    @pytest.mark.parametrize("text", [
        "Design an interactive diagram of a lighthouse at dusk.",
        "Draw me a labelled map showing the paper boat's route.",
        "Please sketch a detailed diagram about how a kite flies.",
    ])
    def test_verb_descriptors_and_noun_reach_the_diagram_capability(self, text):
        assert create_diagram_intent(_task(text))

    @pytest.mark.parametrize("text", [
        "Design an interactive 3D diagram of a comet passing a moon.",
        "Create an animated, detailed 3D model of a kite.",
        "Build me an interactive, animated 3D lighthouse.",
    ])
    def test_three_dimensional_requests_go_to_3d_never_to_a_flat_image(self, text):
        assert create_3d_model_intent(_task(text))
        assert not create_diagram_intent(_task(text))

    def test_unrelated_sentences_are_not_construction_requests(self):
        for text in ("I love the diagram you made yesterday", "what is a map projection?", "the kite flew away"):
            assert not create_diagram_intent(_task(text))

    def test_naming_a_workspace_is_not_asking_for_a_model(self):
        """Found by the full regression: "3D diagram" is also a workspace's name."""
        for text in ("go to the 3D diagram workspace", "which workspace are you in, the 3D diagram one?"):
            assert not create_3d_model_intent(_task(text)), text
            assert not create_diagram_intent(_task(text)), text

    def test_the_subject_loses_its_connective(self):
        assert subject_without_connective("of a lighthouse at dusk.") == "a lighthouse at dusk"
        assert subject_without_connective("showing the paper boat's route") == "the paper boat's route"


class TestAVisualIsNeverReportedDoneWithoutAnImage:
    def test_half_brain_keeps_the_description_but_does_not_claim_completion(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        result = manager.handle_task(_task("Design an interactive diagram of a lighthouse at dusk."))
        assert not result.succeeded
        assert "No image was produced, so the diagram is not done" in result.error
        content = next((tmp_path / "artifacts").rglob("content.json"))
        assert json.loads(content.read_text(encoding="utf-8")) == {"prompt": "a lighthouse at dusk"}


class TestAStaleClarificationNeverCapturesANewRequest:
    def _raise(self, manager):
        sessions = manager.own_skills._activity_sessions
        sessions.raise_clarification(manager.own_skills.name, PendingClarification(
            question="Which city did you mean?", candidates=("Lisbon", "Porto")))
        return sessions

    def test_a_claimed_request_drops_the_question_and_is_handled_on_its_own(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        sessions = self._raise(manager)
        result = manager.handle_task(_task("Create a workspace."))
        assert "Control Center" in (result.output or result.error)
        assert sessions.pending_clarification(manager.own_skills.name) is None

    def test_an_answer_still_resolves_it(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        sessions = self._raise(manager)
        result = manager.handle_task(_task("Porto"))
        assert result.succeeded and "Porto" in result.output
        assert sessions.pending_clarification(manager.own_skills.name) is None

    def test_ordinary_talk_leaves_it_waiting(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        sessions = self._raise(manager)
        manager.handle_task(_task("hmm, give me a second"))
        assert sessions.pending_clarification(manager.own_skills.name) is not None


class TestAModelsClarificationMustBeARealQuestion:
    def test_an_echo_or_a_statement_is_not_put_to_the_person(self):
        assert not _is_a_real_question("Build me a webpage.", "Build me a webpage.")
        assert not _is_a_real_question("Build me a webpage?", "build me a webpage")
        assert not _is_a_real_question("Change your _______.", "Change your voice.")
        assert _is_a_real_question("Which page do you mean - the draft or the live one?", "Build me a webpage.")
        # found with the real local model: a near-restatement asks nothing new
        assert not _is_a_real_question("What do the documents say about the Quillmoor project?",
                                       "What do my documents say about the Quillmoor project?")


class TestNamingACandidateIsNotChoosingIt:
    """Found with the real local model: "Summarise the Quillmoor project history
    from my documents" named a pending candidate, was taken as the answer, never
    performed - and the false choice was remembered as a fact about the person."""

    def test_a_new_request_mentioning_a_candidate_is_not_an_answer(self):
        pending = PendingClarification(question="Which?", candidates=("the Quillmoor project", "the documents"))
        assert pending.select_candidate("Summarise the Quillmoor project history from my documents.") is None
        assert pending.select_candidate("book me a flight to the documents office next week please") is None

    def test_real_answers_still_choose(self):
        pending = PendingClarification(question="Which city?", candidates=("Lisbon", "Porto"))
        for reply in ("Porto", "I meant Porto", "let's go with the second one, Porto please"):
            assert pending.select_candidate(reply) == "Porto", reply


class TestAHandoutNeedsNoBrainWhenTheRequestCarriesItsContent:
    """Phase 11 closure replaced what this class used to check.

    It used to assert that a Half Brain decline offered the ``TITLE:``/
    ``SECTION:`` block. That block is the *Brain's* own answer format, and the
    only structure the handler could read without a model - so Half Brain both
    failed at a request that carried everything needed, and printed an
    internal, model-facing instruction at the person as though it were
    something to type. Both are now wrong to assert: the request below is
    answered deterministically, and a genuine decline names the human shape.
    """

    def test_a_named_and_filled_request_is_built_with_no_model_at_all(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        result = manager.handle_task(_task(
            "Create a handout titled Kites with the sections: How they fly; Where to fly them."))
        assert result.succeeded, result.error
        assert "Kites" in result.output

    def test_a_decline_names_the_human_shape_never_the_model_facing_one(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        # Nothing to go on: no title, no entries - a real decline.
        result = manager.handle_task(_task("Make me a handout"))
        assert not result.succeeded
        assert "TITLE:" not in result.error and "SECTION:" not in result.error
        assert "titled" in result.error and "with:" in result.error


class TestAWorkspaceRefusalNamesOnlyWhatIsWrong:
    def test_only_the_bad_panel_is_named_with_the_reason(self):
        from src.control_center.workspace_catalog import WorkspaceType

        with pytest.raises(ValueError) as refused:
            WorkspaceType("probeglaze", "Probe Glaze", "", ("model3d", "artifacts"))
        assert "('artifacts',)" in str(refused.value) and "model3d" not in str(refused.value)
        assert "artifacts:image" in str(refused.value)
        assert WorkspaceType("probeglaze", "Probe Glaze", "", ("model3d", "artifacts:image")).panels == ("model3d", "artifacts:image")


def _glb_json(data: bytes) -> dict:
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20:20 + length])


class TestMotionIsAnOpenRegistryOfGeometryOverTime:
    PARTS = ("a lighthouse: 1 white cylinder tower radius 1 height 6 ; "
             "1 yellow sphere lamp radius 0.7 on top of the tower spinning")

    def test_a_parts_clause_gives_it_motion_and_the_file_carries_it(self):
        described = parse_part_description(self.PARTS)
        assert {p.name: p.motion for p in described.parts}["lamp"] == "spin"
        data = assemble_glb(described.parts, title="lighthouse", motion="bob")
        report = inspect_glb(data)
        assert report.genuinely_3d and report.animated_channels == 2 and not report.problems
        channels = _glb_json(data)["animations"][0]["channels"]
        assert sorted(c["target"]["path"] for c in channels) == ["rotation", "translation"]

    def test_the_whole_subject_motion_comes_from_the_request_words(self):
        assert motion_in("a paper boat drifting across a pond") == "drift"
        assert motion_in("a comet") is None

    def test_a_thing_named_like_a_verb_does_not_move(self):
        """Found writing this gate: a part named "sail" drifted. A sail, a rock,
        a wave, a swing and a circle are things; "sailing", "rocking" move."""
        for thing in ("a rock", "a wave", "a sail", "a circle", "a swing"):
            assert motion_in(thing) is None, thing
        described = parse_part_description("x: 1 white cone sail radius 1 height 2 ; 1 grey sphere rock radius 1 right of the sail")
        assert [p.motion for p in described.parts] == [None, None]
        assert motion_in("a boat sailing") == "drift" and motion_in("a rock rocking") == "sway"
        # a thing whose *name* is an -ing word ("a rocking chair", "a spinning
        # top") is still only a thing - its name is never read as its motion
        named = parse_part_description("x: 1 brown box rocking chair 1 x 1 x 1 ; 1 grey cylinder spinning top radius 0.5 height 1")
        assert [(p.name, p.motion) for p in named.parts] == [("rocking chair", None), ("spinning top", None)]

    def test_an_unknown_motion_is_refused(self):
        described = parse_part_description(self.PARTS)
        with pytest.raises(Model3DError, match="no motion called"):
            assemble_glb(described.parts, motion="teleport")

    def test_a_new_motion_joins_with_no_source_change(self):
        register_motion(Motion("zigzag", r"zigzag(?:s|ging)?", "translation",
                               lambda t, size: (0.3 * size * (1 if int(t * 8) % 2 else -1), 0.0, 0.0), ("zigzagging",)))
        try:
            described = parse_part_description("a kite: 1 red box sail 2 x 2 x 0.1 zigzagging")
            assert described.parts[0].motion == "zigzag"
            assert inspect_glb(assemble_glb(described.parts)).animated_channels == 1
            assert "zigzag (zigzagging)" in model3d.shape_grammar()
        finally:
            MOTIONS.pop("zigzag", None)

    def test_the_inspector_rejects_motion_that_does_not_add_up(self):
        described = parse_part_description(self.PARTS)
        data = bytearray(assemble_glb(described.parts))
        document = _glb_json(bytes(data))
        document["animations"][0]["channels"][0]["target"]["node"] = 99
        body = json.dumps(document, separators=(",", ":")).encode()
        body += b" " * ((4 - len(body) % 4) % 4)
        old_length = struct.unpack_from("<I", data, 12)[0]
        rebuilt = bytes(data[:12]) + struct.pack("<II", len(body), 0x4E4F534A) + body + bytes(data[20 + old_length:])
        rebuilt = rebuilt[:8] + struct.pack("<I", len(rebuilt)) + rebuilt[12:]
        report = inspect_glb(rebuilt)
        assert not report.valid and any("does not exist" in p for p in report.problems)

    def test_half_brain_builds_a_moving_model_end_to_end(self, tmp_path):
        runtime, manager = _half_brain(tmp_path)
        result = manager.handle_task(_task(
            "Make a 3D model of a paper boat drifting: 1 white box hull 3 x 0.6 x 1.2 ; "
            "1 white cone sail radius 0.6 height 1.5 on top of the hull swaying"))
        assert result.succeeded, result.error
        assert result.payload["animated_channels"] == 2
        assert "the whole a paper boat drifts" not in result.output
        glb = next((tmp_path / "artifacts").rglob("*.glb"))
        report = inspect_glb(Path(glb).read_bytes())
        assert report.genuinely_3d and report.animated_channels == 2
