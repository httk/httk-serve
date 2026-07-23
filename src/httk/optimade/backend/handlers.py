"""Field handlers translating OPTIMADE filter operations into search expressions.

The handler tables map OPTIMADE property names to callables that build
:class:`~httk.optimade.backend.protocols.SearchExpression` objects from a
:class:`~httk.optimade.backend.protocols.SearchVariable`. A backend supplies a
handler table per entry type on its
:class:`~httk.optimade.backend.adapter.BackendAdapter`;
:func:`simple_property_handlers` derives such a table generically from a column
map and the entry's property ``fulltype`` metadata (this is what
:func:`~httk.optimade.backend.providers.adapter_from_providers` uses).

Comparison dunders on columns are invoked via ``getattr(column, '__eq__')(value)``
since they cannot be typed as expression-returning.
"""

import operator
from typing import Any, Callable, Mapping

from ..model.errors import TranslatorError
from ..schema.entries import EntryInfo
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


def simple_property_handlers(
    entry_type: str, columns: Mapping[str, str], entry_info: EntryInfo
) -> dict[str, Mapping[str, Callable[..., Any]]]:
    """Build a filter handler table for an entry type from a column map.

    Always provides the standard ``id`` (matched against the ``__id`` column)
    and ``type`` (a constant equal to ``entry_type``) handlers. For every
    property named in ``columns`` (which maps OPTIMADE property names to backend
    column names), handlers are generated from the property's ``fulltype`` in
    ``entry_info``: string properties get comparison and stringmatching
    handlers; integer and float properties get a numeric comparison handler;
    ``list of ...`` properties get a HAS (set membership) handler. Every
    generated property also gets a ``known`` unknown handler.

    This builder is reused for entry types (references, files, trajectories)
    whose backend rows are plain column maps.
    """
    handlers: dict[str, Mapping[str, Callable[..., Any]]] = {
        'id': {
            'comparison': lambda entry, op, value, sv: string_handler('__id', op, value, sv),
            'unknown': known_unknown_handler,
            'stringmatching': lambda entry, value, smtype, sv: stringmatching_handler('__id', value, smtype, sv),
        },
        'type': {
            'comparison': lambda entry, op, value, sv: constant_comparison_handler(value, op, entry_type, sv),
            'unknown': known_unknown_handler,
            'stringmatching': lambda entry, value, smtype, sv: constant_stringmatching_handler(
                value, entry_type, smtype, sv
            ),
        },
    }
    properties = entry_info['properties']
    for name, column in columns.items():
        fulltype = properties.get(name, {}).get('fulltype', 'string')
        table: dict[str, Callable[..., Any]] = {'unknown': known_unknown_handler}
        if fulltype.startswith('list of '):
            table['HAS'] = lambda entry, ops, values, sv, has_type, inv, col=column: set_handler(
                col, ops, values, inv, has_type, sv
            )
        elif fulltype in ('integer', 'float'):
            table['comparison'] = lambda entry, op, value, sv, col=column: number_handler(col, op, value, sv)
        elif fulltype == 'timestamp':
            # Timestamps are RFC 3339 strings; lexicographic comparison is
            # correct for same-format UTC timestamps, so string_handler applies.
            # No stringmatching handler: substring matching on timestamps is not
            # meaningful.
            table['comparison'] = lambda entry, op, value, sv, col=column: string_handler(col, op, value, sv)
        else:
            table['comparison'] = lambda entry, op, value, sv, col=column: string_handler(col, op, value, sv)
            table['stringmatching'] = lambda entry, value, smtype, sv, col=column: stringmatching_handler(
                col, value, smtype, sv
            )
        handlers[name] = table
    return handlers
