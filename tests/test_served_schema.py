import pytest
from definition_fixtures import (
    calculations_definition,
    references_definition,
    structures_definition,
    widgets_definition,
)
from httk.core import EntryTypeDefinition, PropertyDefinition
from materials_fixtures import materials_schema

from httk.serve.optimade.engine.validate import validate_optimade_request
from httk.serve.optimade.model import RawRequest
from httk.serve.optimade.schema.served import build_served_schema


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


def test_response_should_not_is_excluded_from_defaults_but_explicitly_retrievable() -> None:
    hidden = PropertyDefinition.from_optimade(
        "hidden",
        {
            "$id": "https://example.test/hidden",
            "description": "Hidden by default",
            "x-optimade-type": "string",
            "type": ["string", "null"],
            "x-optimade-requirements": {"response-level": "should not"},
        },
    )
    must_not = PropertyDefinition.from_optimade(
        "must_not",
        {
            "$id": "https://example.test/must_not",
            "description": "Never returned by default",
            "x-optimade-type": "string",
            "type": ["string", "null"],
            "x-optimade-requirements": {"response-level": "must not"},
        },
    )
    definition = EntryTypeDefinition(
        "widgets",
        "A widgets entry.",
        {
            "id": PropertyDefinition.from_simple("id", description="The id", required_response=True),
            "type": PropertyDefinition.from_simple("type", description="The type", required_response=True),
            "hidden": hidden,
            "must_not": must_not,
        },
    )
    schema = build_served_schema(
        {"widgets": definition},
        {"widgets": ["id", "type", "hidden", "must_not"]},
        default_response_overrides={"widgets": ["hidden", "must_not"]},
    )
    assert schema.default_response_fields["widgets"] == ("id", "type")
    assert {"hidden", "must_not"} <= set(schema.properties_by_entry["widgets"])
    default = validate_optimade_request(RawRequest("http://localhost/", "/widgets"), "1.3.0", schema)
    explicit = validate_optimade_request(
        RawRequest("http://localhost/", "/widgets?response_fields=hidden,must_not"), "1.3.0", schema
    )
    assert "hidden" not in default.recognized_response_fields
    assert "hidden" in explicit.recognized_response_fields
    assert "must_not" not in default.recognized_response_fields
    assert "must_not" in explicit.recognized_response_fields
