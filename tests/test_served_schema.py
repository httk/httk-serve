import pytest
from definition_fixtures import (
    calculations_definition,
    references_definition,
    structures_definition,
    widgets_definition,
)
from materials_fixtures import materials_schema

from httk.optimade.schema.served import build_served_schema


def test_built_schema_property_definitions() -> None:
    schema = materials_schema()
    nelements = schema.property_definitions["structures"]["nelements"]
    assert nelements["x-optimade-type"] == "integer"
    assert nelements["x-optimade-implementation"]["sortable"] is False
    assert nelements["x-optimade-implementation"]["response-default"] is True


def test_built_schema_retains_only_exact_entry_definition_ids() -> None:
    schema = build_served_schema({"references": references_definition(), "calculations": calculations_definition()})
    assert schema.entry_definition_ids == {"references": references_definition().definition_id}


def test_build_served_schema_custom_entry_selection() -> None:
    schema = build_served_schema({"structures": structures_definition()}, {"structures": ["id", "type", "nelements"]})
    assert schema.all_entries == ("structures",)
    assert schema.valid_endpoints == ("info", "links", "structures", "info/structures", "")
    assert schema.properties_by_entry == {"structures": ("id", "type", "nelements")}
    assert schema.required_response_fields["structures"] == ("id", "type")
    assert schema.default_response_fields["structures"] == ("id", "type")
    # Definition properties not served must be recognized as valid-but-unknown:
    assert "elements" in schema.unknown_response_fields["structures"]
    assert "nelements" not in schema.unknown_response_fields["structures"]


def test_build_served_schema_sortable_and_default_response_overrides() -> None:
    schema = build_served_schema(
        {"structures": structures_definition()},
        {"structures": ["id", "type", "nelements", "elements"]},
        default_response_overrides={"structures": ["nelements"]},
        sortable={"structures": ["id", "nelements"]},
    )
    assert schema.sortable_response_fields["structures"] == ("id", "nelements")
    assert schema.entry_info["structures"]["properties"]["elements"]["sortable"] is False
    assert schema.default_response_fields["structures"] == ("id", "type", "nelements")
    definitions = schema.property_definitions["structures"]
    assert definitions["nelements"]["x-optimade-implementation"]["sortable"] is True
    assert definitions["nelements"]["x-optimade-implementation"]["response-default"] is True
    assert definitions["elements"]["x-optimade-implementation"]["sortable"] is False
    assert definitions["elements"]["x-optimade-implementation"]["response-default"] is False


def test_build_served_schema_custom_widgets_type() -> None:
    schema = build_served_schema(
        {"structures": structures_definition(), "widgets": widgets_definition()},
        {"structures": ["id", "type"], "widgets": ["id", "type", "cogwheels"]},
    )
    assert schema.all_entries == ("structures", "widgets")
    assert "widgets" in schema.valid_endpoints
    assert "info/widgets" in schema.valid_endpoints
    assert schema.properties_by_entry["widgets"] == ("id", "type", "cogwheels")
    # The widgets definition describes only the served properties:
    assert schema.unknown_response_fields["widgets"] == ()
    assert schema.property_definitions["widgets"]["cogwheels"]["x-optimade-type"] == "integer"
    assert schema.entry_info["widgets"]["properties"]["cogwheels"]["sortable"] is False


def test_build_served_schema_serves_all_when_served_omitted() -> None:
    schema = build_served_schema({"widgets": widgets_definition()})
    assert schema.properties_by_entry["widgets"] == ("id", "type", "cogwheels")


def test_build_served_schema_rejects_undescribed_served_property() -> None:
    with pytest.raises(ValueError) as excinfo:
        build_served_schema({"widgets": widgets_definition()}, {"widgets": ["id", "type", "bogus"]})
    assert "bogus" in str(excinfo.value)
