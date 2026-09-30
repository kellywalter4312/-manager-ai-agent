"""The Upgrade Manifest: which version of each real Alfred OS compatibility
contract is currently installed.

Four different things in this codebase are already called a "version", each
guarding a different boundary, and this module's whole job is to report them
side by side without letting them blur into one number:

    SYSTEM_VERSION            the assembled product (src/core/versioning.py)
    SNAPSHOT_VERSION          the shape of this interface's own export
                              (src/control_center/snapshot.py)
    memory SCHEMA_VERSION     PersistentMemory's on-disk shape
                              (src/services/memory/persistent.py)
    state SCHEMA_VERSION      StateStore's on-disk shape
                              (src/heartbeat/state.py)

and one more concept, reported the same way rather than reinvented:

    visual environment version   {id, version} per surface, from the
                                 Visual Environment Registry (ui/index.html
                                 Sec.37) - every registry declaration of
                                 the {id, version} descriptor shape,
                                 discovered rather than listed (Phase 11).

**What this is not, stated once, plainly, because a manifest is exactly the
kind of thing that quietly grows past its own edges:**

- Not the migration engine. `src/core/versioning.py`'s Migration/
  MigrationChain already exist and are untouched by this module - it reads
  the version numbers they and their callers already maintain; it does not
  decide whether an upgrade may run.
- Not an update server, a package manager, or an auto-updater. Nothing here
  downloads, installs, applies, or schedules anything.
- Not agent identity. This module imports nothing from src.registry,
  src.services.identity, or src.composition, and cannot - even by
  accident - because it never receives an agent object, a name, or a
  registry. Changing a provider, a Brain implementation, a visual
  environment version, or a memory schema can never look like a new Alfred
  through this module, because this module has no notion of "Alfred" to
  begin with.
- Not Brain or Memory state. It reports one integer describing Memory's
  on-disk *shape* (SCHEMA_VERSION); it does not read a single record, a
  Brain mode, or a provider name.

**No fake completeness.** Providers, integrations, the Brain's own
reasoning implementation, and connections do not currently have an
independent, persisted compatibility version anywhere in this codebase -
inspection for this milestone confirmed it, the same inspection that found
SYSTEM_VERSION/SNAPSHOT_VERSION/SCHEMA_VERSION already real and already
used. Inventing a version number for any of those to make this manifest
look complete would be exactly the fabrication this whole project's
control-center layer refuses to do anywhere else ("the interface reads the
system, it does not become a second one") - so they are simply absent, not
represented as some placeholder value.

**Reads, never re-declares.** The visual environment versions are not
copied into a second Python constant that could quietly drift from
ui/index.html's own registry (Sec.37) the next time someone edits one
without remembering the other. They are read out of ui/index.html itself -
the registry's one real source of truth - the same discipline this
project's snapshot layer already applies to Brain mode, memory counts and
everything else it reports: ask the thing that knows, never recompute a
second opinion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from src.core.versioning import SYSTEM_VERSION
from src.heartbeat.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION
from src.services.memory.persistent import SCHEMA_VERSION as MEMORY_SCHEMA_VERSION
from src.utils.logging import get_logger

logger = get_logger(__name__)

DEFAULT_UI_PATH = "ui/index.html"

# Every registry declaration of the Visual Environment Registry's own
# {id, version} descriptor shape (ui/index.html Sec.37), discovered rather
# than listed (Phase 11). Before, one pattern per named surface meant the
# avatar-body, symbolic and hair environments the page already versions were
# invisible here, and a new environment needed this file edited. Two shapes:
#
#     const BRAIN_VISUAL_ENVIRONMENT = { id: "...", version: N, ... }
#         -> surface "brain"
#     const AGENT_VISUAL_ENVIRONMENTS = { core: { id: "...", version: N }, ... }
#         -> surfaces "agent:core", "agent:polygon", ...
#
# Surface names are the constant's own name, lower-cased, less the
# conventional _VISUAL_ENVIRONMENT(S) suffix - exactly the five surface ids
# this manifest always reported, so every version already on record (the
# ledger's system.visual_environment.<surface>) keeps its id.
_SINGLE_DECLARATION = re.compile(
    r'const\s+([A-Z][A-Z0-9_]*)\s*=\s*\{\s*id:\s*"([^"]+)",\s*version:\s*(\d+)'
)
_MAP_DECLARATION = re.compile(r'const\s+([A-Z][A-Z0-9_]*)\s*=\s*\{[ \t]*\r?\n')
_MAP_ENTRY = re.compile(
    r'^\s*"?([A-Za-z0-9_:\-]+)"?\s*:\s*\{\s*id:\s*"([^"]+)",\s*version:\s*(\d+)', re.MULTILINE
)
_MAP_END = re.compile(r'^\s*\};', re.MULTILINE)
_REGISTRY_SUFFIXES = ("_VISUAL_ENVIRONMENTS", "_VISUAL_ENVIRONMENT")


def _surface_prefix(constant: str) -> str:
    for suffix in _REGISTRY_SUFFIXES:
        if constant.endswith(suffix):
            return constant[: -len(suffix)].lower()
    return constant.lower()


@dataclass(frozen=True)
class VisualEnvironmentVersion:
    """One entry read out of the Visual Environment Registry.

    ``surface`` names which stage this is - "agent:core" (the manager
    form), "agent:polygon" (the specialist form), "brain" or "memory" -
    never an agent name, because the registry itself is keyed by
    identityOf()'s structural `form`, not by who anyone is.
    """

    surface: str
    id: str
    version: int


@dataclass(frozen=True)
class UpgradeManifest:
    """Real version numbers of real, currently-installed compatibility
    contracts. See this module's own docstring for what is deliberately
    absent and why.
    """

    system_version: str
    snapshot_version: int
    memory_schema_version: int
    state_schema_version: int
    visual_environments: tuple[VisualEnvironmentVersion, ...]

    def to_dict(self) -> dict:
        """Plain JSON-safe types, the same shape snapshot.py already hands
        every other reading in - a dict, not a second serialisation
        format."""
        return {
            "system_version": self.system_version,
            "snapshot_version": self.snapshot_version,
            "memory_schema_version": self.memory_schema_version,
            "state_schema_version": self.state_schema_version,
            "visual_environments": [
                {"surface": entry.surface, "id": entry.id, "version": entry.version}
                for entry in self.visual_environments
            ],
        }


def visual_environment_versions(
    ui_path: str | Path = DEFAULT_UI_PATH,
) -> tuple[VisualEnvironmentVersion, ...]:
    """Read the Visual Environment Registry's own declarations out of
    ui/index.html, rather than maintaining a second copy of them here.

    Never raises. A missing file is reported as no entries at all; a
    surface whose declaration this narrow parser no longer recognises (a
    future restructuring of the registry, for instance) is reported by
    that one surface being absent from the result, with a warning naming
    which one - the same "say so rather than guess, and don't let one
    degraded part take the whole reading down" rule PersistentMemory
    already applies to one unreadable record among many trustworthy ones.
    """
    path = Path(ui_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning(
            "could not read '%s' for the visual environment registry: %s", path, exc
        )
        return ()

    found: list[VisualEnvironmentVersion] = []
    for match in _SINGLE_DECLARATION.finditer(text):
        found.append(VisualEnvironmentVersion(
            surface=_surface_prefix(match.group(1)), id=match.group(2), version=int(match.group(3)),
        ))
    for declaration in _MAP_DECLARATION.finditer(text):
        closing = _MAP_END.search(text, declaration.end())
        body = text[declaration.end(): closing.start() if closing else len(text)]
        entries = list(_MAP_ENTRY.finditer(body))
        prefix = _surface_prefix(declaration.group(1))
        if not entries:
            if declaration.group(1).endswith(_REGISTRY_SUFFIXES):
                # Named as a registry but no longer in the descriptor shape -
                # said out loud, and omitted, never guessed at.
                logger.warning(
                    "visual environment registry '%s' in '%s' declares no {id, version} entry this "
                    "manifest recognises; omitted from the upgrade manifest", declaration.group(1), path,
                )
            continue
        for entry in entries:
            found.append(VisualEnvironmentVersion(
                surface=f"{prefix}:{entry.group(1)}", id=entry.group(2), version=int(entry.group(3)),
            ))
    return tuple(found)


def upgrade_manifest(
    *, snapshot_version: int, ui_path: str | Path = DEFAULT_UI_PATH
) -> UpgradeManifest:
    """Build the Upgrade Manifest.

    ``snapshot_version`` is supplied by the caller (snapshot.py) rather
    than imported here: this module has no reason to depend on
    control_center.snapshot, and importing it just to read one constant
    would risk the reverse import snapshot.py needs (embedding this
    manifest in its own payload) becoming circular.

    Deterministic and read-only: the same installed code and the same
    ui/index.html on disk always produce the same manifest, and nothing
    here mutates anything, schedules anything, or applies anything.
    """
    return UpgradeManifest(
        system_version=SYSTEM_VERSION,
        snapshot_version=snapshot_version,
        memory_schema_version=MEMORY_SCHEMA_VERSION,
        state_schema_version=STATE_SCHEMA_VERSION,
        visual_environments=visual_environment_versions(ui_path),
    )
