"""Translation of OPTIMADE filter syntax trees into backend search expressions."""

from typing import Any, Callable, Sequence

from httk.data.query import Searcher, SearchExpression, SearchVariable

from ..filter.parser import FilterAst
from ..model.errors import TranslatorError
from .adapter import BackendAdapter, EntrySource
from .handlers import (
    HandlerTable,
)
from .handlers import invert_op as _invert_op
from .handlers import (
    unknown_comparison_handler,
    unknown_has_handler,
    unknown_length_handler,
    unknown_stringmatching_handler,
    unknown_unknown_handler,
)

constant_types = ['String', 'Number', 'Boolean']


def format_value(fulltype: str, val: tuple[Any, ...], allow_null: bool = False) -> Any:
    if fulltype.startswith('list of '):
        if not isinstance(val[0], tuple):
            raise TranslatorError(
                "Type mismatch in filter, query had single value when list of values was expected.",
                400,
                "Bad request",
            )
        inner_fulltype = fulltype[len('list of ') :]
        outvals = []
        for v in val:
            outvals += [format_value(inner_fulltype, v, allow_null=allow_null)]
        return outvals
    elif allow_null and val[0] == 'Null':
        return None
    elif fulltype == 'boolean':
        if val[0] in ['Boolean']:
            return val[1] == 'TRUE'
    elif fulltype == 'integer':
        if val[0] in ['Number']:
            return int(val[1])
    elif fulltype == 'float':
        if val[0] in ['Number']:
            return float(val[1])
    elif fulltype == 'string':
        if val[0] in ['String']:
            return val[1]
    elif fulltype == 'timestamp':
        if val[0] in ['String']:
            return val[1]
    elif fulltype == 'dict':
        raise TranslatorError("Filtering on dictionary properties not implemented.", 501, "Not implemented.")
    elif fulltype == 'unknown':
        return val[1]
    raise TranslatorError("Type mismatch in filter, expected:" + fulltype + ", query has:" + val[0], 400, "Bad request")


def translate_filter(
    filter_ast: FilterAst | None,
    entries: list[str],
    adapter: BackendAdapter,
    sort: Sequence[tuple[str, bool]] | None = None,
) -> list[tuple[EntrySource, Searcher]]:
    """Build one searcher per entry source, with the filter applied to each."""

    pairs: list[tuple[EntrySource, Searcher]] = []

    for entry in entries:
        field_handlers = adapter.field_handlers.get(entry, {})
        entry_info = adapter.schema.entry_info[entry]['properties']
        for source in adapter.sources.get(entry, ()):
            searcher = adapter.store.searcher()
            search_variable = searcher.variable(source.target)
            searcher.output(search_variable, entry)
            if sort is not None:
                for name, descending in sort:
                    searcher.add_sort(getattr(search_variable, source.sort_columns[name]), descending)
            if filter_ast is not None:
                search_expr, needs_post = translate_filter_node(
                    filter_ast,
                    search_variable,
                    entry,
                    entry_info,
                    field_handlers,
                    adapter.schema.recognized_prefixes,
                    False,
                    served_entries=adapter.schema.all_entries,
                )
                searcher.add(search_expr)
                if needs_post:
                    searcher.add_all(search_expr)
            pairs.append((source, searcher))

    return pairs


def translate_filter_node(
    node: FilterAst,
    search_variable: SearchVariable,
    entry: str,
    entry_info: dict[str, Any],
    handlers: HandlerTable,
    recognized_prefixes: tuple[str, ...],
    inv_toggle: bool,
    recursion: int = 0,
    served_entries: tuple[str, ...] = (),
) -> tuple[SearchExpression, bool]:

    search_expr: SearchExpression | None = None
    needs_post = False

    if node[0] in ['AND']:
        search_expr, needs_post = translate_filter_node(
            node[1],
            search_variable,
            entry,
            entry_info,
            handlers,
            recognized_prefixes,
            inv_toggle,
            recursion=recursion + 1,
            served_entries=served_entries,
        )
        rhs_search_expr, rhs_needs_post = translate_filter_node(
            node[2],
            search_variable,
            entry,
            entry_info,
            handlers,
            recognized_prefixes,
            inv_toggle,
            recursion=recursion + 1,
            served_entries=served_entries,
        )
        needs_post = needs_post or rhs_needs_post
        search_expr = search_expr & rhs_search_expr
    elif node[0] in ['OR']:
        search_expr, needs_post = translate_filter_node(
            node[1],
            search_variable,
            entry,
            entry_info,
            handlers,
            recognized_prefixes,
            inv_toggle,
            recursion=recursion + 1,
            served_entries=served_entries,
        )
        rhs_search_expr, rhs_needs_post = translate_filter_node(
            node[2],
            search_variable,
            entry,
            entry_info,
            handlers,
            recognized_prefixes,
            inv_toggle,
            recursion=recursion + 1,
            served_entries=served_entries,
        )
        needs_post = needs_post or rhs_needs_post
        search_expr = search_expr | rhs_search_expr
    elif node[0] in ['NOT']:
        search_expr, needs_post = translate_filter_node(
            node[1],
            search_variable,
            entry,
            entry_info,
            handlers,
            recognized_prefixes,
            not inv_toggle,
            recursion=recursion + 1,
            served_entries=served_entries,
        )
        search_expr = ~search_expr
    elif node[0] in ['HAS_ALL', 'HAS_ANY', 'HAS_ONLY']:
        ops = node[1]
        left = node[2]
        right = node[3]
        assert left[0] == 'Identifier'
        has_handler: Callable[..., Any] | None
        if len(left) > 2 and left[1] in served_entries:
            # Filtering on a relationship, e.g. `references.id HAS "ref-1"`.
            if left[2] == 'id':
                rel_key = left[1] + '.id'
                has_handler = handlers.get(rel_key, {}).get('HAS')
                if has_handler is None:
                    raise TranslatorError(
                        "Filtering on relationship " + rel_key + " not implemented.", 501, "Not implemented"
                    )
                values = format_value('list of string', right)
                if ops != tuple(['='] * len(values)):
                    raise TranslatorError(
                        "HAS queries with non-equal operators not implemented yet.", 501, "Not implemented"
                    )
                search_expr, needs_post = has_handler(rel_key, ops, values, search_variable, node[0], inv_toggle)
                assert search_expr is not None
                return search_expr, needs_post
            raise TranslatorError(
                "Filtering on relationship " + ".".join(left[1:]) + " not implemented.", 501, "Not implemented"
            )
        if left[1] not in entry_info:
            if left[1].startswith(recognized_prefixes):
                raise TranslatorError("Filter invokes unrecognized property name: " + left[1], 400, "Bad request")
            else:
                # TODO: this should warn
                has_handler = unknown_has_handler
                values = format_value('list of unknown', right)
        else:
            values = format_value(entry_info[left[1]].get('fulltype', 'unknown'), right)
            has_handler = handlers.get(left[1], {}).get('HAS')
            if has_handler is None:
                raise TranslatorError("Filtering on property " + left[1] + " not implemented.", 501, "Not implemented")
        if ops != tuple(['='] * len(values)):
            raise TranslatorError("HAS queries with non-equal operators not implemented yet.", 501, "Not implemented")
        search_expr, needs_post = has_handler(left[1], ops, values, search_variable, node[0], inv_toggle)
    elif node[0] in ['LENGTH']:
        left = node[1]
        op = node[2]
        right = node[3]
        assert left[0] == 'Identifier'
        if len(left) > 2 and left[1] in served_entries:
            raise TranslatorError(
                "Filtering on relationship " + ".".join(left[1:]) + " not implemented.", 501, "Not implemented"
            )
        if right[0] == 'Identifier':
            raise TranslatorError(
                "LENGTH comparisons with non-constant right hand side not implemented.", 501, "Not implemented"
            )
        if right[0] != 'Number':
            raise TranslatorError(
                "LENGTH comparison can only be done with Numbers. Unexpected right hand side type:" + right[0],
                501,
                "Not implemented",
            )
        length_handler: Callable[..., Any] | None
        if left[1] not in entry_info:
            if left[1].startswith(recognized_prefixes):
                raise TranslatorError("Filter invokes unrecognized property name: " + left[1], 400, "Bad request")
            else:
                # TODO: this should warn
                length_handler = unknown_length_handler
                value = format_value('unknown', right)
        else:
            length_handler = handlers.get(left[1], {}).get('length')
            if length_handler is None:
                raise TranslatorError("Filtering on property " + left[1] + " not implemented.", 501, "Not implemented")
            assert entry_info[left[1]].get('fulltype', '').startswith("list of ")
            value = format_value("integer", right)
        search_expr = length_handler(left[1], op, value, search_variable)
    elif node[0] in ['>', '>=', '<', '<=', '=', '!=']:
        op = node[0]
        left = node[1]
        right = node[2]
        if (left[0] == 'Boolean' or right[0] == 'Boolean') and op not in ('=', '!='):
            raise TranslatorError(
                "Boolean values only support the = and != comparison operators.", 501, "Not implemented"
            )
        if left[0] in constant_types and right[0] in constant_types:
            raise TranslatorError("Constant vs. Constant comparisons not implemented.", 501, "Not implemented")
        elif left[0] == 'Identifier' and right[0] == 'Identifier':
            raise TranslatorError("Identifier vs. Identifier comparisons not implemented.", 501, "Not implemented")
        else:
            if right[0] == 'Identifier' and left[0] in constant_types:
                left, right = right, left
                op = _invert_op[op]
            assert left[0] == 'Identifier'
            if len(left) > 2 and left[1] in served_entries:
                raise TranslatorError(
                    "Filtering on relationship " + ".".join(left[1:]) + " not implemented.", 501, "Not implemented"
                )
            comparison_handler: Callable[..., Any] | None
            if left[1] not in entry_info:
                if left[1].startswith(recognized_prefixes):
                    raise TranslatorError("Filter invokes unrecognized property name: " + left[1], 400, "Bad request")
                else:
                    # TODO: this should warn
                    comparison_handler = unknown_comparison_handler
                    value = format_value('unknown', right)
            else:
                comparison_handler = handlers.get(left[1], {}).get('comparison')
                if comparison_handler is None:
                    raise TranslatorError(
                        "Filtering on property " + left[1] + " not implemented.", 501, "Not implemented"
                    )
                value = format_value(entry_info[left[1]].get('fulltype', 'unknown'), right)
            search_expr = comparison_handler(left[1], op, value, search_variable)
    elif node[0] in ['ENDS', 'STARTS', 'CONTAINS']:
        left = node[1]
        right = node[2]
        assert left[0] == 'Identifier'
        if len(left) > 2 and left[1] in served_entries:
            raise TranslatorError(
                "Filtering on relationship " + ".".join(left[1:]) + " not implemented.", 501, "Not implemented"
            )
        if right[0] == 'Identifier':
            raise TranslatorError(
                "Identifier vs. Identifier string comparisons not implemented.", 501, "Not implemented"
            )
        stringmatching: Callable[..., Any] | None
        if left[1] not in entry_info:
            if left[1].startswith(recognized_prefixes):
                raise TranslatorError("Filter invokes unrecognized property name: " + left[1], 400, "Bad request")
            else:
                # TODO: this should warn
                stringmatching = unknown_stringmatching_handler
                value = format_value('unknown', right)
        else:
            stringmatching = handlers.get(left[1], {}).get('stringmatching')
            if stringmatching is None:
                raise TranslatorError("Filtering on property " + left[1] + " not implemented.", 501, "Not implemented")
            value = format_value(entry_info[left[1]].get('fulltype', 'unknown'), right)
        search_expr = stringmatching(left[1], value, node[0], search_variable)
    elif node[0] in ['IS_UNKNOWN', 'IS_KNOWN']:
        left = node[1]
        assert left[0] == 'Identifier'
        if len(left) > 2 and left[1] in served_entries:
            raise TranslatorError(
                "Filtering on relationship " + ".".join(left[1:]) + " not implemented.", 501, "Not implemented"
            )
        if left[1] not in entry_info:
            if left[1].startswith(recognized_prefixes):
                raise TranslatorError("Filter invokes unrecognized property name: " + left[1], 400, "Bad request")
            else:
                # TODO: this should warn
                unknown = unknown_unknown_handler
        else:
            unknown = handlers[left[1]]['unknown']
        search_expr = unknown(left[1], search_variable, node[0])
    else:
        raise TranslatorError("Unexpected translation error at: " + str(node[0]), 500, "Internal server error.")
    assert search_expr is not None
    return search_expr, needs_post
