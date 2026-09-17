"""Whole-ecosystem closure campaign 2 - genuine 3D content for arbitrary subjects.

A subject described as parts (by the person, or restated by a Brain in the same
grammar) is assembled into a real glTF 2.0 binary mesh and read back from its
bytes by ``inspect_glb``; only a valid, three-dimensional result is saved as a
``model3d`` artifact. No subject is known to the code. The served run with
fresh subjects and the browser render are recorded in REQUIREMENT_LEDGER.md.
"""

from __future__ import annotations

import struct

import numpy as np
import pytest

import scripts.control_center as cc
import scripts.serve_ui as su
from scripts.exchange import exchange
from src.agents.tools import model3d_production
from src.config.settings import Settings
from src.core.contracts import Task
from src.manager.brain import BrainAction, BrainDecision
from src.manager.capabilities import CREATE_3D_MODEL, MANAGER_CAPABILITIES, create_3d_model_intent, select_capability
from src.providers.media.model3d import (
    SHAPES,
    Model3DError,
    Part,
    assemble_glb,
    inspect_glb,
    parse_part_description,
    world_bounds,
)


class TestTheGlbIsRealAndIndependentlyChecked:
    def test_every_shape_meshes_to_a_closed_valid_3d_solid(self):
        for name, shape in SHAPES.items():
            glb = assemble_glb([Part(name, name, dict(shape.defaults))], title=name)
            report = inspect_glb(glb)
            assert report.valid and report.genuinely_3d and report.triangles > 0, (name, report)
            low, high = world_bounds(Part(name, name, dict(shape.defaults)))
            assert np.allclose(report.bounds_min, low, atol=1e-3) and np.allclose(report.bounds_max, high, atol=1e-3), name

    def test_node_transforms_are_honoured_when_reading_back(self):
        part = Part("tilted", "box", {"width": 4.0, "height": 1.0, "depth": 1.0}, rotation=[0.0, 0.0, 90.0], position=[1.0, 2.0, 3.0])
        report = inspect_glb(assemble_glb([part]))
        assert np.allclose(report.extent, (1.0, 4.0, 1.0), atol=1e-4)
        assert np.allclose(report.bounds_min, (0.5, 0.0, 2.5), atol=1e-4)

    def test_flat_malformed_and_truncated_files_are_not_3d(self):
        flat = inspect_glb(assemble_glb([Part("sheet", "box", {"width": 2.0, "height": 3.0, "depth": 0.001})]))
        assert flat.valid and not flat.genuinely_3d
        good = assemble_glb([Part("ball", "sphere", {"radius": 1.0})])
        assert not inspect_glb(good[:-40]).valid
        assert not inspect_glb(b"glTF" + b"\x00" * 30).valid
        broken = bytearray(good)
        struct.pack_into("<I", broken, 4, 1)
        assert not inspect_glb(bytes(broken)).valid

    def test_invalid_parts_are_refused_before_writing(self):
        with pytest.raises(Model3DError, match="positive"):
            assemble_glb([Part("x", "sphere", {"radius": -1.0})])
        with pytest.raises(Model3DError, match="no shape"):
            assemble_glb([Part("x", "hexagon", {})])
        with pytest.raises(Model3DError, match="no parts"):
            assemble_glb([])


class TestDescriptionsBecomeParts:
    def test_dimensions_counts_colours_and_relations_are_read(self):
        described = parse_part_description(
            "a 3D bench: a teal box seat 3x0.15x0.8, four dimgray cylinder legs radius 0.05 height 0.9 at the corners of the seat, "
            "and a teal box backrest 3x0.8x0.1 behind the seat"
        )
        assert described.not_understood == ()
        names = [p.name for p in described.parts]
        assert names == ["seat", "legs 1", "legs 2", "legs 3", "legs 4", "backrest"]
        seat, leg, back = described.parts[0], described.parts[1], described.parts[-1]
        assert seat.dimensions == {"width": 3.0, "height": 0.15, "depth": 0.8} and seat.colour == (0, 128, 128)
        assert world_bounds(leg)[1][1] == pytest.approx(world_bounds(seat)[0][1])  # legs meet the underside
        assert world_bounds(back)[1][2] == pytest.approx(world_bounds(seat)[0][2])  # backrest behind the seat

    def test_one_part_around_another_encircles_it_and_many_are_spaced(self):
        ringed = parse_part_description("x: a grey sphere core radius 1, a gold torus band radius 1.5 tube 0.1 around the core").parts
        assert ringed[1].position == pytest.approx(ringed[0].position)
        orbit = parse_part_description("x: a grey sphere core radius 1, six white sphere moons radius 0.1 around the core").parts[1:]
        radii = {round(float(np.hypot(m.position[0], m.position[2])), 4) for m in orbit}
        assert len(orbit) == 6 and len(radii) == 1

    def test_what_is_not_geometry_is_reported(self):
        described = parse_part_description("a 3D fountain: a grey cylinder basin radius 2 height 0.5, and some water splashing everywhere")
        assert [p.name for p in described.parts] == ["basin"] and described.not_understood == ("some water splashing everywhere",)


class TestRecognition:
    @pytest.mark.parametrize("text", ["Make a 3D model of a teapot", "could you sculpt a giraffe in 3D?", "Create a 3D bookshelf"])
    def test_3d_requests_reach_the_capability(self, text):
        assert create_3d_model_intent(Task(description=text))
        assert select_capability(Task(description=text), MANAGER_CAPABILITIES) is CREATE_3D_MODEL

    @pytest.mark.parametrize("text", ["I watched a 3D film yesterday", "What is a 3D model?", "Make a diagram of a pump"])
    def test_other_requests_do_not(self, text):
        assert not create_3d_model_intent(Task(description=text))


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


def _say(system, text):
    return exchange(system.channel, system.conversation, text)


def _models(system):
    return [a for a in system.runtime.artifacts.all() if a.type == "model3d"]


class TestThroughTheServedSystem:
    def test_described_parts_become_a_served_checked_glb(self, system):
        turn = _say(system, "Make a 3D model of a birdhouse: a tan box house 1x1x1, a brown cone roof radius 0.8 height 0.6 on top of the house")
        assert "type=model3d" in turn.outcome
        (model,) = _models(system)
        status, body, content_type = su.route(f"/artifact/{model.artifact_id}.glb", system)
        assert status == 200 and content_type == "model/gltf-binary"
        report = inspect_glb(body)
        assert report.genuinely_3d and report.meshes == 2
        assert model.latest.metadata["generator"] == "part_assembly"

    def test_a_bare_subject_without_a_model_is_refused_and_nothing_saved(self, system):
        turn = _say(system, "Create a 3D teapot")
        assert "no reasoning model worked them out" in turn.outcome and "Shapes:" in turn.outcome
        assert _models(system) == []

    def test_a_brain_restates_a_subject_as_parts_and_the_result_is_checked(self, system):
        asked = []

        def brain(task, notes):
            asked.append(notes)
            return BrainDecision(BrainAction.HANDLE_SELF,
                                 "a white box body 2x1x1 ; a black cylinder chimney radius 0.15 height 0.6 on top of the body",
                                 source="scripted_brain")

        system.manager.own_skills._converse = brain
        turn = _say(system, "Create a 3D cottage")
        assert "type=model3d" in turn.outcome and "Break 'cottage'" in asked[0][0]
        (model,) = _models(system)
        assert inspect_glb(open(model.latest.renditions["glb"], "rb").read()).meshes == 2

    def test_a_brain_answer_that_is_not_buildable_saves_nothing(self, system):
        system.manager.own_skills._converse = lambda task, notes: BrainDecision(
            BrainAction.HANDLE_SELF, "a nice cosy cottage with smoke", source="scripted_brain")
        turn = _say(system, "Create a 3D cottage")
        assert "weren't all buildable" in turn.outcome and _models(system) == []

    def test_another_generator_plugs_in_and_passes_the_same_gate(self, system, monkeypatch):
        good = assemble_glb([Part("blob", "sphere", {"radius": 1.0})])
        flat = assemble_glb([Part("card", "box", {"width": 1.0, "height": 1.0, "depth": 0.0001})])
        monkeypatch.setattr(model3d_production, "_GENERATORS", {"vendor": lambda subject: good})
        assert "type=model3d" in _say(system, "Create a 3D jellyfish").outcome
        monkeypatch.setattr(model3d_production, "_GENERATORS", {"vendor": lambda subject: flat})
        assert "not genuine 3D" in _say(system, "Create a 3D postcard").outcome
        assert len(_models(system)) == 1
