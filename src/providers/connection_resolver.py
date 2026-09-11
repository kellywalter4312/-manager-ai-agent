"""Phase 7 - Universal Connection Layer's own canonical connection
context.

**The problem this closes.** Before this checkpoint, an agent held
exactly one ``Connection`` (``Runtime.connection``, ``settings.
connection_provider``, chosen once at process construction) even
though the persistence one layer down - ``service_registry.py``/
``site_registry.py``/``CredentialStore`` - already genuinely supported
many, of different kinds, simultaneously declared and credentialed.
That made the canonical *runtime* model a half-structure: real,
open-ended multi-connection data sitting behind a single-connection
door. ``ConnectionResolver`` is that door widened to match what was
already true one layer down - never a second registry, never a second
credential store, never a list bolted on beside the old field.

**Genuinely open-ended, never a fixed count.** Holds however many
connections this agent was actually equipped with - one, in the
common, unchanged case; many, of any mix of kinds (declared REST
services, declared websites, Gmail), the moment more than one is
declared and credentialed. No maximum is enforced anywhere in this
module.

**Built once, at process-construction time - never re-read live.**
``service_registry.py``/``site_registry.py`` each state their own
rule explicitly: "never a live read from inside a capability
handler." ``build_all_connections`` (``connection_factory.py``) is the
one, sanctioned place that rule permits reading them, at the identical
moment ``connection_factory.build_connection``'s own registry fallback
already did before this checkpoint. A ``ConnectionResolver`` is
therefore an immutable snapshot: a connection declared or credentialed
after one was built becomes reachable on the next build()/reload -
never mid-conversation, the identical boundary every other provider
axis in this project already respects (``composition.py``'s own
communication/knowledge/celebration/... providers are each chosen the
same way, once, at construction).

**Cheap to hold many of, by construction.** Every existing
``Connection`` implementation does no I/O merely by existing -
``DeclaredConnection``/``WebsiteConnection`` just wrap a descriptor
and whatever credential was already on file; a real external cost (an
HTTP call, a browser process) only happens inside ``execute()``
itself. Holding ten resolved connections costs what holding one
already did.

**Dynamic selection, not dynamic discovery.** A capability handler
(``connected_action``) still resolves WHICH of these to use at
execution time, by name, exactly as it already resolves which
operation to use within one - see ``_select_connection``,
``src/manager/capabilities.py``. What it may never do is discover a
name this resolver was not already built with; that is a real,
intentional boundary, not an oversight.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from src.providers.connection import Connection


@dataclass(frozen=True)
class ConnectionResolver:
    """One agent's own open-ended connection context - every
    ``Connection`` it was equipped with at construction time,
    addressable by name, independent of every other agent's own
    (constructed once per ``ManagerSkills`` instance, the identical
    per-instance independence pattern ``GovernanceLog``/the old
    ``connection`` field already had - never a shared/global
    resolver)."""

    _connections: "Mapping[str, Connection]"
    default_name: str

    def __post_init__(self) -> None:
        if not self._connections:
            raise ValueError("ConnectionResolver must be given at least one connection")
        if self.default_name not in self._connections:
            raise ValueError(
                f"ConnectionResolver's own default {self.default_name!r} "
                "must be one of its own connections"
            )
        object.__setattr__(self, "_connections", dict(self._connections))

    @property
    def names(self) -> "tuple[str, ...]":
        """Every connection name this agent was equipped with - sorted,
        so a listing is deterministic. A snapshot from construction
        time; never re-read live (see this module's own docstring)."""
        return tuple(sorted(self._connections))

    @property
    def default(self) -> Connection:
        """The connection an unqualified request means - the identical
        one this project held as its single ``Connection`` before this
        checkpoint (``settings.connection_provider``)."""
        return self._connections[self.default_name]

    def get(self, name: "str | None" = None) -> "Connection | None":
        """The connection named ``name``, or the default when ``name``
        is empty/None. ``None`` only when a real name was given that
        this agent was not actually equipped with - never invented,
        never a fallback to a connection nobody asked for."""
        if not name:
            return self.default
        return self._connections.get(name)

    def __repr__(self) -> str:
        return f"<ConnectionResolver names={self.names!r} default={self.default_name!r}>"
