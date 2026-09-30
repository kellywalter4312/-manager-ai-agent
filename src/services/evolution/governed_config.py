"""Persisted, governed configuration for components whose real "version"
is a tunable value this project's own code already reads at
construction time - never a second credential store, never a second
provider registry, and never a place a secret is ever written.

**Why this exists.** ``Settings`` (``src/config/settings.py``) is this
project's one canonical configuration concept, and it is deliberately
environment-variable-driven with no on-disk persistence of its own -
correct for "which backend is selected" (a deployment decision), wrong
for "what a person changed through a governed evolution and expects to
survive a restart without re-exporting an environment variable every
time." ``CredentialStore`` (``src/services/credentials/``) is the
existing precedent for exactly this gap, applied to secrets; this is
the same shape, applied to non-secret, per-component tunable values
that this checkpoint's evolution handlers (``handlers.py``) need
somewhere real to write to.

**Deliberately narrow.** One flat mapping, ``component_id -> value``
(both plain strings - the same "read fresh, write whole" JSON shape
every other persisted store in this project already uses). No secrets
ever live here; nothing here is granted to the Brain or read by a
capability directly - only a handler's own real code, and the specific
``build()`` call sites this checkpoint threads it through, ever read
it. A future component that wants to participate needs nothing more
than a key here and a place in its own factory that reads it - no
change to this module.

**A file this code cannot read is never overwritten (Phase 11).** A
damaged file, or one written by a newer installation in a shape this
code does not know, is read as *no governed values* - every component
falls back to its own honest default, and a warning says why - and it is
never replaced: ``save_governed_config`` refuses to write over it, so a
newer installation's settings are never silently rewritten into an older
shape and lost. Every write is atomic (a temporary file, then
``os.replace``), so a failure part-way through leaves the previous
complete file, never half of a new one.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path("data") / "evolution" / "config.json"

# This store's own on-disk shape - distinct from, and never derived
# from, any other store's SCHEMA_VERSION (src/core/versioning.py's own
# point). No migration has ever been needed yet.
SCHEMA_VERSION = 1


class GovernedConfigUnreadable(RuntimeError):
    """The governed configuration on disk cannot be read by this code, so
    it must not be written over either."""


@dataclass(frozen=True)
class GovernedConfig:
    values: "dict[str, str]" = field(default_factory=dict)
    # Why the file on disk could not be read, when it could not - None for
    # an ordinary, readable (or absent) file. Never persisted.
    problem: "str | None" = None

    def get(self, component_id: str, default: "str | None" = None) -> "str | None":
        return self.values.get(component_id, default)

    def with_value(self, component_id: str, value: str) -> "GovernedConfig":
        updated = dict(self.values)
        updated[component_id] = value
        return GovernedConfig(values=updated, problem=self.problem)

    def without_value(self, component_id: str) -> "GovernedConfig":
        updated = dict(self.values)
        updated.pop(component_id, None)
        return GovernedConfig(values=updated, problem=self.problem)


def _unreadable_reason(path: Path) -> "tuple[dict | None, str | None]":
    """The file's values and ``None``, or ``None`` and why this code cannot
    read it - never a guess at what a damaged or newer file meant."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"'{path}' is not readable governed configuration ({exc})"
    if not isinstance(payload, dict):
        return None, f"'{path}' does not hold a governed configuration object"
    version = payload.get("schema_version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        return None, (
            f"'{path}' is governed configuration schema {version!r}; this installation "
            f"reads schema {SCHEMA_VERSION} and has no migration for it"
        )
    raw = payload.get("data", payload)
    values = raw.get("values", {}) if isinstance(raw, dict) else None
    if not isinstance(values, dict):
        return None, f"'{path}' has no governed values mapping"
    return {str(k): v for k, v in values.items()}, None


def load_governed_config(path: "Path | str" = DEFAULT_CONFIG_PATH) -> GovernedConfig:
    """A fresh config, populated from ``path`` if it exists. A missing
    file is not an error - every real component this checkpoint governs
    this way has an honest, unconfigured default it falls back to. A file
    this code cannot read is reported (``problem``) and read as no values,
    never guessed at."""
    path = Path(path)
    if not path.exists():
        return GovernedConfig()
    values, problem = _unreadable_reason(path)
    if problem is not None:
        logger.warning("%s - every governed value falls back to its default, and the file is left untouched", problem)
        return GovernedConfig(problem=problem)
    return GovernedConfig(values=values)


def save_governed_config(config: GovernedConfig, path: "Path | str" = DEFAULT_CONFIG_PATH) -> None:
    """Write the whole config to ``path`` atomically - never over a file
    this code could not read (``GovernedConfigUnreadable``), and never
    from a config that was itself read from one."""
    path = Path(path)
    if config.problem is not None:
        raise GovernedConfigUnreadable(config.problem)
    if path.exists():
        _, problem = _unreadable_reason(path)
        if problem is not None:
            raise GovernedConfigUnreadable(problem)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "data": {"values": dict(config.values)}}
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(temporary, path)
