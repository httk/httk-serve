from materials_fixtures import materials_schema

from httk.optimade.schema.entries import EntryInfo
from httk.optimade.schema.served import build_served_schema


def test_built_schema_property_definitions() -> None:
    schema = materials_schema()
    nelements = schema.property_definitions["structures"]["nelements"]
    assert nelements["x-optimade-type"] == "integer"
    assert nelements["x-optimade-implementation"]["sortable"] is False
    assert nelements["x-optimade-implementation"]["response-default"] is True


def test_build_served_schema_custom_entry_selection() -> None:
    schema = build_served_schema({"structures": ["id", "type", "nelements"]})
    assert schema.all_entries == ("structures",)
    assert schema.valid_endpoints == ("info", "links", "structures", "info/structures", "")
    assert schema.properties_by_entry == {"structures": ("id", "type", "nelements")}
    assert schema.required_response_fields["structures"] == ("id", "type")
    assert schema.default_response_fields["structures"] == ("id", "type")
    # Spec properties not served must be recognized as valid-but-unknown:
    assert "elements" in schema.unknown_response_fields["structures"]
    assert "nelements" not in schema.unknown_response_fields["structures"]


def test_build_served_schema_sortable_and_default_response_overrides() -> None:
    schema = build_served_schema(
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


def test_build_served_schema_extra_entry_info() -> None:
    widgets_info: EntryInfo = {
        "description": "A widgets entry.",
        "properties": {
            "id": {
                "description": "The ID for the widgets entry.",
                "type": "string",
                "fulltype": "string",
                "required_support": True,
                "should_support": True,
                "required_query": True,
                "required_response": True,
                "default_response": True,
            },
            "type": {
                "description": "The name of the type of this entry, always 'widgets'",
                "type": "string",
                "fulltype": "string",
                "required_support": True,
                "should_support": True,
                "required_query": True,
                "required_response": True,
                "default_response": True,
            },
            "cogwheels": {
                "description": "The number of cogwheels in the widget.",
                "type": "integer",
                "fulltype": "integer",
                "required_support": False,
                "should_support": True,
                "required_query": False,
                "required_response": False,
                "default_response": False,
            },
        },
    }
    schema = build_served_schema(
        {"structures": ["id", "type"], "widgets": ["id", "type", "cogwheels"]},
        extra_entry_info={"widgets": widgets_info},
    )
    assert schema.all_entries == ("structures", "widgets")
    assert "widgets" in schema.valid_endpoints
    assert "info/widgets" in schema.valid_endpoints
    assert schema.properties_by_entry["widgets"] == ("id", "type", "cogwheels")
    # No spec data for widgets, so nothing is valid-but-unknown:
    assert schema.unknown_response_fields["widgets"] == ()
    assert schema.property_definitions["widgets"]["cogwheels"]["x-optimade-type"] == "integer"
    # The served entry info is an independent copy of the supplied data:
    assert schema.entry_info["widgets"]["properties"]["cogwheels"]["sortable"] is False
    assert "sortable" not in widgets_info["properties"]["cogwheels"]
