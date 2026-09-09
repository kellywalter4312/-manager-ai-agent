"""Open-ended service discovery: turning an unknown service's own,
self-published metadata into a *proposed* ``ServiceDescriptor`` -
never a persisted one.

**The gap this closes.** The prior checkpoint's `services <name>
declare ...` already lets any legitimate service become a real,
governed connection - but only once a person has hand-typed its base
URL and every operation's method/path/auth requirement. Nothing yet
let Alfred find that shape out *from the service itself*. This module
is that missing half: DISCOVER and INSPECT, in the DISCOVER -> INSPECT
-> VALIDATE -> REPRESENT -> (present for authorization) -> CONNECT
lifecycle this checkpoint's own brief describes. REPRESENT is this
module's own job (producing a real ``ServiceDescriptor``); everything
from "present for authorization" onward is the *already-governed*
``services <name> declare`` administrative write
(``scripts/control_center.py``) - unchanged, and never bypassed.

**A real, pre-existing convention, not an invented one.** A service
publishes its own metadata at a fixed, predictable location -
``<base_url>/.well-known/alfred-service.json`` - the identical RFC 8615
"a domain describes itself at a standard path" pattern
``.well-known/openid-configuration`` and ``.well-known/security.txt``
already use in the wild (and the same shape this project's own
``video_generation.py`` already fetches a *third party's* published
OpenAPI document from, live, for a different purpose). Deliberately a
small, self-describing JSON document - not full OpenAPI parsing, which
would be a materially larger, riskier surface than this checkpoint's
own "smallest missing mechanism" calls for.

**Untrusted input, treated as untrusted throughout.** Every field this
module ever reads from a fetched document is named explicitly below -
``name`` is supplied by the *caller*, never read from the document;
``base_url`` is likewise always the caller's own, already-validated
argument, never anything the document claims, so a service can never
redirect Alfred to represent a different origin than the one actually
queried. ``operations``/``description`` are read, each individually
re-validated through ``ServiceDescriptor``/``ServiceOperation``'s own
existing, already-strict construction - the identical validation a
hand-typed ``declare`` command already goes through, never a second,
looser path. Every other key a document might contain (a claimed
permission, an "auto_approve" flag, an instruction, a different name)
is silently ignored - never read, never interpreted, never capable of
reaching Alfred's own configuration, credentials or governance. This
function itself never persists anything and never calls ``execute()``
on anything - discovery alone is not authorization to act.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation

# RFC 8615's own well-known-URI prefix, plus a name specific to this
# project - so a service can publish Alfred's own metadata alongside
# unrelated .well-known entries (security.txt, openid-configuration, ...)
# without collision.
WELL_KNOWN_PATH = "/.well-known/alfred-service.json"

# A self-description has no legitimate reason to be large. Bounding the
# read defends against a hostile or simply broken endpoint that streams
# an unbounded response - the request is refused outright rather than
# read in full and discarded, so a malicious host gains nothing by
# trying.
_MAX_RESPONSE_BYTES = 65536

_USER_AGENT = "AlfredOS-ServiceDiscovery/1.0"


class ServiceDiscoveryError(Exception):
    """Discovery could not produce a usable ``ServiceDescriptor`` - an
    unreachable host, a malformed document, or a document with no
    individually-valid operation. A real, honest refusal every time,
    the identical discipline every other real-network call in this
    project already holds itself to - never a fabricated empty
    success."""


@dataclass(frozen=True)
class DiscoveryResult:
    """What discovery actually found - a proposal, not a connection.
    Nothing about this type is persisted by constructing it; a caller
    (``scripts/control_center.py``'s own ``services <name> discover``)
    decides what, if anything, to show a person and what ``declare``
    command would turn it into a real one.
    """

    descriptor: ServiceDescriptor
    source_url: str
    # How many operation entries the document itself declared, before
    # any were dropped as individually malformed - an honest count, so
    # "3 of 5 were usable" is visible rather than silently rounded down
    # to "3 operations" with no sign anything was ever discarded.
    declared_operation_count: int


def discover_service(name: str, base_url: str, *, timeout: float = 8.0) -> DiscoveryResult:
    """Fetch ``<base_url>/.well-known/alfred-service.json`` and turn it
    into a proposed ``ServiceDescriptor`` named ``name``.

    Raises ``ServiceDiscoveryError`` on any failure - unreachable host,
    invalid JSON, a document with nothing individually valid to offer.
    Never raises anything else, and never returns a descriptor that
    ``ServiceDescriptor``'s own construction would not itself accept -
    this function performs no validation ``declare`` does not already
    enforce, it only *sources* the arguments that construction from
    somewhere other than a person's own typing.
    """
    normalised_base = str(base_url).strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalised_base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ServiceDiscoveryError(f"'{base_url}' is not an absolute http(s) URL")

    url = normalised_base + WELL_KNOWN_PATH
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.URLError as exc:
        raise ServiceDiscoveryError(f"could not reach '{url}': {exc}") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise ServiceDiscoveryError(
            f"'{url}' returned more than {_MAX_RESPONSE_BYTES} bytes - refused"
        )

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ServiceDiscoveryError(f"'{url}' did not return valid JSON: {exc}") from exc

    if not isinstance(payload, dict):
        raise ServiceDiscoveryError(
            f"'{url}' must return a JSON object, got {type(payload).__name__}"
        )

    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise ServiceDiscoveryError(f"'{url}' declares no usable 'operations' list")

    operations: "list[ServiceOperation]" = []
    for entry in raw_operations:
        if not isinstance(entry, dict):
            continue  # one malformed entry - a string, a number, null - does not sink the whole document
        try:
            operations.append(
                ServiceOperation(
                    name=str(entry.get("name", "")),
                    method=str(entry.get("method", "GET")),
                    path=str(entry.get("path", "/")),
                    requires_auth=bool(entry.get("requires_auth", True)),
                    description=str(entry.get("description", ""))[:500],
                )
            )
        except ValueError:
            continue  # one operation individually failing validation does not sink the rest

    if not operations:
        raise ServiceDiscoveryError(
            f"'{url}' declared {len(raw_operations)} operation(s), but none were individually valid"
        )

    # De-duplicate by name, keeping the first occurrence - a document
    # that (accidentally or otherwise) declares the same operation name
    # twice must not silently double up; ServiceDescriptor's own
    # construction already refuses duplicate names outright, so this
    # keeps a document with an honest mistake usable rather than
    # refusing the whole discovery over it.
    seen: set = set()
    deduplicated: "list[ServiceOperation]" = []
    for op in operations:
        if op.name in seen:
            continue
        seen.add(op.name)
        deduplicated.append(op)

    try:
        descriptor = ServiceDescriptor(
            name=name,
            base_url=normalised_base,
            operations=tuple(deduplicated),
            description=str(payload.get("description", ""))[:500],
        )
    except ValueError as exc:
        raise ServiceDiscoveryError(str(exc)) from exc

    return DiscoveryResult(
        descriptor=descriptor, source_url=url, declared_operation_count=len(raw_operations)
    )
