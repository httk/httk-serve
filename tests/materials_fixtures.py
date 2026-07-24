"""Self-contained materials (structures/calculations) schema and filter handlers.

httk-optimade is a generic implementation of the OPTIMADE protocol: entry
schemas and their filter handlers are supplied by the deployment (in practice by
an :class:`httk.core.EntryProvider` such as httk-atomistic's structure
provider), not built into the package. Several tests still want a realistic
materials schema to exercise the engine end to end, so the ``structures`` and
``calculations`` tables that httk-optimade used to ship as built-in defaults
live here, as a test fixture. Backend column names (``formula_symbols``,
``number_of_elements``, ...) mirror the httk database schema these tests target.
"""

from typing import Any

from definition_fixtures import served_schema

from httk.optimade.backend.handlers import (
    HandlerTable,
    constant_comparison_handler,
    constant_set_handler,
    constant_stringmatching_handler,
    false_handler,
    known_unknown_handler,
    number_handler,
    set_handler,
    string_handler,
    stringmatching_handler,
    true_handler,
)
from httk.optimade.backend.protocols import SearchExpression, SearchVariable
from httk.optimade.schema.served import ServedSchema

STRUCTURE_PROPERTIES = [
    'id',
    'type',
    'elements',
    'nelements',
    'chemical_formula_descriptive',
    'dimension_types',
    'nperiodic_dimensions',
    'lattice_vectors',
    'structure_features',
    'nsites',
    'species_at_sites',
    'cartesian_site_positions',
    'chemical_formula_anonymous',
    'chemical_formula_reduced',
]

CALCULATION_PROPERTIES = [
    'id',
    'type',
    '_httk_total_energy',
    '_httk_structure_id',
]

DEFAULT_RESPONSE_OVERRIDES = {
    'structures': [
        'structure_features',
        'lattice_vectors',
        'elements',
        'nelements',
        'chemical_formula_descriptive',
        'dimension_types',
        'nperiodic_dimensions',
        'nsites',
        'species_at_sites',
        'cartesian_site_positions',
        'chemical_formula_anonymous',
        'chemical_formula_reduced',
    ],
    'calculations': [
        '_httk_total_energy',
        '_httk_structure_id',
    ],
}


def materials_schema() -> ServedSchema:
    """A schema serving ``structures`` and ``calculations`` (the former default)."""
    return served_schema(
        {'structures': STRUCTURE_PROPERTIES, 'calculations': CALCULATION_PROPERTIES},
        default_response_overrides=DEFAULT_RESPONSE_OVERRIDES,
    )


def _structure_features_set_handler(
    values: Any, ops: Any, has_type: str, search_variable: SearchVariable
) -> SearchExpression:
    # These fixtures carry no structure features, so all set comparisons are False.
    return false_handler(search_variable)


def _structure_features_length_handler(op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    if value == 0:
        return true_handler(search_variable)
    return false_handler(search_variable)


def materials_field_handlers() -> dict[str, HandlerTable]:
    """The filter handler tables for the ``structures``/``calculations`` schema."""
    return {
        'structures': {
            'id': {
                'comparison': lambda entry, op, value, sv: string_handler('__id', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('__id', value, smtype, sv),
            },
            'type': {
                # The property's own value is the LEFT operand (the
                # constant_set_handler convention), so `type STARTS "struct"`
                # asks whether "structures".startswith("struct").
                'comparison': lambda entry, op, value, sv: constant_comparison_handler('structures', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: constant_stringmatching_handler(
                    'structures', value, smtype, sv
                ),
            },
            'elements': {
                'HAS': lambda entry, ops, values, sv, has_type: set_handler(
                    'formula_symbols', ops, values, has_type, sv
                ),
                'length': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'nelements': {
                'comparison': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'nperiodic_dimensions': {
                'comparison': lambda entry, op, value, sv: constant_comparison_handler(3, op, value, sv),
                'unknown': known_unknown_handler,
            },
            'dimension_types': {
                'HAS': lambda entry, ops, values, sv, has_type: constant_set_handler(
                    [1, 1, 1], ops, values, has_type, sv
                ),
                'length': lambda entry, op, value, sv: constant_comparison_handler(3, op, value, sv),
                'unknown': known_unknown_handler,
            },
            'chemical_formula_descriptive': {
                'comparison': lambda entry, op, value, sv: string_handler('formula', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('formula', value, smtype, sv),
            },
            'structure_features': {
                'HAS': lambda entry, ops, values, sv, has_type: _structure_features_set_handler(
                    values, ops, has_type, sv
                ),
                'length': lambda entry, op, value, sv: _structure_features_length_handler(op, value, sv),
                'unknown': known_unknown_handler,
            },
            'nsites': {
                'unknown': known_unknown_handler,
            },
            'species_at_sites': {
                'unknown': known_unknown_handler,
            },
            'cartesian_site_positions': {
                'unknown': known_unknown_handler,
            },
            'chemical_formula_anonymous': {
                'comparison': lambda entry, op, value, sv: string_handler('anonymous_formula', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler(
                    'anonymous_formula', value, smtype, sv
                ),
            },
            'chemical_formula_reduced': {
                'comparison': lambda entry, op, value, sv: string_handler('formula', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('formula', value, smtype, sv),
            },
        },
        'calculations': {
            'id': {
                'comparison': lambda entry, op, value, sv: string_handler('__id', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('__id', value, smtype, sv),
            },
            'type': {
                'comparison': lambda entry, op, value, sv: constant_comparison_handler('calculations', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: constant_stringmatching_handler(
                    'calculations', value, smtype, sv
                ),
            },
            '_httk_total_energy': {
                'comparison': lambda entry, op, value, sv: number_handler('total_energy', op, value, sv),
                'unknown': known_unknown_handler,
            },
            '_httk_structure_id': {
                'comparison': lambda entry, op, value, sv: string_handler('structure', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler(
                    'structure', value, smtype, sv
                ),
            },
        },
    }
