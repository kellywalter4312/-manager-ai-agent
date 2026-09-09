"""Open-ended service discovery, persistence layer:
``src/services/connections/service_registry.py`` - the same
"read fresh, write whole" file-backed contract
``account_store_file.py`` already proves for credentials, applied here
to what a service *is* rather than its secret.

Every test writes to a real file under ``tmp_path`` and reads it back
through a fresh call - never asserting against in-memory state alone -
so genuine restart/reload behaviour is what's actually proven.
"""

from __future__ import annotations

import json

from src.providers.service_descriptor import ServiceDescriptor, ServiceOperation, ServiceParameter
from src.services.connections.service_registry import (
    declare_service,
    forget_service,
    load_service_registry,
    save_service_registry,
)


def _descriptor(name="weatherstub", **kw):
    return ServiceDescriptor(
        name=name, base_url="http://127.0.0.1:9911",
        operations=(ServiceOperation(name="ping", requires_auth=False),),
        **kw,
    )


class TestLoadingAMissingOrEmptyRegistry:
    def test_a_missing_file_is_an_honestly_empty_registry_not_an_error(self, tmp_path):
        assert load_service_registry(tmp_path / "nowhere.json") == {}

    def test_a_malformed_file_is_tolerated_as_empty(self, tmp_path):
        path = tmp_path / "services.json"
        path.write_text("{not valid json", encoding="utf-8")
        assert load_service_registry(path) == {}

    def test_one_bad_entry_does_not_take_down_the_rest(self, tmp_path):
        path = tmp_path / "services.json"
        path.write_text(
            json.dumps({
                "services": [
                    {"name": "gmail", "base_url": "http://x", "operations": [{"name": "x"}]},  # reserved, invalid
                    {"name": "good", "base_url": "http://y", "operations": [{"name": "ping"}]},
                ]
            }),
            encoding="utf-8",
        )
        registry = load_service_registry(path)
        assert set(registry) == {"good"}


class TestDeclareAndForgetRoundTrip:
    def test_declaring_persists_to_a_real_file(self, tmp_path):
        path = tmp_path / "services.json"
        declare_service(_descriptor(), path)
        assert path.exists()
        raw = json.loads(path.read_text(encoding="utf-8"))
        assert raw["services"][0]["name"] == "weatherstub"

    def test_a_fresh_load_after_declaring_sees_the_identical_descriptor(self, tmp_path):
        """Restart/reload proof: a brand-new call, simulating a fresh
        process, reads back exactly what a prior call wrote."""
        path = tmp_path / "services.json"
        declare_service(_descriptor(description="a real test fixture"), path)

        reloaded = load_service_registry(path)
        assert set(reloaded) == {"weatherstub"}
        d = reloaded["weatherstub"]
        assert d.base_url == "http://127.0.0.1:9911"
        assert d.description == "a real test fixture"
        assert d.operation_names == ("ping",)
        assert d.operation("ping").requires_auth is False

    def test_declaring_the_same_name_twice_replaces_not_duplicates(self, tmp_path):
        path = tmp_path / "services.json"
        declare_service(_descriptor(), path)
        declare_service(_descriptor(description="updated"), path)
        registry = load_service_registry(path)
        assert len(registry) == 1
        assert registry["weatherstub"].description == "updated"

    def test_multiple_services_persist_independently(self, tmp_path):
        path = tmp_path / "services.json"
        declare_service(_descriptor("weatherstub"), path)
        declare_service(_descriptor("bookingstub"), path)
        registry = load_service_registry(path)
        assert set(registry) == {"weatherstub", "bookingstub"}

    def test_forgetting_removes_it_and_reports_true(self, tmp_path):
        path = tmp_path / "services.json"
        declare_service(_descriptor(), path)
        assert forget_service("weatherstub", path) is True
        assert load_service_registry(path) == {}

    def test_forgetting_an_undeclared_name_is_an_honest_idempotent_no_op(self, tmp_path):
        path = tmp_path / "services.json"
        assert forget_service("never-declared", path) is False

    def test_forgetting_one_service_leaves_another_untouched(self, tmp_path):
        path = tmp_path / "services.json"
        declare_service(_descriptor("weatherstub"), path)
        declare_service(_descriptor("bookingstub"), path)
        forget_service("weatherstub", path)
        assert set(load_service_registry(path)) == {"bookingstub"}


class TestSaveOverwritesWhole:
    def test_save_rewrites_the_whole_file_not_a_patch(self, tmp_path):
        path = tmp_path / "services.json"
        save_service_registry({"a": _descriptor("a")}, path)
        save_service_registry({"b": _descriptor("b")}, path)
        registry = load_service_registry(path)
        assert set(registry) == {"b"}


class TestParameterRoundTrip:
    """Data-flow-through-execution checkpoint: a real bug this
    checkpoint's own live verification caught - ServiceOperation's new
    ``parameters`` field was silently dropped on save/load, which broke
    a path-parameterised operation the moment it was reloaded (its own
    {placeholder} then had no matching declared parameter at all).
    Every declared parameter must survive a real file round trip."""

    def test_a_path_parameter_survives_a_real_round_trip(self, tmp_path):
        path = tmp_path / "services.json"
        descriptor = ServiceDescriptor(
            name="x", base_url="http://x.test",
            operations=(
                ServiceOperation(
                    name="inspect_shelf", method="GET", path="/warehouse/{shelf_locator}",
                    requires_auth=False,
                    parameters=(ServiceParameter(name="shelf_locator", location="path", required=True),),
                ),
            ),
        )
        declare_service(descriptor, path)

        reloaded = load_service_registry(path)["x"].operation("inspect_shelf")
        assert reloaded.parameter("shelf_locator").location == "path"
        assert reloaded.parameter("shelf_locator").required is True

    def test_an_optional_query_parameter_survives_a_real_round_trip(self, tmp_path):
        path = tmp_path / "services.json"
        descriptor = ServiceDescriptor(
            name="x", base_url="http://x.test",
            operations=(
                ServiceOperation(
                    name="search_catalogue", method="GET", path="/search", requires_auth=False,
                    parameters=(ServiceParameter(name="page_token", location="query", required=False),),
                ),
            ),
        )
        declare_service(descriptor, path)

        reloaded = load_service_registry(path)["x"].operation("search_catalogue")
        assert reloaded.parameter("page_token").required is False

    def test_a_body_parameter_survives_a_real_round_trip(self, tmp_path):
        path = tmp_path / "services.json"
        descriptor = ServiceDescriptor(
            name="x", base_url="http://x.test",
            operations=(
                ServiceOperation(
                    name="create_entry", method="POST", path="/entries", requires_auth=True,
                    parameters=(ServiceParameter(name="payload_reference", location="body", required=True),),
                ),
            ),
        )
        declare_service(descriptor, path)

        reloaded = load_service_registry(path)["x"].operation("create_entry")
        assert reloaded.parameter("payload_reference").location == "body"

    def test_an_operation_declared_before_this_field_existed_still_loads(self, tmp_path):
        """A file with no 'parameters' key at all on an operation - the
        exact shape every file this project wrote before this
        checkpoint has - still loads, with no parameters, never a
        crash."""
        path = tmp_path / "services.json"
        path.write_text(
            json.dumps({
                "services": [
                    {
                        "name": "x", "base_url": "http://x.test",
                        "operations": [{"name": "ping", "method": "GET", "path": "/ping"}],
                    },
                ],
            }),
            encoding="utf-8",
        )
        reloaded = load_service_registry(path)["x"].operation("ping")
        assert reloaded.parameters == ()
