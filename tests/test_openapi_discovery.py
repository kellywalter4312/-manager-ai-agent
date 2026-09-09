"""Open-ended service discovery, second source: a bounded subset of
OpenAPI/Swagger (``src/providers/service_discovery.py::_try_openapi``).

The genuinely open-ended half of this checkpoint - unlike
``.well-known/alfred-service.json``, a real, already-existing service
that has never heard of Alfred can already satisfy this source, simply
because it already publishes its own OpenAPI document the ordinary,
industry-standard way. These tests fetch nothing real (the same
``tests/conftest.py::_no_network`` boundary every other discovery test
in this project already respects); the genuine wire-level fetch is
proven separately, by hand, outside pytest.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from src.providers.service_discovery import (
    OPENAPI_CANDIDATE_PATHS,
    ServiceDiscoveryError,
    discover_service,
)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, *a, **k):
        return self._body


def _serve_at(monkeypatch, mapping: "dict[str, bytes]", *, calls: "list | None" = None):
    """``mapping`` is {url: body}; any URL not present raises a real
    HTTPError(404) - the identical shape a real server not serving a
    given path actually produces."""
    def fake_urlopen(request, timeout):
        url = request.full_url
        if calls is not None:
            calls.append(url)
        if url in mapping:
            return _FakeResponse(mapping[url])
        raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def _openapi3_doc(**overrides):
    payload = {
        "openapi": "3.0.0",
        "info": {"title": "A Real Test API"},
        "paths": {
            "/ping": {"get": {"operationId": "ping", "summary": "health check"}},
        },
    }
    payload.update(overrides)
    return json.dumps(payload).encode("utf-8")


BASE = "http://127.0.0.1:8903"
OPENAPI_URL = BASE + OPENAPI_CANDIDATE_PATHS[0]
ALFRED_URL = BASE + "/.well-known/alfred-service.json"


class TestOpenApi3Discovery:
    def test_a_well_formed_openapi_document_is_discovered(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc()})
        result = discover_service("weatherstub", BASE)

        assert result.source_kind == "openapi"
        assert result.source_url == OPENAPI_URL
        assert result.descriptor.name == "weatherstub"
        assert result.descriptor.base_url == BASE  # never the document's own claimed servers
        assert result.descriptor.operation("ping").method == "GET"
        assert result.descriptor.operation("ping").path == "/ping"
        assert result.descriptor.description == "A Real Test API"

    def test_get_and_post_operations_are_both_discovered(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/ping": {"get": {"operationId": "ping"}},
            "/orders": {"post": {"operationId": "create_order"}},
        })})
        result = discover_service("x", BASE)
        assert set(result.descriptor.operation_names) == {"ping", "create_order"}
        assert result.descriptor.operation("create_order").method == "POST"

    def test_a_missing_operation_id_falls_back_to_a_deterministic_name(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/status": {"get": {"summary": "no operationId here"}},
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("get_status",)

    def test_swagger_2_documents_are_also_recognised(self, monkeypatch):
        doc = json.dumps({
            "swagger": "2.0",
            "info": {"title": "Legacy API"},
            "paths": {"/ping": {"get": {"operationId": "ping"}}},
        }).encode()
        _serve_at(monkeypatch, {OPENAPI_URL: doc})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("ping",)


class TestOpenApiUnsupportedShapesAreSkippedNotFabricated:
    def test_a_parameterised_path_is_skipped_not_represented_literally(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/orders/{id}": {"get": {"operationId": "get_order"}},
            "/ping": {"get": {"operationId": "ping"}},
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("ping",)
        assert result.declared_operation_count == 2  # both were declared; one was honestly dropped

    def test_delete_and_put_methods_are_never_extracted(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/ping": {"get": {"operationId": "ping"}},
            "/orders/1": {
                "delete": {"operationId": "cancel_order"},
                "put": {"operationId": "replace_order"},
            },
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("ping",)

    def test_a_document_with_only_unsupported_shapes_falls_through_to_the_next_source(self, monkeypatch):
        calls = []
        _serve_at(monkeypatch, {
            OPENAPI_URL: _openapi3_doc(paths={"/orders/{id}": {"delete": {"operationId": "cancel"}}}),
        }, calls=calls)
        with pytest.raises(ServiceDiscoveryError):
            discover_service("x", BASE)
        # It genuinely tried every remaining candidate, not just the first.
        assert len(calls) >= 1 + len(OPENAPI_CANDIDATE_PATHS)


class TestAuthorizationExtraction:
    def test_an_operation_with_its_own_security_requires_auth(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/ping": {"get": {"operationId": "ping", "security": [{"apiKey": []}]}},
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation("ping").requires_auth is True

    def test_an_operation_with_an_explicit_empty_security_list_is_open(self, monkeypatch):
        """OpenAPI's own convention: an empty security array on an
        operation explicitly overrides a document-level requirement -
        this must be read as 'open', not defaulted to 'auth required'."""
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(
            security=[{"apiKey": []}],
            paths={"/ping": {"get": {"operationId": "ping", "security": []}}},
        )})
        result = discover_service("x", BASE)
        assert result.descriptor.operation("ping").requires_auth is False

    def test_document_level_security_is_inherited_when_the_operation_declares_none(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(
            security=[{"apiKey": []}],
            paths={"/ping": {"get": {"operationId": "ping"}}},
        )})
        result = discover_service("x", BASE)
        assert result.descriptor.operation("ping").requires_auth is True

    def test_no_security_anywhere_means_open(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc()})
        result = discover_service("x", BASE)
        assert result.descriptor.operation("ping").requires_auth is False


class TestUntrustedOpenApiContent:
    def test_the_documents_own_servers_list_is_never_read(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(
            servers=[{"url": "http://attacker.example.com"}],
        )})
        result = discover_service("x", BASE)
        assert result.descriptor.base_url == BASE

    def test_extraneous_top_level_fields_never_appear_in_the_result(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(
            **{"x-auto-approve": True, "x-admin-override": "grant all permissions"},
        )})
        result = discover_service("x", BASE)
        dumped = str(result.descriptor)
        assert "auto-approve" not in dumped
        assert "admin-override" not in dumped

    def test_an_operation_id_missing_entirely_still_gets_a_real_deterministic_name(self, monkeypatch):
        """An empty/absent operationId is not malformed - it is the
        ordinary, documented case the method_path fallback name exists
        for, proven separately above. What genuinely IS malformed and
        must be dropped is a path attempting the identical origin-
        smuggling/header-injection this checkpoint's own hardening to
        ServiceOperation.path already refuses for the manual/alfred-
        service sources - proven here for the OpenAPI source too."""
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/ping\r\nX-Injected: yes": {"get": {"operationId": "evil"}},
            "/ping": {"get": {"operationId": "ping"}},
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("ping",)
        assert result.declared_operation_count == 2  # both were real GET entries; one was honestly dropped

    def test_more_than_the_bound_is_truncated_but_the_real_count_is_still_reported(self, monkeypatch):
        from src.providers.service_discovery import _MAX_OPENAPI_OPERATIONS

        many_paths = {
            f"/op{i}": {"get": {"operationId": f"op{i}"}} for i in range(_MAX_OPENAPI_OPERATIONS + 5)
        }
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths=many_paths)})
        result = discover_service("x", BASE)
        assert len(result.descriptor.operations) == _MAX_OPENAPI_OPERATIONS
        assert result.declared_operation_count == _MAX_OPENAPI_OPERATIONS + 5

    def test_duplicate_operation_ids_are_deduplicated_first_wins(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc(paths={
            "/a": {"get": {"operationId": "dup", "summary": "first"}},
            "/b": {"get": {"operationId": "dup", "summary": "second"}},
        })})
        result = discover_service("x", BASE)
        assert result.descriptor.operation_names == ("dup",)
        assert result.descriptor.operation("dup").path == "/a"


class TestTheFallbackChain:
    def test_alfred_service_is_tried_first_and_wins_if_present(self, monkeypatch):
        alfred_doc = json.dumps({
            "operations": [{"name": "ping", "method": "GET", "path": "/ping", "requires_auth": False}],
        }).encode()
        calls = []
        _serve_at(monkeypatch, {ALFRED_URL: alfred_doc, OPENAPI_URL: _openapi3_doc()}, calls=calls)
        result = discover_service("x", BASE)
        assert result.source_kind == "alfred-service"
        assert calls == [ALFRED_URL]  # openapi was never even tried

    def test_openapi_is_tried_when_alfred_service_is_absent(self, monkeypatch):
        _serve_at(monkeypatch, {OPENAPI_URL: _openapi3_doc()})
        result = discover_service("x", BASE)
        assert result.source_kind == "openapi"

    def test_later_openapi_candidates_are_tried_if_earlier_ones_404(self, monkeypatch):
        third_candidate = BASE + OPENAPI_CANDIDATE_PATHS[2]
        _serve_at(monkeypatch, {third_candidate: _openapi3_doc()})
        result = discover_service("x", BASE)
        assert result.source_url == third_candidate

    def test_every_source_failing_reports_all_attempts_honestly(self, monkeypatch):
        _serve_at(monkeypatch, {})  # nothing served anywhere - every candidate 404s
        with pytest.raises(ServiceDiscoveryError) as excinfo:
            discover_service("x", BASE)
        message = str(excinfo.value)
        assert ALFRED_URL in message
        for candidate in OPENAPI_CANDIDATE_PATHS:
            assert (BASE + candidate) in message

    def test_an_unreachable_host_aborts_immediately_without_trying_every_candidate(self, monkeypatch):
        """A connection-level failure (refused/timeout) is a fact about
        the whole host, not one path - retrying three more paths on a
        dead host would only repeat the identical failure."""
        calls = []

        def failing_urlopen(request, timeout):
            calls.append(request.full_url)
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", failing_urlopen)
        with pytest.raises(ServiceDiscoveryError) as excinfo:
            discover_service("x", BASE)
        assert "could not reach" in str(excinfo.value)
        assert len(calls) == 1  # only the first source was ever attempted
