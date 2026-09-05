"""Stage 6 — the Upgrade Manifest: what version of Alfred OS is installed.

Not the migration engine (src/core/versioning.py, untouched by this
milestone), not an update mechanism, not agent identity. A small,
read-only description of real, already-existing compatibility contracts:
SYSTEM_VERSION, SNAPSHOT_VERSION, the two persisted-store SCHEMA_VERSIONs,
and the Visual Environment Registry's own {id, version} declarations, read
out of ui/index.html rather than declared a second time.
"""

from __future__ import annotations

import inspect

import pytest

from src.control_center.manifest import (
    UpgradeManifest,
    VisualEnvironmentVersion,
    upgrade_manifest,
    visual_environment_versions,
)
from src.control_center.snapshot import SNAPSHOT_VERSION
from src.core.versioning import SYSTEM_VERSION
from src.heartbeat.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION
from src.services.memory.persistent import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION

REAL_UI_PATH = "ui/index.html"

SYNTHETIC_REGISTRY = """
  const AGENT_VISUAL_ENVIRONMENTS = {
    core:    { id: "test-core-emblem", version: 3, build: buildManagerEmblem },
    polygon: { id: "test-wing-emblem", version: 2, build: buildSpecialistEmblem },
  };
  const BRAIN_VISUAL_ENVIRONMENT  = { id: "test-brain-lattice", version: 1, build: buildBrainGeometry };
  const MEMORY_VISUAL_ENVIRONMENT = { id: "test-memory-core",   version: 5, build: buildMemoryGeometry };
  const ROOM_VISUAL_ENVIRONMENT   = { id: "test-room-core",     version: 1, build: buildRoomCore };
"""


# ---------------------------------------------------------------------------
# 1. One authoritative system version
# ---------------------------------------------------------------------------


class TestOneAuthoritativeSystemVersion:
    def test_the_manifest_reports_the_real_system_version(self):
        manifest = upgrade_manifest(snapshot_version=1)
        assert manifest.system_version == SYSTEM_VERSION

    def test_there_is_exactly_one_system_version_field(self):
        """Not a list, not a dict of candidates - one authoritative value."""
        fields = {f for f in UpgradeManifest.__dataclass_fields__}
        assert sum(1 for f in fields if "system_version" in f) == 1


# ---------------------------------------------------------------------------
# 2. SYSTEM_VERSION stays distinct from SNAPSHOT_VERSION
# ---------------------------------------------------------------------------


class TestSystemVersionStaysDistinctFromSnapshotVersion:
    def test_they_are_different_fields_with_different_types(self):
        manifest = upgrade_manifest(snapshot_version=SNAPSHOT_VERSION)
        assert isinstance(manifest.system_version, str)
        assert isinstance(manifest.snapshot_version, int)

    def test_snapshot_version_is_supplied_by_the_caller_not_guessed(self):
        """Deliberately decoupled from control_center.snapshot at import
        time (see the module's own docstring on why) - proved by showing
        the function honestly reports whatever it was given."""
        manifest = upgrade_manifest(snapshot_version=999)
        assert manifest.snapshot_version == 999
        assert manifest.system_version == SYSTEM_VERSION  # unaffected


# ---------------------------------------------------------------------------
# 3 / 4. Store schema versions are represented correctly
# ---------------------------------------------------------------------------


class TestSchemaVersionsAreRepresented:
    def test_memory_schema_version_matches_persistentmemory(self):
        manifest = upgrade_manifest(snapshot_version=1)
        assert manifest.memory_schema_version == MEMORY_SCHEMA_VERSION

    def test_state_schema_version_matches_statestore(self):
        manifest = upgrade_manifest(snapshot_version=1)
        assert manifest.state_schema_version == STATE_SCHEMA_VERSION

    def test_the_two_schema_versions_are_independent_fields(self):
        """Even though both happen to be 1 today, they must never be
        collapsed into one shared value - a future memory schema bump
        must not silently also read as a state schema bump."""
        manifest = upgrade_manifest(snapshot_version=1)
        assert "memory_schema_version" in manifest.to_dict()
        assert "state_schema_version" in manifest.to_dict()
        # Not the same *field*, even though both happen to equal 1 today:
        assert UpgradeManifest.__dataclass_fields__["memory_schema_version"] is not (
            UpgradeManifest.__dataclass_fields__["state_schema_version"]
        )


# ---------------------------------------------------------------------------
# 5. Visual Environment Registry versions are represented correctly
# ---------------------------------------------------------------------------


class TestVisualEnvironmentVersionsAreRepresented:
    def test_all_five_surfaces_are_found_in_a_synthetic_registry(self, tmp_path):
        ui_file = tmp_path / "index.html"
        ui_file.write_text(SYNTHETIC_REGISTRY, encoding="utf-8")

        found = {v.surface: v for v in visual_environment_versions(ui_file)}

        assert found["agent:core"] == VisualEnvironmentVersion("agent:core", "test-core-emblem", 3)
        assert found["agent:polygon"] == VisualEnvironmentVersion("agent:polygon", "test-wing-emblem", 2)
        assert found["brain"] == VisualEnvironmentVersion("brain", "test-brain-lattice", 1)
        assert found["memory"] == VisualEnvironmentVersion("memory", "test-memory-core", 5)
        assert found["room"] == VisualEnvironmentVersion("room", "test-room-core", 1)

    def test_the_real_ui_index_html_is_read_correctly(self):
        """Not a synthetic fixture - the actual registry this project
        ships, proving the extractor still matches production and did not
        drift from ui/index.html Sec.37's real declarations."""
        found = {v.surface: v for v in visual_environment_versions(REAL_UI_PATH)}
        assert found["agent:core"].id == "alfred-tactical-core"
        assert found["agent:polygon"].id == "specialist-wing-core"
        assert found["room"].id == "room-energy-core"
        assert found["agent:core"].version == 1
        assert found["agent:polygon"].version == 1
        assert found["room"].version == 1
        # Stage 17.18 replaced the Cognition and Memory environments
        # outright - a node lattice became a folded human cortex, an
        # archive core became a stratified archive - so both carry a new
        # id and a bumped version. That makes this a stronger reading than
        # it used to be: when every surface was version 1, the extractor
        # returning a hardcoded 1 would have passed. Two surfaces that are
        # genuinely on a different version now prove it reads the real
        # file.
        assert found["brain"].id == "brain-human-cortex"
        assert found["brain"].version == 3
        assert found["memory"].id == "memory-archive-strata"
        assert found["memory"].version == 3

    def test_versions_are_not_redeclared_a_second_time_in_python(self):
        """The discipline this whole module exists to protect: no Python
        constant anywhere hard-codes "alfred-tactical-core" or any other
        registry id. Proved by scanning this module's own source."""
        import src.control_center.manifest as manifest_module

        source = inspect.getsource(manifest_module)
        for forbidden_id in (
            "alfred-tactical-core", "specialist-wing-core",
            "brain-node-lattice", "memory-archive-core", "room-energy-core",
        ):
            assert forbidden_id not in source, forbidden_id

    def test_a_missing_ui_file_yields_no_entries_and_does_not_raise(self, tmp_path):
        result = visual_environment_versions(tmp_path / "does-not-exist.html")
        assert result == ()

    def test_one_missing_surface_does_not_take_down_the_others(self, tmp_path):
        """Mirrors PersistentMemory's own rule: one bad record does not
        cost the rest of the store."""
        partial = SYNTHETIC_REGISTRY.replace("BRAIN_VISUAL_ENVIRONMENT", "SOMETHING_ELSE_ENTIRELY")
        ui_file = tmp_path / "index.html"
        ui_file.write_text(partial, encoding="utf-8")

        found = {v.surface for v in visual_environment_versions(ui_file)}

        assert "brain" not in found
        assert {"agent:core", "agent:polygon", "memory", "room"} <= found

    def test_the_manifest_as_a_whole_still_builds_when_a_surface_is_missing(self, tmp_path):
        ui_file = tmp_path / "index.html"
        ui_file.write_text("nothing relevant here", encoding="utf-8")

        manifest = upgrade_manifest(snapshot_version=1, ui_path=ui_file)

        assert manifest.visual_environments == ()
        assert manifest.system_version == SYSTEM_VERSION  # the rest is unaffected


# ---------------------------------------------------------------------------
# 6. No fake provider/model/API-key information
# ---------------------------------------------------------------------------


class TestNoFakeProviderInformation:
    def test_the_module_names_no_provider(self):
        import src.control_center.manifest as manifest_module

        source = inspect.getsource(manifest_module).lower()
        for forbidden in ("claude", "openai", "grok", "anthropic", "api_key", "apikey", "model"):
            assert forbidden not in source, forbidden

    def test_the_manifest_has_no_provider_field(self):
        field_names = UpgradeManifest.__dataclass_fields__.keys()
        assert not any("provider" in name for name in field_names)

    def test_the_manifest_has_no_brain_or_connection_field(self):
        """No fake completeness (Sec.3 of this milestone): Brain,
        providers, integrations have no real persisted version contract
        today, so they are absent - not represented by an invented
        placeholder. Checked as a substring of every field name (not dict
        membership, which would only catch a field named exactly "brain"
        and miss one named "brain_schema_version")."""
        field_names = UpgradeManifest.__dataclass_fields__.keys()
        for forbidden in ("brain", "provider", "connection", "integration", "gmail", "capability"):
            assert not any(forbidden in name for name in field_names), forbidden


# ---------------------------------------------------------------------------
# 7. Deterministic
# ---------------------------------------------------------------------------


class TestManifestGenerationIsDeterministic:
    def test_two_calls_produce_an_equal_manifest(self):
        first = upgrade_manifest(snapshot_version=1)
        second = upgrade_manifest(snapshot_version=1)
        assert first == second

    def test_two_calls_produce_an_equal_dict(self):
        assert upgrade_manifest(snapshot_version=1).to_dict() == upgrade_manifest(snapshot_version=1).to_dict()

    def test_visual_environment_order_is_stable(self, tmp_path):
        ui_file = tmp_path / "index.html"
        ui_file.write_text(SYNTHETIC_REGISTRY, encoding="utf-8")
        first = visual_environment_versions(ui_file)
        second = visual_environment_versions(ui_file)
        assert first == second


# ---------------------------------------------------------------------------
# 8. No dependency on agent display names
# ---------------------------------------------------------------------------


class TestManifestDoesNotDependOnAgentDisplayNames:
    def test_the_module_imports_nothing_identity_related(self):
        """Checked against the real import statements (via ast), not the
        whole source text - the module's own docstring names src.registry
        and src.composition descriptively, to explain what is deliberately
        *not* imported, and a plain substring search would trip on its own
        explanation."""
        import ast

        import src.control_center.manifest as manifest_module

        tree = ast.parse(inspect.getsource(manifest_module))
        imported_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported_modules.append(node.module)
            elif isinstance(node, ast.Import):
                imported_modules.extend(alias.name for alias in node.names)

        for forbidden in ("src.registry", "src.services.identity", "src.composition"):
            assert forbidden not in imported_modules, forbidden

        # A code-shaped check, not a prose one: the module's own docstring
        # discusses "Alfred" and "Arthur" by name, in quotes, to explain
        # what it deliberately does not depend on - so this checks for an
        # actual class reference instead of a substring a sentence could
        # innocently contain.
        assert "AgentRegistry" not in inspect.getsource(manifest_module)

    def test_upgrade_manifest_takes_no_agent_or_name_parameter(self):
        param_names = inspect.signature(upgrade_manifest).parameters.keys()
        for forbidden in ("agent", "name", "identity"):
            assert not any(forbidden in p for p in param_names), forbidden

    def test_the_manifest_has_no_agent_name_field(self):
        field_names = UpgradeManifest.__dataclass_fields__.keys()
        for forbidden in ("name", "agent", "identity", "owner"):
            assert not any(forbidden in f for f in field_names), forbidden


# ---------------------------------------------------------------------------
# to_dict() shape
# ---------------------------------------------------------------------------


class TestToDictShape:
    def test_to_dict_has_exactly_the_expected_keys(self):
        keys = set(upgrade_manifest(snapshot_version=1).to_dict().keys())
        assert keys == {
            "system_version", "snapshot_version", "memory_schema_version",
            "state_schema_version", "visual_environments",
        }

    def test_visual_environments_serialise_as_plain_dicts(self):
        payload = upgrade_manifest(snapshot_version=1).to_dict()
        for entry in payload["visual_environments"]:
            assert set(entry.keys()) == {"surface", "id", "version"}
