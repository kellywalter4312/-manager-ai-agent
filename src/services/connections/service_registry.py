"""File-backed persistence for declared ``ServiceDescriptor``s - the
open-ended-discovery counterpart to
``src/services/credentials/account_store_file.py``, structured the
identical "read fresh, write whole" way for the identical reason: a
partial write can never leave the file half-updated and half-stale.

**Never read by Alfred, the Brain, or any agent directly.** Only an
operator surface (``scripts/control_center.py``'s own ``services``
verb, reached through Control Center) ever declares or forgets an
entry here - the same boundary ``DEFAULT_ACCOUNTS_PATH`` already sits
behind. What a *running* agent actually reaches is
``connection_factory.build_connection``'s own fallback lookup into
this registry at process-construction time, never a live read from
inside a capability handler.

**A declared service is data, not code, and stays that way at rest
too.** The file holds exactly what ``ServiceDescriptor``/
``ServiceOperation`` already validate - a name, a base URL, and a
finite operation list - never a credential (that stays in
``CredentialStore``, keyed by the identical service name) and never
anything resembling executable content.
"""

from __future__ import annotations

import json
from pathlib import Path

from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation

# Its own file, alongside credentials' own two - one axis's declared
# state never shares a file with another's, the identical per-concern
# isolation this project already holds itself to everywhere else in
# data/.
DEFAULT_SERVICES_PATH = Path("data") / "connections" / "services.json"

# Schema-versioned like every other durable store in this project
# (src/core/versioning.py's own recommendation, already followed by
# data/evolution/*.json) - an intentionally-trivial version 1 with
# nothing yet to migrate from.
SCHEMA_VERSION = 1


def load_service_registry(path: "Path | str" = DEFAULT_SERVICES_PATH) -> "dict[str, ServiceDescriptor]":
    """Every declared service, keyed by its own name.

    A missing file is not an error - the same "absence is not an
    error" rule ``load_credential_store`` already follows: a brand-new
    installation, or one where nothing has ever been declared, gets an
    honestly empty registry, not a crash. A malformed entry is skipped
    rather than corrupting the whole load - the same tolerant-of-partial-
    corruption discipline ``ArtifactStore``'s own index load already
    holds itself to, so one bad entry never makes every other declared
    service unreachable.
    """
    path = Path(path)
    if not path.exists():
        return {}

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}

    registry: "dict[str, ServiceDescriptor]" = {}
    for entry in payload.get("services", []):
        try:
            operations = tuple(
                ServiceOperation(
                    name=op["name"],
                    method=op.get("method", "GET"),
                    path=op.get("path", "/"),
                    requires_auth=bool(op.get("requires_auth", True)),
                    description=op.get("description", ""),
                )
                for op in entry.get("operations", [])
            )
            descriptor = ServiceDescriptor(
                name=entry["name"],
                base_url=entry["base_url"],
                operations=operations,
                description=entry.get("description", ""),
            )
        except (KeyError, ValueError):
            continue
        registry[descriptor.name] = descriptor
    return registry


def save_service_registry(
    registry: "dict[str, ServiceDescriptor]", path: "Path | str" = DEFAULT_SERVICES_PATH
) -> None:
    """Write every descriptor currently in ``registry`` to ``path``,
    overwriting whatever was there - the whole registry rewritten each
    time, never patched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "services": [
            {
                "name": descriptor.name,
                "base_url": descriptor.base_url,
                "description": descriptor.description,
                "operations": [
                    {
                        "name": op.name,
                        "method": op.method,
                        "path": op.path,
                        "requires_auth": op.requires_auth,
                        "description": op.description,
                    }
                    for op in descriptor.operations
                ],
            }
            for descriptor in sorted(registry.values(), key=lambda d: d.name)
        ],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def declare_service(
    descriptor: ServiceDescriptor, path: "Path | str" = DEFAULT_SERVICES_PATH
) -> "dict[str, ServiceDescriptor]":
    """Register (or replace) one declared service and persist the
    result immediately - a real, durable write, never held only in
    memory. Returns the resulting registry so a caller can report what
    is now declared without a second read."""
    registry = load_service_registry(path)
    registry[descriptor.name] = descriptor
    save_service_registry(registry, path)
    return registry


def forget_service(name: str, path: "Path | str" = DEFAULT_SERVICES_PATH) -> bool:
    """Remove one declared service permanently. Returns whether
    something was actually removed - forgetting a name that was never
    declared, or already forgotten, is an honest, idempotent no-op,
    the same rule ``CredentialStore.revoke`` already follows."""
    registry = load_service_registry(path)
    normalised = " ".join(str(name).split()).lower()
    if normalised not in registry:
        return False
    del registry[normalised]
    save_service_registry(registry, path)
    return True
