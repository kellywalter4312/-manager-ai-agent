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
from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation, ServiceParameter
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


def _mixed_descriptor(**kw):
    """One safe read (no credential needed), one consequential write
    (credential required) - the exact shape that surfaced the real,
    live 'a whole connection is blocked by one operation's own
    requirement' finding this checkpoint fixes."""
    defaults = dict(
        name="weatherstub", base_url="http://127.0.0.1:9911",
        operations=(
            ServiceOperation(name="ping", method="GET", path="/ping", requires_auth=False),
            ServiceOperation(name="place_order", method="POST", path="/orders", requires_auth=True),
        ),
    )
    defaults.update(kw)
    return ServiceDescriptor(**defaults)


class TestOperationLevelAuthorization:
    """Operation-level authorization (this checkpoint): a connection's
    own blanket .authorization stays unchanged (still conservative, on
    purpose, for a caller that genuinely wants "is anything here usable
    at all"); .authorization_for(operation) is the new, precise check a
    caller uses to ask about one specific operation."""

    def test_a_safe_operation_is_not_required_even_when_a_sibling_operation_needs_auth(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.authorization_for("ping") is AuthorizationState.NOT_REQUIRED

    def test_a_credential_gated_operation_is_unauthorized_with_no_account(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.authorization_for("place_order") is AuthorizationState.UNAUTHORIZED

    def test_a_credential_gated_operation_is_authorized_once_an_account_exists(self):
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="x")
        conn = DeclaredConnection(_mixed_descriptor(), account)
        assert conn.authorization_for("place_order") is AuthorizationState.AUTHORIZED

    def test_the_whole_connections_blanket_authorization_is_unchanged_and_still_conservative(self):
        """.authorization (the pre-existing property) must keep its own
        prior, intentionally conservative meaning - this checkpoint adds
        a more precise check alongside it, never silently changes what
        the old one already meant for every existing caller of it."""
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.authorization is AuthorizationState.UNAUTHORIZED

    def test_an_unknown_operation_raises_rather_than_returning_a_state(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        with pytest.raises(KeyError):
            conn.authorization_for("does_not_exist")


class TestRequiresConfirmationFor:
    def test_a_get_operation_never_requires_confirmation(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.requires_confirmation_for("ping") is False

    def test_a_post_operation_always_requires_confirmation(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.requires_confirmation_for("place_order") is True

    def test_an_unknown_operation_raises_rather_than_returning_a_default(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        with pytest.raises(KeyError):
            conn.requires_confirmation_for("does_not_exist")


class TestOperationDescriptionFor:
    """Semantic operation-selection checkpoint: the one piece of
    metadata beyond an operation's bare name a caller (_select_operation,
    src/manager/capabilities.py) can use to tell what it actually does."""

    def test_returns_the_operations_own_declared_description(self):
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.operation_description_for("ping") == ""

        described = ServiceDescriptor(
            name="weatherstub", base_url="http://127.0.0.1:9911",
            operations=(
                ServiceOperation(
                    name="ping", method="GET", path="/ping", requires_auth=False,
                    description="reports whether the service is reachable",
                ),
            ),
        )
        assert (
            DeclaredConnection(described, None).operation_description_for("ping")
            == "reports whether the service is reachable"
        )

    def test_an_unknown_operation_returns_an_empty_string_not_an_exception(self):
        """Unlike authorization_for/requires_confirmation_for, this is
        never a hard failure for a caller merely scoring candidates -
        an empty string is treated identically to 'nothing more to go
        on than the name'."""
        conn = DeclaredConnection(_mixed_descriptor(), None)
        assert conn.operation_description_for("does_not_exist") == ""


class TestExecuteWithParameters:
    def test_get_parameters_become_a_real_safely_escaped_query_string(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn = DeclaredConnection(_descriptor(), None)
        conn.execute("ping", city="New York", days=3)
        assert seen["url"].startswith("http://127.0.0.1:9911/ping?")
        assert "city=New+York" in seen["url"] or "city=New%20York" in seen["url"]
        assert "days=3" in seen["url"]

    def test_a_value_attempting_to_smuggle_a_new_query_key_is_safely_escaped_not_injected(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn = DeclaredConnection(_descriptor(), None)
        conn.execute("ping", city="x&admin=true")
        # The literal attempted injection must appear only as an escaped
        # value, never as a second, real "admin" query parameter.
        assert "&admin=true" not in seen["url"]
        assert "admin%3Dtrue" in seen["url"] or "admin=true" not in seen["url"].split("?", 1)[1].split("&")

    def test_post_parameters_become_a_real_json_body(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["body"] = request.data
            seen["content_type"] = request.get_header("Content-type")
            return _FakeResponse(b'{"id": "order-1"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="x")
        conn = DeclaredConnection(_mixed_descriptor(), account)
        result = conn.execute("place_order", item="widget", quantity=2)

        import json
        assert json.loads(seen["body"]) == {"item": "widget", "quantity": 2}
        assert seen["content_type"] == "application/json"
        assert result.ok

    def test_a_nested_object_parameter_is_refused_not_serialised_blindly(self):
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping", filters={"nested": "object"})
        assert not result.ok
        assert "filters" in result.detail

    def test_a_list_parameter_is_also_refused(self):
        conn = DeclaredConnection(_descriptor(), None)
        result = conn.execute("ping", tags=["a", "b"])
        assert not result.ok

    def test_no_parameters_still_works_exactly_as_before(self, monkeypatch):
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse(b'{"ok": true}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn = DeclaredConnection(_descriptor(), None)
        conn.execute("ping")
        assert seen["url"] == "http://127.0.0.1:9911/ping"  # no trailing '?', unchanged from before

    def test_a_real_service_rejection_is_reported_honestly_never_retried_with_a_guess(self, monkeypatch):
        """The service's own real, honest rejection (a 400 for a
        missing/invalid parameter) is reported plainly - Alfred never
        fabricates the missing value and retries."""
        def fake_urlopen(request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 400, "Bad Request", {}, io.BytesIO(b'{"error": "quantity is required"}')
            )

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        account = ProviderAccount(provider="weatherstub", account_id="default", credential="x")
        conn = DeclaredConnection(_mixed_descriptor(), account)
        result = conn.execute("place_order", item="widget")

        assert not result.ok
        assert "400" in result.detail
        assert "quantity is required" in result.detail
        # The service WAS genuinely reached, even though it rejected the
        # request - that is real evidence of availability, not absence.
        assert conn.availability.state is AvailabilityState.AVAILABLE


class _FakeResponse2:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._body


class TestParameterLocationExecution:
    """Data-flow-through-execution checkpoint: a declared parameter's
    real value is injected exactly where the service itself said it
    goes - path, query, or JSON body - never fabricated when absent."""

    def _op(self, method="GET", path="/x", **kw):
        return ServiceOperation(name="op", method=method, path=path, requires_auth=False, **kw)

    def test_a_path_parameter_is_substituted_into_the_url(self, monkeypatch):
        op = self._op(
            path="/warehouse/{shelf_locator}",
            parameters=(ServiceParameter(name="shelf_locator", location="path", required=True),),
        )
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        result = conn.execute("op", shelf_locator="a1-north")
        assert result.ok
        assert seen["url"] == "http://x.test/warehouse/a1-north"

    def test_a_path_parameter_value_cannot_smuggle_an_extra_path_segment(self, monkeypatch):
        """A value containing '/' is percent-encoded, never allowed to
        create a new path segment - path traversal/injection stays
        impossible regardless of what the value contains."""
        op = self._op(
            path="/warehouse/{shelf_locator}",
            parameters=(ServiceParameter(name="shelf_locator", location="path", required=True),),
        )
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op", shelf_locator="../../etc/passwd")
        assert "/../" not in seen["url"]
        assert seen["url"] == "http://x.test/warehouse/..%2F..%2Fetc%2Fpasswd"

    def test_a_missing_required_path_parameter_fails_honestly_with_no_http_call(self, monkeypatch):
        op = self._op(
            path="/warehouse/{shelf_locator}",
            parameters=(ServiceParameter(name="shelf_locator", location="path", required=True),),
        )
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        called = []
        monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(1))

        result = conn.execute("op")
        assert not result.ok
        assert result.status is ConnectionStatus.FAILED
        assert "shelf_locator" in result.detail
        assert not called

    def test_a_query_parameter_reaches_the_real_query_string(self, monkeypatch):
        op = self._op(parameters=(ServiceParameter(name="keyword", location="query", required=True),))
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op", keyword="widgets")
        assert seen["url"] == "http://x.test/x?keyword=widgets"

    def test_a_body_parameter_reaches_the_real_json_body(self, monkeypatch):
        op = self._op(
            method="POST",
            parameters=(ServiceParameter(name="payload_reference", location="body", required=True),),
        )
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["body"] = request.data
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op", payload_reference="ref-99")
        import json

        assert json.loads(seen["body"]) == {"payload_reference": "ref-99"}

    def test_an_optional_parameter_may_be_omitted(self, monkeypatch):
        op = self._op(parameters=(ServiceParameter(name="verbose", location="query", required=False),))
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op")
        assert seen["url"] == "http://x.test/x"

    def test_an_optional_parameter_is_used_when_explicitly_provided(self, monkeypatch):
        op = self._op(parameters=(ServiceParameter(name="verbose", location="query", required=False),))
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op", verbose="true")
        assert seen["url"] == "http://x.test/x?verbose=true"

    def test_an_arbitrarily_named_parameter_works_generically(self, monkeypatch):
        """No special case for 'id'/'key' - a deliberately unusual
        declared name works identically."""
        op = self._op(
            path="/x/{zzz_totally_arbitrary_field_47}",
            parameters=(ServiceParameter(name="zzz_totally_arbitrary_field_47", location="path", required=True),),
        )
        conn = DeclaredConnection(ServiceDescriptor(name="x", base_url="http://x.test", operations=(op,)), None)
        seen = {}

        def fake_urlopen(request, timeout):
            seen["url"] = request.full_url
            return _FakeResponse2(b"{}")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        conn.execute("op", zzz_totally_arbitrary_field_47="value-1")
        assert seen["url"] == "http://x.test/x/value-1"
