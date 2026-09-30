"""Open-ended service discovery, foundation layer: ``ServiceDescriptor``/
``ServiceOperation`` (``src/providers/service_descriptor.py``).

Pure data validation - no network, no persistence, no Connection. What
this proves: a legitimate, previously-unknown service can be
represented completely as data (a name, one base URL, a finite
operation list), and an invalid or dangerous shape is refused at
construction, the same "invalid states unrepresentable" discipline
every other frozen contract in this project already holds itself to.
"""

from __future__ import annotations

import pytest

from src.providers.service_descriptor import (
    RESERVED_SERVICE_NAMES,
    ServiceDescriptor,
    ServiceOperation,
    ServiceParameter,
)


class TestServiceOperationValidation:
    def test_a_well_formed_operation_is_accepted(self):
        op = ServiceOperation(name="ping", method="get", path="ping", requires_auth=False)
        assert op.name == "ping"
        assert op.method == "GET"  # normalised uppercase
        assert op.path == "/ping"  # leading slash added
        assert op.requires_auth is False

    def test_empty_name_is_refused(self):
        with pytest.raises(ValueError):
            ServiceOperation(name="   ")

    def test_an_unrecognised_method_is_refused(self):
        """No DELETE/PUT/PATCH/TRACE/CONNECT - GET and POST only, the
        smallest set that can prove the mechanism without opening a
        wider real-world write surface than this checkpoint asked for."""
        with pytest.raises(ValueError):
            ServiceOperation(name="wipe", method="DELETE", path="/x")

    def test_name_is_normalised_to_a_safe_identifier_shape(self):
        op = ServiceOperation(name="Check Availability")
        assert op.name == "check_availability"

    def test_description_whitespace_is_collapsed(self):
        op = ServiceOperation(name="x", description="  a   real   op  ")
        assert op.description == "a real op"

    def test_operations_are_frozen(self):
        op = ServiceOperation(name="x")
        with pytest.raises(Exception):
            op.name = "y"  # type: ignore[misc]


class TestServiceDescriptorValidation:
    def _op(self, name="ping", **kw):
        return ServiceOperation(name=name, **kw)

    def test_a_well_formed_descriptor_is_accepted(self):
        d = ServiceDescriptor(
            name="Weatherstub", base_url="http://127.0.0.1:9911/",
            operations=(self._op(),),
        )
        assert d.name == "weatherstub"  # lowercased
        assert d.base_url == "http://127.0.0.1:9911"  # trailing slash stripped

    def test_empty_name_is_refused(self):
        with pytest.raises(ValueError):
            ServiceDescriptor(name="  ", base_url="http://x", operations=(self._op(),))

    @pytest.mark.parametrize("reserved", sorted(RESERVED_SERVICE_NAMES))
    def test_a_reserved_connection_name_is_refused(self, reserved):
        """'gmail'/'null' are the two built-in connection_factory.py
        names - a declared service must never be able to shadow either,
        or selecting the real Gmail connection could silently resolve
        to a person's own declared data instead."""
        with pytest.raises(ValueError):
            ServiceDescriptor(name=reserved, base_url="http://x", operations=(self._op(),))

    @pytest.mark.parametrize(
        "bad_url", ["not-a-url", "ftp://example.com", "example.com", ""]
    )
    def test_a_non_http_base_url_is_refused(self, bad_url):
        with pytest.raises(ValueError):
            ServiceDescriptor(name="x", base_url=bad_url, operations=(self._op(),))

    def test_no_operations_at_all_is_refused(self):
        with pytest.raises(ValueError):
            ServiceDescriptor(name="x", base_url="http://x", operations=())

    def test_duplicate_operation_names_are_refused(self):
        with pytest.raises(ValueError):
            ServiceDescriptor(
                name="x", base_url="http://x",
                operations=(self._op("ping"), self._op("ping")),
            )

    def test_operation_lookup_is_case_and_space_insensitive(self):
        d = ServiceDescriptor(
            name="x", base_url="http://x", operations=(self._op("check_availability"),),
        )
        assert d.operation("Check Availability") is not None
        assert d.operation("nonexistent") is None

    def test_operation_names_property(self):
        d = ServiceDescriptor(
            name="x", base_url="http://x",
            operations=(self._op("a"), self._op("b")),
        )
        assert d.operation_names == ("a", "b")

    def test_descriptor_is_frozen(self):
        d = ServiceDescriptor(name="x", base_url="http://x", operations=(self._op(),))
        with pytest.raises(Exception):
            d.name = "y"  # type: ignore[misc]


class TestServiceParameterValidation:
    """Data-flow-through-execution checkpoint: the small, closed
    representation of one real input an operation declares - name,
    where it goes (path/query/body), and whether it is required."""

    def test_defaults_to_query_and_required(self):
        p = ServiceParameter(name="anything_at_all")
        assert p.location == "query"
        assert p.required is True

    def test_name_keeps_the_services_own_spelling_and_matches_any_case(self):
        # Phase 11 closure: a parameter's name is its wire name (the JSON
        # field or query key the service reads), so its spelling is kept -
        # lower-casing sent "resourcekey" to an API reading "resourceKey".
        # Whitespace still becomes underscores, and lookup ignores case.
        p = ServiceParameter(name="Resource Key")
        assert p.name == "Resource_Key"
        op = ServiceOperation(name="fetch", method="GET", path="/r", parameters=(ServiceParameter(name="resourceKey"),))
        assert op.parameter("RESOURCEKEY") is op.parameters[0]
        assert op.parameter("resourcekey").name == "resourceKey"

    def test_an_unrecognised_location_is_refused(self):
        with pytest.raises(ValueError):
            ServiceParameter(name="x", location="header")

    def test_optional_is_respected(self):
        p = ServiceParameter(name="x", required=False)
        assert p.required is False

    def test_parameters_are_frozen(self):
        p = ServiceParameter(name="x")
        with pytest.raises(Exception):
            p.name = "y"  # type: ignore[misc]


class TestServiceOperationParameters:
    """ServiceOperation's own bookkeeping over its declared parameters -
    empty by default (every operation declared before this field
    existed is unaffected), and consistent with its own path."""

    def test_empty_by_default(self):
        op = ServiceOperation(name="x")
        assert op.parameters == ()
        assert op.required_parameters == ()

    def test_a_path_parameter_requires_a_matching_placeholder(self):
        with pytest.raises(ValueError):
            ServiceOperation(
                name="x", path="/items",
                parameters=(ServiceParameter(name="widget_id", location="path"),),
            )

    def test_a_placeholder_requires_a_matching_declared_path_parameter(self):
        with pytest.raises(ValueError):
            ServiceOperation(name="x", path="/items/{widget_id}", parameters=())

    def test_an_arbitrarily_named_path_parameter_works_generically(self):
        """No special case for 'id' - any declared name that matches
        its own placeholder is accepted."""
        op = ServiceOperation(
            name="x", path="/warehouses/{shelf_locator}",
            parameters=(ServiceParameter(name="shelf_locator", location="path"),),
        )
        assert op.parameter("shelf_locator").location == "path"

    def test_duplicate_parameter_names_are_refused(self):
        with pytest.raises(ValueError):
            ServiceOperation(
                name="x",
                parameters=(ServiceParameter(name="a"), ServiceParameter(name="a")),
            )

    def test_required_parameters_property_excludes_optional_ones(self):
        op = ServiceOperation(
            name="x",
            parameters=(
                ServiceParameter(name="required_one", required=True),
                ServiceParameter(name="optional_one", required=False),
            ),
        )
        assert op.required_parameters == ("required_one",)

    def test_parameter_lookup_is_honest_absence_for_an_unknown_name(self):
        op = ServiceOperation(name="x")
        assert op.parameter("does_not_exist") is None
