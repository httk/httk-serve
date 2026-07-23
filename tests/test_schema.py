from materials_fixtures import materials_schema

from httk.optimade.schema import entries


def test_entry_info_integrity() -> None:
    assert set(entries.entry_info.keys()) == {"structures", "calculations", "references", "files"}
    for entry_info in entries.entry_info.values():
        assert "descripion" not in entry_info
        assert isinstance(entry_info["description"], str)
        for prop in entry_info["properties"].values():
            assert isinstance(prop["description"], str)
            assert isinstance(prop["fulltype"], str)
            assert isinstance(prop["required_response"], bool)
            assert isinstance(prop["default_response"], bool)


def test_properties_by_entry_are_lists() -> None:
    for props in entries.properties_by_entry.values():
        assert isinstance(props, list)
        assert "id" in props
        assert "type" in props


def test_served_entry_info_is_independent_copy() -> None:
    schema = materials_schema()
    for entry in schema.all_entries:
        for name, prop in schema.entry_info[entry]["properties"].items():
            assert prop["sortable"] is False
            spec_prop = entries.entry_info[entry]["properties"][name]
            assert "sortable" not in spec_prop


def test_default_response_fields() -> None:
    schema = materials_schema()
    assert set(schema.default_response_fields.keys()) == {"structures", "calculations"}
    structures = schema.default_response_fields["structures"]
    for field in ("id", "type", "elements", "nelements", "lattice_vectors", "structure_features"):
        assert field in structures
    calculations = schema.default_response_fields["calculations"]
    assert "_httk_total_energy" in calculations
    assert "_httk_structure_id" in calculations


def test_required_response_fields() -> None:
    schema = materials_schema()
    for entry in schema.all_entries:
        assert schema.required_response_fields[entry] == ("id", "type")


def test_unknown_response_fields() -> None:
    schema = materials_schema()
    unknown_structures = schema.unknown_response_fields["structures"]
    for field in ("immutable_id", "last_modified", "elements_ratios", "chemical_formula_hill", "species", "assemblies"):
        assert field in unknown_structures
    for field in schema.properties_by_entry["structures"]:
        assert field not in unknown_structures


def test_valid_endpoints() -> None:
    schema = materials_schema()
    for endpoint in ("", "info", "links", "structures", "calculations", "info/structures", "info/calculations"):
        assert endpoint in schema.valid_endpoints


def test_optimade_v12_v13_properties_present() -> None:
    schema = materials_schema()
    properties = entries.entry_info["structures"]["properties"]
    for name in (
        "space_group_symmetry_operations_xyz",
        "space_group_symbol_hall",
        "space_group_symbol_hermann_mauguin",
        "space_group_symbol_hermann_mauguin_extended",
        "space_group_it_number",
        "fractional_site_positions",
        "site_coordinate_span",
        "site_coordinate_span_description",
        "optimization_type",
        "wyckoff_positions",
    ):
        assert name in properties
        # Not served by the materials fixture schema, so they must be recognized
        # as valid-but-unknown response fields:
        assert name in schema.unknown_response_fields["structures"]
