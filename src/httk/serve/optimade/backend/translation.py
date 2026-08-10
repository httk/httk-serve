"""Translation of OPTIMADE filter syntax trees into backend search expressions.

The generic translation is implemented in :mod:`httk.store.query.optimade_filters`;
:func:`translate_filter` delegates to
:func:`~httk.store.query.optimade_filters.translate_filter_ast` and wraps its neutral
:class:`~httk.store.FilterTranslationError` failure categories
into :class:`~httk.serve.optimade.model.errors.TranslatorError` HTTP errors.
:func:`format_value` and :func:`translate_filter_node` are thin
OPTIMADE-side wrappers over the upstream functions.
"""

from collections.abc import Mapping, Sequence
from typing import Any

from httk.core.optimade import FilterAst
from httk.store import FilterTranslationError
from httk.store.query import Searcher, SearchExpression, SearchVariable
from httk.store.query.optimade_filters import (
    HandlerTable,
    RelatedPropertyResolver,
    translate_filter_ast,
)
from httk.store.query.optimade_filters import format_value as _format_value

from ..model.errors import translator_error_from
from .adapter import BackendAdapter, EntrySource


def format_value(fulltype: str, val: tuple[Any, ...], allow_null: bool = False) -> Any:
    """Convert a filter value and translate neutral failures to HTTP errors.

    Delegates to :func:`httk.store.query.optimade_filters.format_value` and raises
    :class:`~httk.serve.optimade.model.errors.TranslatorError` for its neutral
    :class:`~httk.store.FilterTranslationError` failures.

    :param fulltype: Simplified OPTIMADE property type.
    :param val: Parsed filter values.
    :param allow_null: Allow a null value for the property.
    :return: Backend-ready filter value.
    :raises httk.serve.optimade.model.errors.TranslatorError: If the value cannot be translated.
    """
    try:
        return _format_value(fulltype, val, allow_null=allow_null)
    except FilterTranslationError as error:
        raise translator_error_from(error) from error


def _related_property_resolver(adapter: BackendAdapter) -> RelatedPropertyResolver:
    """Build the :data:`~httk.store.query.optimade_filters.RelatedPropertyResolver` for an adapter.

    The returned resolver serves the two-phase semi-join behind depth-1
    relationship-property filters (e.g. ``references.doi CONTAINS "10.1"``):
    called with ``(related_type, sub_ast)`` — the filter node with the
    ``<related_type>.`` prefix stripped — it runs the sub-filter over the
    related entry type's own sources, exactly as if it had been filtered
    directly on ``/<related_type>``: the same schema-derived property
    fulltypes, the same handler tables, one fresh searcher per source. The
    matching related-entry ids are collected through each source's ``'id'``
    field extractor, deduplicated preserving first-seen order across sources,
    and returned as a tuple.

    The sub-translation runs with empty ``relationship_targets`` and no nested
    resolver, enforcing depth-1 semantics (nested dotted paths were already
    rejected before the resolver is called). Sub-search translation errors
    propagate as :class:`~httk.store.FilterTranslationError` and
    receive the normal category-to-status wrapping in the caller.
    """

    def resolve(related_type: str, sub_ast: FilterAst) -> tuple[str, ...]:
        handlers = adapter.field_handlers.get(related_type, {})
        property_fulltypes = {
            name: prop.get('fulltype', 'unknown')
            for name, prop in adapter.schema.entry_info[related_type]['properties'].items()
        }
        matched: dict[str, None] = {}
        for source in adapter.sources.get(related_type, ()):
            searcher = adapter.store.searcher()
            search_variable = searcher.variable(source.target)
            searcher.output(search_variable, related_type)
            searcher.add(
                translate_filter_ast(
                    sub_ast,
                    search_variable,
                    property_fulltypes,
                    handlers,
                    adapter.schema.recognized_prefixes,
                    relationship_targets=(),
                    related_property_resolver=None,
                )
            )
            id_extractor = source.fields['id']
            for item in searcher:
                matched.setdefault(str(id_extractor(item[0][0])))
        return tuple(matched)

    return resolve


def translate_filter(
    filter_ast: FilterAst | None,
    entries: list[str],
    adapter: BackendAdapter,
    sort: Sequence[tuple[str, bool]] | None = None,
) -> list[tuple[EntrySource, Searcher]]:
    """Build one searcher per entry source, with the filter applied to each.

    Relationship-property filters (dotted identifiers over served entry types)
    are resolved through the adapter's related-property resolver (built by
    ``_related_property_resolver``), so filtering ``references.doi`` behaves
    exactly like filtering ``/references`` directly.

    :param filter_ast: Parsed filter, or ``None`` for an unfiltered query.
    :param entries: Entry endpoints to search.
    :param adapter: Backend adapter supplying sources and handlers.
    :param sort: Response fields and descending flags for sorting.
    :return: Source/searcher pairs with the filter and sort applied.
    :raises httk.serve.optimade.model.errors.TranslatorError: If the filter cannot be translated.
    """

    pairs: list[tuple[EntrySource, Searcher]] = []
    resolver = _related_property_resolver(adapter)

    for entry in entries:
        field_handlers = adapter.field_handlers.get(entry, {})
        property_fulltypes = {
            name: prop.get('fulltype', 'unknown')
            for name, prop in adapter.schema.entry_info[entry]['properties'].items()
        }
        for source in adapter.sources.get(entry, ()):
            searcher = adapter.store.searcher()
            search_variable = searcher.variable(source.target)
            searcher.output(search_variable, entry)
            if sort is not None:
                for name, descending in sort:
                    searcher.add_sort(getattr(search_variable, source.sort_keys[name]), descending)
            if filter_ast is not None:
                try:
                    search_expr = translate_filter_ast(
                        filter_ast,
                        search_variable,
                        property_fulltypes,
                        field_handlers,
                        adapter.schema.recognized_prefixes,
                        relationship_targets=adapter.schema.all_entries,
                        related_property_resolver=resolver,
                    )
                except FilterTranslationError as error:
                    raise translator_error_from(error) from error
                searcher.add(search_expr)
            pairs.append((source, searcher))

    return pairs


def translate_filter_node(
    node: FilterAst,
    search_variable: SearchVariable,
    entry: str,
    entry_info: Mapping[str, Any],
    handlers: HandlerTable,
    recognized_prefixes: tuple[str, ...],
    served_entries: tuple[str, ...] = (),
) -> SearchExpression:
    """Translate one filter node against an OPTIMADE *entry-info* property mapping.

    An OPTIMADE-side adaptation of
    :func:`~httk.store.query.optimade_filters.translate_filter_ast`: ``entry_info`` maps
    property names to their property dictionaries (only their ``'fulltype'``
    keys are read) rather than straight to fulltypes, ``served_entries`` names
    the relationship targets, and failures surface as
    :class:`~httk.serve.optimade.model.errors.TranslatorError` instead of the
    upstream neutral :class:`~httk.store.FilterTranslationError`.

    No related-property resolver is threaded through, so relationship-property
    filters other than ``<type>.id HAS ...`` raise a not-implemented (501)
    error. Use :func:`translate_filter` (which builds the resolver from its
    adapter) for full relationship-property filtering.

    :param node: Filter node to translate.
    :param search_variable: Backend variable used by the expression.
    :param entry: Entry endpoint being filtered.
    :param entry_info: Simplified property metadata for the entry.
    :param handlers: Property handlers used for translation.
    :param recognized_prefixes: Property-definition prefixes accepted by the filter.
    :param served_entries: Entry types available as relationship targets.
    :return: Backend search expression.
    :raises httk.serve.optimade.model.errors.TranslatorError: If the filter cannot be translated.
    """
    property_fulltypes = {name: prop.get('fulltype', 'unknown') for name, prop in entry_info.items()}
    try:
        return translate_filter_ast(
            node,
            search_variable,
            property_fulltypes,
            handlers,
            recognized_prefixes,
            relationship_targets=served_entries,
        )
    except FilterTranslationError as error:
        raise translator_error_from(error) from error
