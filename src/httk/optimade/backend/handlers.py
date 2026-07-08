"""Field handlers translating OPTIMADE filter operations into search expressions.

The handler tables map OPTIMADE property names to callables that build
:class:`~httk.optimade.backend.protocols.SearchExpression` objects from a
:class:`~httk.optimade.backend.protocols.SearchVariable`. The default tables
returned by :func:`default_field_handlers` encode the column names of the
httk database schema (e.g. ``formula_symbols``, ``number_of_elements``); a
backend with a different schema can supply its own tables on its
:class:`~httk.optimade.backend.adapter.BackendAdapter`.

Comparison dunders on columns are invoked via ``getattr(column, '__eq__')(value)``
since they cannot be typed as expression-returning.
"""

import operator
from typing import Any, Callable, Mapping

from ..model.errors import TranslatorError
from .protocols import SearchExpression, SearchVariable

HandlerTable = Mapping[str, Mapping[str, Callable[..., Any]]]

invert_op = {'!=': '!=', '>': '<', '<': '>', '=': '=', '<=': '>=', '>=': '<='}
_python_opmap = {
    '!=': '__ne__',
    '>': '__gt__',
    '<': '__lt__',
    '=': '__eq__',
    '<=': '__le__',
    '>=': '__ge__',
    'STARTS': 'startswith',
    'ENDS': 'endswith',
}


def true_handler(search_variable: SearchVariable) -> SearchExpression:
    return getattr(getattr(search_variable, 'hexhash'), '__eq__')(getattr(search_variable, 'hexhash'))


def false_handler(search_variable: SearchVariable) -> SearchExpression:
    return getattr(getattr(search_variable, 'hexhash'), '__ne__')(getattr(search_variable, 'hexhash'))


def unknown_unknown_handler(entry: str, search_variable: SearchVariable, unknown_type: str) -> SearchExpression:
    if unknown_type == 'IS_UNKNOWN':
        return true_handler(search_variable)
    elif unknown_type == 'IS_KNOWN':
        return false_handler(search_variable)
    raise TranslatorError("Unexpected unknown operator type", 500, "Internal server error.")


def known_unknown_handler(entry: str, search_variable: SearchVariable, unknown_type: str) -> SearchExpression:
    if unknown_type == 'IS_UNKNOWN':
        return false_handler(search_variable)
    elif unknown_type == 'IS_KNOWN':
        return true_handler(search_variable)
    raise TranslatorError("Unexpected unknown operator type", 500, "Internal server error.")


def unknown_comparison_handler(entry: str, ops: Any, values: Any, search_variable: SearchVariable) -> SearchExpression:
    return false_handler(search_variable)


def unknown_stringmatching_handler(
    entry: str, values: Any, stringmatching_type: str, search_variable: SearchVariable
) -> SearchExpression:
    return false_handler(search_variable)


def unknown_has_handler(
    entry: str, op: Any, value: Any, search_variable: SearchVariable, has_type: str, inv_toggle: bool
) -> tuple[SearchExpression, bool]:
    return false_handler(search_variable), False


def unknown_length_handler(entry: str, op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    return false_handler(search_variable)


def string_handler(entry: str, op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    httk_op = _python_opmap[op]
    return getattr(getattr(search_variable, entry), httk_op)(value)


def stringmatching_handler(
    entry: str, value: str, stringmatching_type: str, search_variable: SearchVariable
) -> SearchExpression:
    escaped_value = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    if stringmatching_type == 'ENDS':
        return getattr(getattr(search_variable, entry), 'like')('%' + escaped_value)
    elif stringmatching_type == 'STARTS':
        return getattr(getattr(search_variable, entry), 'like')(escaped_value + '%')
    elif stringmatching_type == 'CONTAINS':
        return getattr(getattr(search_variable, entry), 'like')('%' + escaped_value + '%')
    else:
        raise TranslatorError("Unexpected stringmatching operator type", 500, "Internal server error.")


def constant_comparison_handler(val1: Any, op: str, val2: Any, search_variable: SearchVariable) -> SearchExpression:
    if getattr(operator, _python_opmap[op])(val1, val2):
        return true_handler(search_variable)
    else:
        return false_handler(search_variable)


def constant_stringmatching_handler(
    val1: Any, val2: Any, stringmatching_type: str, search_variable: SearchVariable
) -> SearchExpression:
    if getattr(val1, _python_opmap[stringmatching_type])(val2):
        return true_handler(search_variable)
    else:
        return false_handler(search_variable)


def number_handler(entry: str, op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    httk_op = _python_opmap[op]
    return getattr(getattr(search_variable, entry), httk_op)(value)


def timestamp_handler(entry: str, op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    raise TranslatorError("Timestamp comparison not yet implemented.", 501, "Not implemented.")


def set_handler(
    entry: str, ops: Any, values: Any, inv: bool, has_type: str, search_variable: SearchVariable
) -> tuple[SearchExpression, bool]:
    if has_type == 'HAS_ALL':
        if not inv:
            search = getattr(getattr(search_variable, entry), 'has_any')(values[0])
            for value in values[1:]:
                search = search & (getattr(getattr(search_variable, entry), 'has_any')(value))
            return search, False
        else:
            search = getattr(getattr(search_variable, entry), 'has_inv_any')(values[0])
            for value in values[1:]:
                search = search & (getattr(getattr(search_variable, entry), 'has_inv_any')(value))
            return search, True
    elif has_type == 'HAS_ANY':
        if not inv:
            return getattr(getattr(search_variable, entry), 'has_any')(*values), False
        else:
            return getattr(getattr(search_variable, entry), 'has_inv_any')(*values), True
    elif has_type == 'HAS_ONLY':
        if not inv:
            return getattr(getattr(search_variable, entry), 'has_only')(*values), True
        else:
            return getattr(getattr(search_variable, entry), 'has_inv_only')(*values), True
    raise TranslatorError("Unexpected set operator type: " + str(has_type), 500, "Internal server error.")


def constant_set_handler(
    val1: Any, ops: Any, val2: Any, has_type: str, inv: bool, search_variable: SearchVariable
) -> tuple[SearchExpression, bool]:
    if has_type == 'HAS_ALL':
        if set(val2) <= set(val1):
            return true_handler(search_variable), False
        else:
            return false_handler(search_variable), False
    elif has_type == 'HAS_ANY':
        if set(val2).isdisjoint(val1):
            return false_handler(search_variable), False
        else:
            return true_handler(search_variable), False
    elif has_type == 'HAS_ONLY':
        if set(val1) <= set(val2):
            return true_handler(search_variable), False
        else:
            return false_handler(search_variable), False
    raise TranslatorError("Unexpected set operator type: " + str(has_type), 500, "Internal server error.")


def structure_features_set_handler(
    values: Any, ops: Any, inv: bool, has_type: str, search_variable: SearchVariable
) -> tuple[SearchExpression, bool]:
    # Any HAS ANY, HAS ALL, HAS ONLY operation will check for presence of an identifier in
    # structure_features. For now we don't support any structure features, hence, all such
    # comparisons return False.
    return false_handler(search_variable), False


def structure_features_length_handler(op: str, value: Any, search_variable: SearchVariable) -> SearchExpression:
    # structure_features is assumed to always be empty
    if value == 0:
        return true_handler(search_variable)
    else:
        return false_handler(search_variable)


def default_field_handlers() -> dict[str, HandlerTable]:
    """The handler tables for the httk database schema."""
    return {
        'structures': {
            'id': {
                'comparison': lambda entry, op, value, sv: string_handler('__id', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('__id', value, smtype, sv),
            },
            'type': {
                'comparison': lambda entry, op, value, sv: constant_comparison_handler(value, op, 'structures', sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: constant_stringmatching_handler(
                    value, 'structures', smtype, sv
                ),
            },
            'elements': {
                'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler(
                    'formula_symbols', ops, values, inv, has_type, sv
                ),
                'length': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'nelements': {
                'comparison': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'nperiodic_dimensions': {
                'comparison': lambda entry, op, value, sv: constant_comparison_handler(value, op, 3, sv),
                'unknown': known_unknown_handler,
            },
            'dimension_types': {
                'HAS': lambda entry, ops, values, sv, has_type, inv: constant_set_handler(
                    values, ops, [1, 1, 1], has_type, inv, sv
                ),
                'length': lambda entry, op, value, sv: constant_comparison_handler(value, op, 3, sv),
                'unknown': known_unknown_handler,
            },
            'chemical_formula_descriptive': {
                'comparison': lambda entry, op, value, sv: string_handler('formula', op, value, sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('formula', value, smtype, sv),
            },
            'structure_features': {
                'HAS': lambda entry, ops, values, sv, has_type, inv: structure_features_set_handler(
                    values, ops, inv, has_type, sv
                ),
                'length': lambda entry, op, value, sv: structure_features_length_handler(op, value, sv),
                'unknown': known_unknown_handler,
            },
            # TODO: The 'comparison' handlers for nsites, species_at_sites, and
            # cartesian_site_positions do not match the property; they carry over
            # unchanged from httk v1.
            'nsites': {
                'comparison': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'species_at_sites': {
                'comparison': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
                'unknown': known_unknown_handler,
            },
            'cartesian_site_positions': {
                'comparison': lambda entry, op, value, sv: number_handler('number_of_elements', op, value, sv),
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
                'comparison': lambda entry, op, value, sv: constant_comparison_handler(value, op, 'calculations', sv),
                'unknown': known_unknown_handler,
                'stringmatching': lambda entry, value, smtype, sv: constant_stringmatching_handler(
                    value, 'calculations', smtype, sv
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


def default_structure_fields() -> dict[str, Callable[[Any], Any]]:
    """Row extractors for httk Structure objects (the future httk-db wiring)."""
    return {
        'type': lambda x: "structures",
        'id': lambda x: x.db.sid,
        'structure_features': lambda x: [],
        'lattice_vectors': lambda x: x.uc_basis.to_floats(),
        'elements': lambda x: sorted(set(x.formula_symbols)),
        'nelements': lambda x: x.number_of_elements,
        'chemical_formula_descriptive': lambda x: x.formula,
        'dimension_types': lambda x: [1, 1, 1],
        'nperiodic_dimensions': lambda x: 3,
        'nsites': lambda x: len(x.uc.uc_cartesian_coords),
        'species_at_sites': lambda x: [
            item
            for sublist in [[a.symbols[0]] * count for a, count in zip(x.assignments, x.uc_counts)]
            for item in sublist
        ],
        'cartesian_site_positions': lambda x: x.uc.uc_cartesian_coords.to_floats(),
        'chemical_formula_reduced': lambda x: x.formula,
        'chemical_formula_anonymous': lambda x: x.anonymous_formula,
    }


def default_calculation_fields() -> dict[str, Callable[[Any], Any]]:
    """Row extractors for httk calculation Result objects (the future httk-db wiring)."""
    return {
        'type': lambda x: "calculations",
        'id': lambda x: x.db.sid,
        '_httk_total_energy': lambda x: x.total_energy,
        '_httk_structure_id': lambda x: x.structure.db.sid,
    }
