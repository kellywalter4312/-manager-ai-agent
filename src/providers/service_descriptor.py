"""A declared, previously-unknown service - the data a new governed
connection needs to become real without a new Python class.

**The gap this closes.** ``connection_factory.py``'s own ``_BUILDERS``
dict is genuinely generic in its *dispatch* (any name can be looked up)
but not in its *membership* - today only "null" and "gmail" exist, and
adding a third service has always meant writing a new ``Connection``
subclass, ordinary source code work. ``run_connections_command``
(``scripts/control_center.py``) already accepts and credentials *any*
service name - "a future service needs no change here to become
connectable, only a name," its own docstring says - but a credentialed
name alone was never enough to make ``connected_action``
(``src/manager/capabilities.py``) able to reach it: nothing turned a
name plus a credential into an actual ``Connection`` instance with real
declared operations. This module is that missing middle piece: what a
newly-discovered service *is*, expressed as data.

**Governed, not unrestricted.** A ``ServiceDescriptor`` names one fixed
base URL and a finite, explicitly declared set of operations, each with
its own fixed HTTP method and path - the identical "one destination
host, one endpoint, one method, bounded" shape
``WikipediaLiveInfoProvider``'s own docstring already commits to for a
real, working stdlib HTTP call. There is no way to reach an arbitrary,
conversation-chosen URL through this - a service becomes reachable only
by being declared through the existing, confirmed, audited
administrative-write path (``services <name> declare ...``, gated
exactly like ``provider add``/``connections connect`` already are),
never by anything Alfred decides on its own mid-conversation.

**No catalogue.** Nothing here names a real company, website or
domain - a ``ServiceDescriptor`` is a value a caller constructs from
whatever a person declares, the same "names come from the caller,
never invented" discipline ``ProviderAccount.account_id`` already
holds itself to.
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

# "null"/"gmail" are the two connection names the architecture already
# reserves for its own built-in builders (connection_factory.py's own
# _BUILDERS) - a declared service must never shadow either, or
# selecting MANAGER_CONNECTION_PROVIDER=gmail could quietly resolve to
# a person's own declared data instead of the real Gmail connection.
RESERVED_SERVICE_NAMES = frozenset({"null", "gmail"})

_ALLOWED_METHODS: tuple[str, ...] = ("GET", "POST")

# Data-flow-through-execution checkpoint: the closed, small set of places
# a real, explicit parameter value can actually go in the one HTTP call
# DeclaredConnection.execute makes - never an open-ended location scheme,
# since only these three are things this project's own bounded HTTP
# client (urllib, a fixed base_url + path) can genuinely realise safely.
_ALLOWED_PARAMETER_LOCATIONS: tuple[str, ...] = ("path", "query", "body")

_PATH_PLACEHOLDER_PATTERN = re.compile(r"\{([a-zA-Z0-9_]+)\}")


@dataclass(frozen=True)
class ServiceParameter:
    """One input a declared operation genuinely needs or accepts - the
    service's own published metadata (an OpenAPI ``parameters`` entry's
    ``name``/``in``/``required``, or its ``requestBody`` schema's own
    property names), never invented by this project.

    ``location`` says where a real, explicit value for this parameter
    is injected into the one HTTP request ``DeclaredConnection.execute``
    makes: ``"path"`` (substituted into a ``{name}`` placeholder in the
    operation's own declared path), ``"query"`` or ``"body"`` (both flow
    through the identical GET-query-string/POST-JSON-body handling this
    project's execution layer already has - the smaller, sufficient
    representation this checkpoint's own brief explicitly allows, rather
    than a separate enforced channel for query versus body that the
    HTTP method itself does not already imply).
    """

    name: str
    location: str = "query"
    required: bool = True

    def __post_init__(self) -> None:
        name = " ".join(str(self.name).split()).lower().replace(" ", "_")
        if not name:
            raise ValueError("ServiceParameter.name must not be empty")
        object.__setattr__(self, "name", name)

        location = str(self.location).strip().lower()
        if location not in _ALLOWED_PARAMETER_LOCATIONS:
            raise ValueError(
                f"ServiceParameter.location must be one of {_ALLOWED_PARAMETER_LOCATIONS}, "
                f"got {location!r}"
            )
        object.__setattr__(self, "location", location)

        object.__setattr__(self, "required", bool(self.required))


@dataclass(frozen=True)
class ServiceOperation:
    """One thing a declared service can be asked to do - a fixed HTTP
    method and path relative to its own ``ServiceDescriptor.base_url``,
    never a caller-supplied one."""

    name: str
    method: str = "GET"
    path: str = "/"
    requires_auth: bool = True
    description: str = ""
    # Data-flow-through-execution checkpoint: every input this operation
    # declares, each carrying its own real location - empty by default,
    # so every operation declared before this field existed keeps
    # working completely unchanged.
    parameters: "tuple[ServiceParameter, ...]" = ()

    def __post_init__(self) -> None:
        name = " ".join(str(self.name).split()).lower().replace(" ", "_")
        if not name:
            raise ValueError("ServiceOperation.name must not be empty")
        object.__setattr__(self, "name", name)

        method = str(self.method).strip().upper()
        if method not in _ALLOWED_METHODS:
            raise ValueError(
                f"ServiceOperation.method must be one of {_ALLOWED_METHODS}, got {method!r}"
            )
        object.__setattr__(self, "method", method)

        path = str(self.path).strip() or "/"
        # "://" would make the concatenated URL DeclaredConnection builds
        # (base_url + path) *look* like it still targets base_url while
        # actually embedding a second, different origin in the path
        # component - never followed as one by urllib (string
        # concatenation is not re-parsed), but rejected outright anyway
        # so a malicious or malformed operation can never even be
        # declared, not merely rendered harmless. Control characters are
        # rejected for the identical reason: no operation path may ever
        # smuggle a header/request-line injection into the one real
        # network call this connection is allowed to make. Checked
        # before, not after, normalisation - service-controlled input,
        # not this class's own formatting.
        if "://" in path or any(ord(c) < 0x20 for c in path):
            raise ValueError(f"ServiceOperation.path must be a plain relative path, got {self.path!r}")
        if not path.startswith("/"):
            path = "/" + path
        object.__setattr__(self, "path", path)

        object.__setattr__(self, "description", " ".join(str(self.description or "").split()))

        parameters = tuple(self.parameters)
        names = [p.name for p in parameters]
        if len(names) != len(set(names)):
            raise ValueError("ServiceOperation.parameters must have unique names")

        # A "path" parameter is only ever meaningful alongside a real
        # {name} placeholder the service itself put in this operation's
        # own path - and a placeholder with nothing declared for it
        # could never be filled without fabricating a value, so it is
        # refused here rather than silently sent as the literal text
        # "{name}". Both directions checked, never assumed consistent.
        placeholders = set(_PATH_PLACEHOLDER_PATTERN.findall(path))
        path_parameter_names = {p.name for p in parameters if p.location == "path"}
        if path_parameter_names - placeholders:
            raise ValueError(
                f"ServiceOperation declares path parameter(s) "
                f"{sorted(path_parameter_names - placeholders)} with no matching "
                f"'{{name}}' placeholder in path {path!r}"
            )
        if placeholders - path_parameter_names:
            raise ValueError(
                f"ServiceOperation path {path!r} names placeholder(s) "
                f"{sorted(placeholders - path_parameter_names)} with no matching declared "
                "path parameter"
            )

        object.__setattr__(self, "parameters", parameters)

    def parameter(self, name: str) -> "ServiceParameter | None":
        normalised = " ".join(str(name).split()).lower().replace(" ", "_")
        return next((p for p in self.parameters if p.name == normalised), None)

    @property
    def required_parameters(self) -> "tuple[str, ...]":
        return tuple(p.name for p in self.parameters if p.required)


@dataclass(frozen=True)
class ServiceDescriptor:
    """One previously-unknown service, declared as data: a name, a
    single fixed base URL, and a finite set of operations. Constructing
    one never reaches the network and never persists anything - see
    ``src/services/connections/service_registry.py`` for that."""

    name: str
    base_url: str
    operations: "tuple[ServiceOperation, ...]"
    description: str = ""

    def __post_init__(self) -> None:
        name = " ".join(str(self.name).split()).lower()
        if not name:
            raise ValueError("ServiceDescriptor.name must not be empty")
        if name in RESERVED_SERVICE_NAMES:
            raise ValueError(
                f"'{name}' is a reserved connection name (built into "
                "connection_factory.py) and cannot be declared as a service"
            )
        object.__setattr__(self, "name", name)

        base_url = str(self.base_url).strip().rstrip("/")
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError(
                f"ServiceDescriptor.base_url must be an absolute http(s) URL, got {self.base_url!r}"
            )
        object.__setattr__(self, "base_url", base_url)

        operations = tuple(self.operations)
        if not operations:
            raise ValueError("ServiceDescriptor.operations must declare at least one operation")
        names = [op.name for op in operations]
        if len(names) != len(set(names)):
            raise ValueError("ServiceDescriptor.operations must have unique names")
        object.__setattr__(self, "operations", operations)

        object.__setattr__(self, "description", " ".join(str(self.description or "").split()))

    def operation(self, name: str) -> "ServiceOperation | None":
        """The declared operation named ``name``, or ``None`` - never
        raises, the same honest-absence shape ``CredentialStore.get``
        already returns for an unknown account."""
        normalised = " ".join(str(name).split()).lower().replace(" ", "_")
        return next((op for op in self.operations if op.name == normalised), None)

    @property
    def operation_names(self) -> "tuple[str, ...]":
        return tuple(op.name for op in self.operations)
