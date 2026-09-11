"""File-backed persistence for declared ``SiteDescriptor``s - the
literal-website-interaction counterpart to ``service_registry.py``,
structured the identical "read fresh, write whole" way for the
identical reason: a partial write can never leave the file half-
updated and half-stale.

**Never read by Alfred, the Brain, or any agent directly.** Only an
operator surface (``scripts/control_center.py``'s own ``sites`` verb,
reached through Control Center) ever declares or forgets an entry here
- the same boundary ``DEFAULT_SERVICES_PATH`` already sits behind.
What a *running* agent actually reaches is
``connection_factory.build_connection``'s own fallback lookup into
this registry at process-construction time, never a live read from
inside a capability handler.

**A declared site is data, not code, and stays that way at rest too.**
The file holds exactly what ``SiteDescriptor`` already validates - a
name, a base URL, and an optional auth-cookie name - never a
credential (that stays in ``CredentialStore``, keyed by the identical
site name) and never anything resembling executable content.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.providers.site_descriptor import SiteDescriptor

# Its own file, alongside services' and credentials' own - one axis's
# declared state never shares a file with another's, the identical
# per-concern isolation this project already holds itself to
# everywhere else in data/.
DEFAULT_SITES_PATH = Path("data") / "connections" / "sites.json"

# Schema-versioned like every other durable store in this project, an
# intentionally-trivial version 1 with nothing yet to migrate from.
SCHEMA_VERSION = 1


def load_site_registry(path: "Path | str" = DEFAULT_SITES_PATH) -> "dict[str, SiteDescriptor]":
    """Every declared site, keyed by its own name.

    A missing file is not an error - the same "absence is not an
    error" rule ``load_service_registry`` already follows: a brand-new
    installation, or one where nothing has ever been declared, gets an
    honestly empty registry, not a crash. A malformed entry is skipped
    rather than corrupting the whole load - the same tolerant-of-
    partial-corruption discipline ``load_service_registry`` already
    holds itself to, so one bad entry never makes every other declared
    site unreachable.
    """
    path = Path(path)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    registry: "dict[str, SiteDescriptor]" = {}
    for entry in payload.get("sites", []):
        try:
            descriptor = SiteDescriptor(
                name=entry["name"],
                base_url=entry["base_url"],
                auth_cookie_name=entry.get("auth_cookie_name", ""),
                description=entry.get("description", ""),
            )
        except (KeyError, ValueError):
            continue
        registry[descriptor.name] = descriptor
    return registry


def save_site_registry(
    registry: "dict[str, SiteDescriptor]", path: "Path | str" = DEFAULT_SITES_PATH
) -> None:
    """Write every descriptor currently in ``registry`` to ``path``,
    overwriting whatever was there - the whole registry rewritten each
    time, never patched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "sites": [
            {
                "name": descriptor.name,
                "base_url": descriptor.base_url,
                "auth_cookie_name": descriptor.auth_cookie_name,
                "description": descriptor.description,
            }
            for descriptor in sorted(registry.values(), key=lambda d: d.name)
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def declare_site(
    descriptor: SiteDescriptor, path: "Path | str" = DEFAULT_SITES_PATH
) -> "dict[str, SiteDescriptor]":
    """Register (or replace) one declared site and persist the result
    immediately - a real, durable write, never held only in memory.
    Returns the resulting registry so a caller can report what is now
    declared without a second read."""
    registry = load_site_registry(path)
    registry[descriptor.name] = descriptor
    save_site_registry(registry, path)
    return registry


def forget_site(name: str, path: "Path | str" = DEFAULT_SITES_PATH) -> bool:
    """Remove one declared site permanently. Returns whether something
    was actually removed - forgetting a name that was never declared,
    or already forgotten, is an honest, idempotent no-op, the same rule
    ``forget_service`` already follows."""
    registry = load_site_registry(path)
    normalised = " ".join(str(name).split()).lower()
    if normalised not in registry:
        return False
    del registry[normalised]
    save_site_registry(registry, path)
    return True
