"""Open-ended service discovery: turning an unknown service's own,
self-published metadata into a *proposed* ``ServiceDescriptor`` -
never a persisted one.

**The gap this closes.** ``services <name> declare ...`` already lets
any legitimate service become a real, governed connection - but only
once a person has hand-typed its base URL and every operation's
method/path/auth requirement. This module is the missing half:
DISCOVER and INSPECT, ahead of the already-governed REPRESENT/
authorize/CONNECT lifecycle (``services <name> declare``, unchanged,
never bypassed).

**Two real, pre-existing conventions, tried in order - never an
invented catalogue.** The prior checkpoint added the first:
``<base_url>/.well-known/alfred-service.json``, the identical RFC 8615
"a domain describes itself at a standard path" pattern
``.well-known/openid-configuration``/``.well-known/security.txt``
already use, for a service that specifically knows about Alfred. This
checkpoint adds the genuinely open-ended second: a small, bounded
*subset* of OpenAPI/Swagger - the real, industry-standard way an
already-existing service (that has never heard of Alfred, and never
will) already describes its own API, tried at the conventional
locations real services actually publish it
(``/openapi.json``, ``/.well-known/openapi.json``, ``/swagger.json``).
Not full OpenAPI parsing - response schemas, headers, cookies and
every method beyond GET/POST are deliberately out of scope. Path and
query parameters and a JSON request body's own declared fields (the
data-flow-through-execution checkpoint's own addition) are read where
the document actually declares them; a legitimate operation this
narrow reading still cannot represent (an inconsistently-declared
path, for one) is skipped, not fabricated, the identical "skip what
does not validate, never invent what would" discipline the
alfred-service.json parser already holds itself to.

**Untrusted input, treated as untrusted throughout, for both
sources.** ``name`` is always the *caller's* argument, never read from
either one. ``base_url`` is likewise always the caller's own,
already-validated argument - an OpenAPI document's own ``servers``
list is read by nobody here, so a service can never redirect Alfred to
represent a different origin than the one actually queried, exactly
the same boundary the alfred-service.json source already enforces.
Every operation extracted from either source is re-validated through
``ServiceDescriptor``/``ServiceOperation``'s own existing, already-
strict construction - the identical validation a hand-typed ``declare``
command already goes through, never a second, looser path. This module
never persists anything and never calls ``execute()`` on anything -
discovery alone is not authorization to act, from either source.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation, ServiceParameter

# RFC 8615's own well-known-URI prefix, plus a name specific to this
# project - so a service can publish Alfred's own metadata alongside
# unrelated .well-known entries without collision.
WELL_KNOWN_PATH = "/.well-known/alfred-service.json"

# The real, conventional locations an already-existing service
# publishes its own OpenAPI/Swagger document at - tried in this order,
# the first one that yields a recognisable, usable document wins.
# Never a guess at an arbitrary path: every one of these is a real,
# widely-used convention.
OPENAPI_CANDIDATE_PATHS: "tuple[str, ...]" = (
    "/openapi.json",
    "/.well-known/openapi.json",
    "/swagger.json",
)

# A self-description has no legitimate reason to be enormous. Bounding
# the read defends against a hostile or simply broken endpoint that
# streams an unbounded response - the request is refused outright
# rather than read in full and discarded, so a malicious host gains
# nothing by trying. OpenAPI documents are naturally larger than a tiny
# alfred-service.json, so this is generous enough for a real, modest
# API surface without being unbounded.
_MAX_RESPONSE_BYTES = 200_000

# A legitimately huge API (hundreds of endpoints) still yields a sane,
# reviewable proposal rather than an unusable wall of operations - the
# identical "bounded, not unlimited" discipline the response-size limit
# above already applies to.
_MAX_OPENAPI_OPERATIONS = 25

# GET/POST only - the identical, already-enforced allow-list
# ServiceOperation itself holds a hand-typed declare to; nothing here
# is more permissive than what a person could type by hand.
_OPENAPI_METHODS: "tuple[str, ...]" = ("get", "post")

_USER_AGENT = "AlfredOS-ServiceDiscovery/1.0"


class ServiceDiscoveryError(Exception):
    """Discovery could not produce a usable ``ServiceDescriptor`` from
    any known source - an unreachable host, or every known discovery
    location returning nothing individually valid. A real, honest
    refusal every time - never a fabricated empty success."""


class _SourceNotFound(Exception):
    """Internal only: this *specific* discovery source (one URL, one
    document shape) had nothing usable - never raised across a module
    boundary. The caller (``discover_service``) catches this to try the
    next source; a genuine host-level failure (unreachable, timed out)
    is a ``ServiceDiscoveryError`` instead, and is never caught here,
    because retrying a different path on a host that cannot be reached
    at all would only repeat the identical failure."""


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
    source_kind: str  # "alfred-service" | "openapi" - which convention actually answered
    # How many operation entries the source itself declared, before any
    # were dropped as individually unusable (malformed, parameterised,
    # an unsupported method, over the bound) - an honest count, so
    # "6 of 14 were usable" is visible rather than silently rounded
    # down with no sign anything was ever discarded.
    declared_operation_count: int


def discover_service(name: str, base_url: str, *, timeout: float = 8.0) -> DiscoveryResult:
    """Try every known discovery source against ``base_url``, in order,
    and turn the first usable one into a proposed ``ServiceDescriptor``
    named ``name``.

    Raises ``ServiceDiscoveryError`` when the host cannot be reached at
    all, or when every known source was tried and none was usable.
    Never raises anything else, and never returns a descriptor that
    ``ServiceDescriptor``'s own construction would not itself accept.
    """
    normalised_base = str(base_url).strip().rstrip("/")
    parsed = urllib.parse.urlparse(normalised_base)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ServiceDiscoveryError(f"'{base_url}' is not an absolute http(s) URL")

    attempts: "list[str]" = []

    try:
        return _try_alfred_service(name, normalised_base, timeout)
    except _SourceNotFound as exc:
        attempts.append(str(exc))

    for candidate_path in OPENAPI_CANDIDATE_PATHS:
        try:
            return _try_openapi(name, normalised_base, candidate_path, timeout)
        except _SourceNotFound as exc:
            attempts.append(str(exc))

    raise ServiceDiscoveryError(
        f"no usable discovery source found at '{normalised_base}'. Tried: " + "; ".join(attempts)
    )


def _fetch_document(url: str, timeout: float) -> dict:
    """The one real network call every discovery source shares: fetch,
    bound, decode, parse. A 404 (or any other HTTP-level error) means
    only "nothing here" - ``_SourceNotFound``, so the caller tries the
    next source. Anything that means the *host itself* could not be
    reached at all - refused, timed out, no such host - is a genuine
    ``ServiceDiscoveryError``, deliberately never caught by a source
    loop, since trying a different path on an unreachable host would
    only repeat the identical failure.
    """
    request = urllib.request.Request(
        url, headers={"User-Agent": _USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        raise _SourceNotFound(f"'{url}' returned HTTP {exc.code}") from None
    except urllib.error.URLError as exc:
        raise ServiceDiscoveryError(f"could not reach '{url}': {exc}") from exc

    if len(raw) > _MAX_RESPONSE_BYTES:
        raise _SourceNotFound(f"'{url}' returned more than {_MAX_RESPONSE_BYTES} bytes")

    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _SourceNotFound(f"'{url}' did not return valid JSON: {exc}") from None

    if not isinstance(payload, dict):
        raise _SourceNotFound(f"'{url}' did not return a JSON object")

    return payload


def _dedup_by_name(operations: "list[ServiceOperation]") -> "tuple[ServiceOperation, ...]":
    """First occurrence wins, deterministic - the identical rule both
    sources already need, factored out once rather than duplicated."""
    seen: set = set()
    deduplicated: "list[ServiceOperation]" = []
    for op in operations:
        if op.name in seen:
            continue
        seen.add(op.name)
        deduplicated.append(op)
    return tuple(deduplicated)


def _alfred_service_parameters(raw: object) -> "tuple[ServiceParameter, ...]":
    """One operation entry's own declared ``parameters`` list, in the
    identical flat ``{"name": ..., "location": ..., "required": ...}``
    shape the rest of this source already uses - absent entirely, or
    malformed, means no parameters, never a fabricated one."""
    if not isinstance(raw, list):
        return ()
    parameters: "list[ServiceParameter]" = []
    for item in raw:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        try:
            parameters.append(
                ServiceParameter(
                    name=str(item["name"]),
                    location=str(item.get("location", "query")),
                    required=bool(item.get("required", True)),
                )
            )
        except ValueError:
            continue
    return tuple(parameters)


def _try_alfred_service(name: str, base_url: str, timeout: float) -> DiscoveryResult:
    """Source 1: ``<base_url>/.well-known/alfred-service.json`` - a
    service that specifically knows about, and publishes for, Alfred.
    Unchanged from the prior checkpoint - a document here still means
    exactly what it always did.
    """
    url = base_url + WELL_KNOWN_PATH
    payload = _fetch_document(url, timeout)

    raw_operations = payload.get("operations")
    if not isinstance(raw_operations, list) or not raw_operations:
        raise _SourceNotFound(f"'{url}' declares no usable 'operations' list")

    operations: "list[ServiceOperation]" = []
    for entry in raw_operations:
        if not isinstance(entry, dict):
            continue
        try:
            operations.append(
                ServiceOperation(
                    name=str(entry.get("name", "")),
                    method=str(entry.get("method", "GET")),
                    path=str(entry.get("path", "/")),
                    requires_auth=bool(entry.get("requires_auth", True)),
                    description=str(entry.get("description", ""))[:500],
                    # Data-flow-through-execution checkpoint: a service
                    # that specifically publishes for Alfred can name
                    # its own real inputs directly, in the identical
                    # flat shape every other field on this entry already
                    # uses - never invented when absent.
                    parameters=_alfred_service_parameters(entry.get("parameters")),
                )
            )
        except ValueError:
            continue

    if not operations:
        raise _SourceNotFound(
            f"'{url}' declared {len(raw_operations)} operation(s), but none were individually valid"
        )

    try:
        descriptor = ServiceDescriptor(
            name=name,
            base_url=base_url,
            operations=_dedup_by_name(operations),
            description=str(payload.get("description", ""))[:500],
        )
    except ValueError as exc:
        raise ServiceDiscoveryError(str(exc)) from exc

    return DiscoveryResult(
        descriptor=descriptor, source_url=url, source_kind="alfred-service",
        declared_operation_count=len(raw_operations),
    )


def _openapi_parameters(op_obj: dict) -> "tuple[ServiceParameter, ...]":
    """Every real input this one OpenAPI operation entry declares -
    read straight from its own ``parameters`` array (``in: query`` or
    ``in: path``) and, when present, its own ``requestBody``'s JSON
    schema (``required``/``properties``) - never a header, a cookie, or
    anything this checkpoint's own bounded execution layer has no real
    place to put. Nothing here is fabricated: a field absent from the
    document simply does not appear in the result.
    """
    parameters: "list[ServiceParameter]" = []

    raw_parameters = op_obj.get("parameters")
    for param in raw_parameters if isinstance(raw_parameters, list) else ():
        if not isinstance(param, dict):
            continue
        location = param.get("in")
        param_name = param.get("name")
        if location not in ("query", "path") or not param_name:
            continue
        # OpenAPI's own rule: every path parameter is required,
        # regardless of what (or whether) the document's own
        # "required" key says - a placeholder with no value can never
        # be safely omitted from the URL it is part of.
        required = True if location == "path" else bool(param.get("required", False))
        parameters.append(ServiceParameter(name=str(param_name), location=location, required=required))

    raw_body = op_obj.get("requestBody")
    schema = {}
    if isinstance(raw_body, dict):
        content = raw_body.get("content")
        media = content.get("application/json") if isinstance(content, dict) else None
        schema = media.get("schema") if isinstance(media, dict) else {}
        schema = schema if isinstance(schema, dict) else {}
    required_body_fields = set(schema.get("required") or []) if isinstance(schema.get("required"), list) else set()
    raw_properties = schema.get("properties")
    for field_name in raw_properties if isinstance(raw_properties, dict) else ():
        parameters.append(
            ServiceParameter(
                name=str(field_name), location="body", required=field_name in required_body_fields,
            )
        )

    return tuple(parameters)


def _try_openapi(name: str, base_url: str, candidate_path: str, timeout: float) -> DiscoveryResult:
    """Source 2: a bounded subset of OpenAPI/Swagger at one of the real,
    conventional locations - the genuinely open-ended source, since it
    needs no accommodation from the service at all, only that it
    already publishes what most real APIs already do.
    """
    url = base_url + candidate_path
    payload = _fetch_document(url, timeout)

    is_openapi3 = isinstance(payload.get("openapi"), str) and payload["openapi"].startswith("3.")
    is_swagger2 = payload.get("swagger") == "2.0"
    if not (is_openapi3 or is_swagger2):
        raise _SourceNotFound(f"'{url}' is not a recognisable OpenAPI/Swagger document")

    raw_paths = payload.get("paths")
    if not isinstance(raw_paths, dict) or not raw_paths:
        raise _SourceNotFound(f"'{url}' declares no usable 'paths'")

    # OpenAPI's own inheritance rule: a document-level 'security' applies
    # to every operation that does not declare its own - read once, per
    # document, never per operation guessed.
    document_security = payload.get("security")

    operations: "list[ServiceOperation]" = []
    declared_count = 0
    for path_key in sorted(raw_paths):
        path_item = raw_paths.get(path_key)
        if not isinstance(path_item, dict):
            continue
        for method in _OPENAPI_METHODS:
            op_obj = path_item.get(method)
            if not isinstance(op_obj, dict):
                continue
            # Counted here, before any of the filters below - "declared"
            # means "a real GET/POST operation entry existed in the
            # document", matching this field's own docstring promise
            # ("before any were dropped as ... parameterised or over the
            # bound"), not merely "survived every filter".
            declared_count += 1
            if len(operations) >= _MAX_OPENAPI_OPERATIONS:
                continue
            op_security = op_obj.get("security", document_security)
            requires_auth = bool(op_security)
            fallback_name = f"{method}_{path_key.strip('/').replace('/', '_')}" or method
            raw_name = op_obj.get("operationId") or fallback_name
            description = str(op_obj.get("summary") or op_obj.get("description") or "")[:500]
            try:
                operations.append(
                    ServiceOperation(
                        name=str(raw_name), method=method.upper(), path=path_key,
                        requires_auth=requires_auth, description=description,
                        # Data-flow-through-execution checkpoint: a
                        # parameterised path (e.g. /orders/{id}) is only
                        # ever accepted when the document itself
                        # declares a matching 'in: path' parameter for
                        # every placeholder - ServiceOperation's own
                        # construction enforces that both ways (see its
                        # docstring), so an inconsistent or
                        # under-declared path is skipped exactly the
                        # same "invalid, not fabricated" way any other
                        # malformed operation already is, via the
                        # identical except ValueError below - never a
                        # second check duplicated here.
                        parameters=_openapi_parameters(op_obj),
                    )
                )
            except ValueError:
                continue

    if not operations:
        raise _SourceNotFound(
            f"'{url}' declared paths, but none were individually usable (GET/POST only)"
        )

    info = payload.get("info")
    title = str(info.get("title", "")) if isinstance(info, dict) else ""

    try:
        descriptor = ServiceDescriptor(
            name=name, base_url=base_url, operations=_dedup_by_name(operations),
            description=title[:500],
        )
    except ValueError as exc:
        raise ServiceDiscoveryError(str(exc)) from exc

    return DiscoveryResult(
        descriptor=descriptor, source_url=url, source_kind="openapi",
        declared_operation_count=declared_count,
    )
