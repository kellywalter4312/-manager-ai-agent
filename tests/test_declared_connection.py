"""Open-ended service discovery, execution layer:
``DeclaredConnection`` (``src/providers/declared_connection.py``) - the
one real ``Connection`` implementation any declared service gets,
without a new Python subclass.

``tests/conftest.py::_no_network`` makes any real ``socket.socket.connect``
(and therefore ``urllib.request.urlopen``) a loud, session-wide
``AssertionError`` for this whole suite, on purpose - the same rule
every other real-network provider in this project (Wikipedia's own live-
info provider included) is already tested under. These tests exercise
the real business logic - operation resolution, authorization gating,
credential injection, honest success/failure mapping, truncation - by
substituting a controlled ``urllib.request.urlopen`` (never a fake
network device, never a mocked Connection method), the identical
boundary a real call would cross. The actual wire-level HTTP round trip
against a genuine local server is proven separately, by hand, outside
pytest - see this checkpoint's own commit message.
"""

from __future__ import annotations

import io
import urllib.error
import urllib.request

import pytest

from src.providers.availability import AvailabilityState
from src.providers.connection import AuthorizationState, ConnectionStatus
from src.providers.declared_connection import DeclaredConnection
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation
from src.services.credentials.account import ProviderAccount


def _descriptor(**kw):
    defaults = dict(
        name="weatherstub", base_url="http://127.0.0.1:9911",
        operations=(
            ServiceOperation(name="ping", method="GET", path="/ping", requires_auth=False),
            ServiceOperation(name="forecast", method="GET", path="/forecast", requires_auth=True),
        ),
    )
    defaults.update(kw)
    return ServiceDescriptor(**defaults)


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class TestAuthorizationReflectsWhatIsDeclaredAndOnFile:
    def test_not_required_when_no_operation_needs_a_credential(self):
        d = ServiceDescriptor(
            name="open-service", base_url="http://x",
            operations=(ServiceOperation(name="ping", requires_auth=False),),
        )
        conn = DeclaredConnection(d, None)
        assert conn.authorization is AuthorizationState.NOT_REQUIRED

    def test_unauthorized_when_an_operation_needs_a_credential_and_none_is_on_file(self):
        conn = DeclaredConnection(_descriptor(), None)
        assert conn.authorization is AuthorizationState.UNAUTHORIZED

    def test_authorized_once_a_credential_is_on_file(self):
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="secret-token")
        conn = DeclaredConnection(_descriptor(), account)
        assert conn.authorization is AuthorizationState.AUTHORIZED

    def test_supported_operations_matches_the_descriptor_exactly(self):
        conn = DeclaredConnection(_descriptor(), None)
        assert conn.supported_operations == ("ping", "forecast")

    def test_availability_starts_unknown_never_invented(self):
        conn = DeclaredConnection(_descriptor(), None)
        assert conn.availability.state is AvailabilityState.UNKNOWN


class TestExecuteHonestGating:
    def test_an_undeclared_operation_is_unsupported_and_never_attempted(self, monkeypatch):
        called = []
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(1))
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("delete_everything")
        assert result.status is ConnectionStatus.UNSUPPORTED
        assert not called

    def test_an_auth_required_operation_with_no_account_is_unauthorized_and_never_attempted(self, monkeypatch):
        called = []
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(1))
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("forecast")
        assert result.status is ConnectionStatus.UNAUTHORIZED
        assert not called
        # A refused attempt must not be mistaken for evidence either way.
        assert conn.availability.state is AvailabilityState.UNKNOWN

    def test_an_open_operation_needs_no_account_and_is_attempted(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b'{"ok": true}')
        )
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping")
        assert result.ok
        assert result.status is ConnectionStatus.SUCCEEDED


class TestExecuteRealCallShape:
    def test_the_request_targets_exactly_the_declared_base_url_plus_path(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            seen["method"] = request.get_method()
            seen["timeout"] = timeout
            return _FakeResponse(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn = DeclaredConnection(_descriptor(), None, timeout=3.0)
        conn.execute("ping")
        assert seen["url"] == "http://127.0.0.1:9911/ping"
        assert seen["method"] == "GET"
        assert seen["timeout"] == 3.0

    def test_the_credential_is_injected_as_a_bearer_header_never_logged_in_the_result(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["auth_header"] = request.get_header("Authorization")
            return _FakeResponse(b'{"forecast": "sunny"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="top-secret")
        conn = DeclaredConnection(_descriptor(), account)
        result = conn.execute("forecast")
        assert seen["auth_header"] == "Bearer top-secret"
        assert "top-secret" not in result.describe()

    def test_an_open_operation_carries_no_authorization_header_even_with_an_account_on_file(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["auth_header"] = request.get_header("Authorization")
            return _FakeResponse(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="unused-here")
        conn = DeclaredConnection(_descriptor(), account)
        conn.execute("ping")
        assert seen["auth_header"] is None

    def test_a_genuine_success_updates_availability_and_returns_the_real_body(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b'{"temp": 21}')
        )
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping")
        assert result.ok
        assert "21" in result.detail
        assert conn.availability.state is AvailabilityState.AVAILABLE

    def test_a_network_failure_is_reported_honestly_never_raised_never_fabricated(self, monkeypatch):
        def fake_urlopen(*a, **k):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping")
        assert result.status is ConnectionStatus.FAILED
        assert not result.ok
        assert conn.availability.state is AvailabilityState.UNAVAILABLE

    def test_a_non_json_response_is_still_returned_as_plain_text_never_a_crash(self, monkeypatch):
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda *a, **k: _FakeResponse(b"plain text, not json")
        )
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping")
        assert result.ok
        assert result.detail == "plain text, not json"

    def test_an_oversized_response_is_truncated_not_rejected(self, monkeypatch):
        huge = ('{"data": "' + ("x" * 5000) + '"}').encode()
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _FakeResponse(huge))
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping")
        assert result.ok
        assert len(result.detail) <= 2000

    def test_construction_rejects_a_non_positive_timeout(self):
        with pytest.raises(ValueError):
            DeclaredConnection(_descriptor(), None, timeout=0)
