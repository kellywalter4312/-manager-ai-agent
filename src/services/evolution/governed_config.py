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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_CONFIG_PATH = Path("data") / "evolution" / "config.json"

# This store's own on-disk shape - distinct from, and never derived
# from, any other store's SCHEMA_VERSION (src/core/versioning.py's own
# point). No migration has ever been needed yet.
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GovernedConfig:
    values: "dict[str, str]" = field(default_factory=dict)

    def get(self, component_id: str, default: "str | None" = None) -> "str | None":
        return self.values.get(component_id, default)

    def with_value(self, component_id: str, value: str) -> "GovernedConfig":
        updated = dict(self.values)
        updated[component_id] = value
        return GovernedConfig(values=updated)

    def without_value(self, component_id: str) -> "GovernedConfig":
        updated = dict(self.values)
        updated.pop(component_id, None)
        return GovernedConfig(values=updated)


def load_governed_config(path: "Path | str" = DEFAULT_CONFIG_PATH) -> GovernedConfig:
    """A fresh config, populated from ``path`` if it exists. A missing
    file is not an error - every real component this checkpoint governs
    this way has an honest, unconfigured default it falls back to."""
    path = Path(path)
    if not path.exists():
        return GovernedConfig()
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw = payload.get("data", payload)
    return GovernedConfig(values=dict(raw.get("values", {})))


def save_governed_config(config: GovernedConfig, path: "Path | str" = DEFAULT_CONFIG_PATH) -> None:
    """Write the whole config to ``path`` - overwriting whatever was
    there, the same "read fresh, write whole" simplicity every other
    persisted store in this project already uses."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"schema_version": SCHEMA_VERSION, "data": {"values": dict(config.values)}}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
