"""Translation of OPTIMADE filter syntax trees into backend search expressions.

The generic translation now lives in :mod:`httk.data.optimade_query`;
:func:`translate_filter` delegates to
:func:`~httk.data.optimade_query.translate_filter_ast` and wraps its neutral
:class:`~httk.data.optimade_query.FilterTranslationError` failure categories
into :class:`~httk.optimade.model.errors.TranslatorError` HTTP errors.
:func:`format_value` and :func:`translate_filter_node` are kept as
backwards-compatible wrappers over the upstream functions.
"""

from typing import Any, Mapping, Sequence

from httk.core import FilterAst
from httk.data.optimade_query import (
    FilterTranslationError,
    HandlerTable,
    RelatedPropertyResolver,
)
from httk.data.optimade_query import format_value as _format_value
from httk.data.optimade_query import (
    translate_filter_ast,
)
from httk.data.query import Searcher, SearchExpression, SearchVariable

from ..model.errors import translator_error_from
from .adapter import BackendAdapter, EntrySource


def format_value(fulltype: str, val: tuple[Any, ...], allow_null: bool = False) -> Any:
    """Backwards-compatible wrapper over :func:`httk.data.optimade_query.format_value`.

    Raises :class:`~httk.optimade.model.errors.TranslatorError` instead of the
    upstream neutral :class:`~httk.data.optimade_query.FilterTranslationError`.
    """
    try:
        return _format_value(fulltype, val, allow_null=allow_null)
    except FilterTranslationError as error:
        raise translator_error_from(error) from error


def _related_property_resolver(adapter: BackendAdapter) -> RelatedPropertyResolver:
    """Build the :data:`~httk.data.optimade_query.RelatedPropertyResolver` for an adapter.

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
    propagate as :class:`~httk.data.optimade_query.FilterTranslationError` and
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
            search_expr, needs_post = translate_filter_ast(
                sub_ast,
                search_variable,
                related_type,
                property_fulltypes,
                handlers,
                adapter.schema.recognized_prefixes,
                False,
                relationship_targets=(),
                related_property_resolver=None,
            )
            searcher.add(search_expr)
            if needs_post:
                searcher.add_all(search_expr)
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
                    searcher.add_sort(getattr(search_variable, source.sort_columns[name]), descending)
            if filter_ast is not None:
                try:
                    search_expr, needs_post = translate_filter_ast(
                        filter_ast,
                        search_variable,
                        entry,
                        property_fulltypes,
                        field_handlers,
                        adapter.schema.recognized_prefixes,
                        False,
                        relationship_targets=adapter.schema.all_entries,
                        related_property_resolver=resolver,
                    )
                except FilterTranslationError as error:
                    raise translator_error_from(error) from error
                searcher.add(search_expr)
                if needs_post:
                    searcher.add_all(search_expr)
            pairs.append((source, searcher))

    return pairs


def translate_filter_node(
    node: FilterAst,
    search_variable: SearchVariable,
    entry: str,
    entry_info: Mapping[str, Any],
    handlers: HandlerTable,
    recognized_prefixes: tuple[str, ...],
    inv_toggle: bool,
    recursion: int = 0,
    served_entries: tuple[str, ...] = (),
) -> tuple[SearchExpression, bool]:
    """Backwards-compatible wrapper over :func:`httk.data.optimade_query.translate_filter_ast`.

    Preserves the historical signature: ``entry_info`` maps property names to
    their property dictionaries (only their ``'fulltype'`` keys are read), and
    ``served_entries`` names the relationship targets. Raises
    :class:`~httk.optimade.model.errors.TranslatorError` instead of the
    upstream neutral :class:`~httk.data.optimade_query.FilterTranslationError`.

    Also preserves the historical no-resolver behavior: no
    related-property resolver is threaded through, so relationship-property
    filters other than ``<type>.id HAS ...`` raise the historical
    not-implemented (501) error. Use :func:`translate_filter` (which builds
    the resolver from its adapter) for full relationship-property filtering.
    """
    property_fulltypes = {name: prop.get('fulltype', 'unknown') for name, prop in entry_info.items()}
    try:
        return translate_filter_ast(
            node,
            search_variable,
            entry,
            property_fulltypes,
            handlers,
            recognized_prefixes,
            inv_toggle,
            recursion=recursion,
            relationship_targets=served_entries,
        )
    except FilterTranslationError as error:
        raise translator_error_from(error) from error
