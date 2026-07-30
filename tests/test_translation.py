import pytest
from fake_backend import FakeStore, FakeVariable
from materials_fixtures import materials_field_handlers, materials_schema

from httk.serve.optimade.backend import BackendAdapter, EntrySource, translate_filter
from httk.serve.optimade.filter import parse_optimade_filter
from httk.serve.optimade.model import TranslatorError


def make_adapter(store: FakeStore | None = None) -> BackendAdapter:
    return BackendAdapter(
        store=store if store is not None else FakeStore(),
        sources={
            "structures": (EntrySource(target="structure-table", fields={}),),
            "calculations": (
                EntrySource(target="aimd-table", fields={}),
                EntrySource(target="elastic-table", fields={}),
            ),
        },
        schema=materials_schema(),
        field_handlers=materials_field_handlers(),
    )


def translate_one(filter_string: str, entry: str = "structures"):
    adapter = make_adapter()
    pairs = translate_filter(parse_optimade_filter(filter_string), [entry], adapter)
    searchers = [searcher for _source, searcher in pairs]
    assert all(len(s.expressions) == 1 for s in searchers)
    return searchers


def test_no_filter_builds_searcher_per_source() -> None:
    adapter = make_adapter()
    pairs = translate_filter(None, ["calculations"], adapter)
    assert len(pairs) == 2
    assert pairs[0][0].target == "aimd-table"
    assert pairs[1][0].target == "elastic-table"
    for _source, searcher in pairs:
        assert searcher.expressions == []
        assert searcher.outputs[0][1] == "calculations"


def test_number_comparison() -> None:
    (searcher,) = translate_one("nelements=3")
    assert searcher.expressions[0].tree == ("eq", ("column", "number_of_elements"), 3)


def test_inverted_constant_first_comparison() -> None:
    (searcher,) = translate_one('3 < nelements')
    assert searcher.expressions[0].tree == ("gt", ("column", "number_of_elements"), 3)


def test_string_comparison_maps_to_formula() -> None:
    (searcher,) = translate_one('chemical_formula_descriptive = "GaTi"')
    assert searcher.expressions[0].tree == ("eq", ("column", "formula"), "GaTi")


def test_id_maps_to_dunder_id() -> None:
    (searcher,) = translate_one('id = "abc"')
    assert searcher.expressions[0].tree == ("eq", ("column", "__id"), "abc")


def test_has_all_becomes_conjunction_of_has_any() -> None:
    (searcher,) = translate_one('elements HAS ALL "Ga","Ti"')
    assert searcher.expressions[0].tree == (
        "AND",
        ("has_any", ("column", "formula_symbols"), ("Ga",)),
        ("has_any", ("column", "formula_symbols"), ("Ti",)),
    )


def test_has_any() -> None:
    (searcher,) = translate_one('elements HAS ANY "Ga","Ti"')
    assert searcher.expressions[0].tree == ("has_any", ("column", "formula_symbols"), ("Ga", "Ti"))


def test_has_only() -> None:
    (searcher,) = translate_one('elements HAS ONLY "Ga","Ti"')
    assert searcher.expressions[0].tree == ("has_only", ("column", "formula_symbols"), ("Ga", "Ti"))


def test_not_has_family_negates_the_plain_set_expressions() -> None:
    # There is no inverse set operation any more: NOT is `~` over exactly the
    # expression the un-negated filter produces, and the backend decides
    # whether that also needs post-filter evaluation.
    (searcher,) = translate_one('NOT elements HAS ALL "Ga"')
    assert searcher.expressions[0].tree == ("NOT", ("has_any", ("column", "formula_symbols"), ("Ga",)))
    (searcher,) = translate_one('NOT elements HAS ANY "Ga","Ti"')
    assert searcher.expressions[0].tree == (
        "NOT",
        ("has_any", ("column", "formula_symbols"), ("Ga", "Ti")),
    )
    (searcher,) = translate_one('NOT elements HAS ONLY "Ga","Ti"')
    assert searcher.expressions[0].tree == (
        "NOT",
        ("has_only", ("column", "formula_symbols"), ("Ga", "Ti")),
    )


def test_and_or_nesting() -> None:
    (searcher,) = translate_one('nelements=1 AND (nelements=2 OR nelements=3)')
    assert searcher.expressions[0].tree == (
        "AND",
        ("eq", ("column", "number_of_elements"), 1),
        (
            "OR",
            ("eq", ("column", "number_of_elements"), 2),
            ("eq", ("column", "number_of_elements"), 3),
        ),
    )


def test_stringmatching_contains_passes_the_literal_text() -> None:
    # The filter constant reaches the column verbatim: no pattern syntax crosses
    # the neutral protocol, so a LIKE metacharacter is matched literally.
    (searcher,) = translate_one('chemical_formula_descriptive CONTAINS "Ga_x"')
    assert searcher.expressions[0].tree == ("contains", ("column", "formula"), "Ga_x")


def test_stringmatching_starts_and_ends() -> None:
    (starts,) = translate_one('chemical_formula_descriptive STARTS WITH "Ga"')
    assert starts.expressions[0].tree == ("startswith", ("column", "formula"), "Ga")
    (ends,) = translate_one('chemical_formula_descriptive ENDS WITH "Ga"')
    assert ends.expressions[0].tree == ("endswith", ("column", "formula"), "Ga")


def test_stringmatching_percent_is_not_a_wildcard() -> None:
    (searcher,) = translate_one('chemical_formula_descriptive CONTAINS "50%"')
    assert searcher.expressions[0].tree == ("contains", ("column", "formula"), "50%")


def test_type_stringmatching_compares_the_property_value_left() -> None:
    # `type STARTS "struct"` asks whether "structures".startswith("struct"); the
    # operands used to be reversed. CONTAINS used to raise KeyError outright.
    for filter_string in ['type STARTS WITH "struct"', 'type ENDS WITH "ures"', 'type CONTAINS "struct"']:
        (searcher,) = translate_one(filter_string)
        assert searcher.expressions[0].tree == ("always_true",), filter_string
    for filter_string in ['type STARTS WITH "structuresX"', 'type CONTAINS "zzz"', 'type ENDS WITH "Xures"']:
        (searcher,) = translate_one(filter_string)
        assert searcher.expressions[0].tree == ("always_false",), filter_string


def test_type_comparison_compares_the_property_value_left() -> None:
    (searcher,) = translate_one('type = "structures"')
    assert searcher.expressions[0].tree == ("always_true",)
    (searcher,) = translate_one('type = "references"')
    assert searcher.expressions[0].tree == ("always_false",)


def test_length_maps_to_count_column() -> None:
    (searcher,) = translate_one('elements LENGTH 2')
    assert searcher.expressions[0].tree == ("eq", ("column", "number_of_elements"), 2)


def test_is_known_on_always_known_property_is_true() -> None:
    (searcher,) = translate_one('nelements IS KNOWN')
    assert searcher.expressions[0].tree == ("always_true",)


def test_is_unknown_on_always_known_property_is_false() -> None:
    (searcher,) = translate_one('nelements IS UNKNOWN')
    assert searcher.expressions[0].tree == ("always_false",)


def test_unknown_nonprefixed_property_matches_nothing() -> None:
    (searcher,) = translate_one('bananas = 3')
    assert searcher.expressions[0].tree == ("always_false",)


def test_unknown_prefixed_property_raises() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('_httk_bananas = 3')
    assert excinfo.value.response_code == 400


def test_has_with_operator_untranslatable() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('elements HAS < 3')
    assert excinfo.value.response_code == 500


def test_has_all_with_operator_not_implemented() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('elements HAS ALL > "Ga","Ti"')
    assert excinfo.value.response_code == 501


def test_identifier_vs_identifier_not_implemented() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('nelements = nsites')
    assert excinfo.value.response_code == 501


def test_type_mismatch_raises() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('nelements = "three"')
    assert excinfo.value.response_code == 400


def test_calculation_total_energy_comparison() -> None:
    searchers = translate_one('_httk_total_energy < -1.5', entry="calculations")
    assert len(searchers) == 2
    for searcher in searchers:
        assert searcher.expressions[0].tree == ("lt", ("column", "total_energy"), -1.5)


def test_boolean_comparison_on_unknown_property_matches_nothing() -> None:
    (searcher,) = translate_one('bananas = TRUE')
    assert searcher.expressions[0].tree == ("always_false",)


def test_boolean_with_ordering_operator_not_implemented() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('bananas > TRUE')
    assert excinfo.value.response_code == 501


def test_boolean_vs_nonboolean_property_type_mismatch() -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('nelements = TRUE')
    assert excinfo.value.response_code == 400


def test_nsites_comparison_not_implemented() -> None:
    # nsites has no backend column in the default schema, so the bogus
    # (number_of_elements) comparison handler was removed; comparison filters now
    # return an honest 501 instead of silently querying the wrong column.
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('nsites = 3')
    assert excinfo.value.response_code == 501


def test_list_typed_property_scalar_comparison_is_rejected() -> None:
    # species_at_sites / cartesian_site_positions are 'list of ...'. With their
    # bogus comparison handlers removed, a scalar comparison returns a clean 501.
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('species_at_sites = "Si"')
    assert excinfo.value.response_code == 501
    with pytest.raises(TranslatorError) as excinfo:
        translate_one('cartesian_site_positions = 0.5')
    assert excinfo.value.response_code == 501
    # The removed handlers were in any case unreachable: a scalar right-hand side
    # against a 'list of ...' property is rejected by format_value (400) first.
    from httk.serve.optimade.backend.translation import format_value

    with pytest.raises(TranslatorError) as excinfo:
        format_value('list of string', ('String', 'Si'))
    assert excinfo.value.response_code == 400


@pytest.mark.parametrize(
    "filter_string,response_code,response_msg",
    [
        # One filter per httk.data.optimade_query FilterTranslationError
        # category, locking the category -> HTTP status mapping:
        # unrecognized-property (a recognized-prefix property that does not exist)
        ('_httk_bananas = 3', 400, "Bad request"),
        # type-mismatch (string constant against an integer property)
        ('nelements = "three"', 400, "Bad request"),
        # not-implemented (identifier vs. identifier comparison)
        ('nelements = nsites', 501, "Not implemented"),
        # internal (bare HAS with a non-equal operator is not a translatable node)
        ('elements HAS < 3', 500, "Internal server error."),
    ],
)
def test_translation_error_categories_map_to_http_statuses(
    filter_string: str, response_code: int, response_msg: str
) -> None:
    with pytest.raises(TranslatorError) as excinfo:
        translate_one(filter_string)
    assert excinfo.value.response_code == response_code
    assert excinfo.value.response_msg == response_msg


def test_simple_property_handlers_timestamp_generates_comparison() -> None:
    from httk.serve.optimade.backend.handlers import simple_property_handlers

    property_fulltypes = {'last_modified': 'timestamp'}
    handlers = simple_property_handlers('files', {'last_modified': 'last_modified'}, property_fulltypes)
    table = handlers['last_modified']
    assert 'comparison' in table
    assert 'unknown' in table
    # Substring matching on timestamps is not meaningful.
    assert 'stringmatching' not in table
    sv = FakeVariable('files')
    expr = table['comparison']('last_modified', '>=', '2021-01-01T00:00:00Z', sv)
    assert expr.tree == ("ge", ("column", "last_modified"), "2021-01-01T00:00:00Z")
