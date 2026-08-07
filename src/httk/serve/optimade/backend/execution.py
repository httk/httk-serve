"""Query execution: runs translated searchers and adapts results for the endpoints."""

from collections.abc import Iterator, Sequence
from typing import Any

from httk.core.optimade import FilterAst
from httk.data.query import Searcher

from ..model.errors import TranslatorError
from ..model.results import ResultRow
from .adapter import BackendAdapter, EntrySource
from .translation import translate_filter


class StoreResults:
    """Results of a query over one or more searchers.

    Implements the :class:`~httk.serve.optimade.model.results.QueryResults`
    protocol. Iteration yields one
    :class:`~httk.serve.optimade.model.results.ResultRow` per entry, whose values
    map response-field names to values extracted from the matched row objects.

    :param pairs: Sources and already-configured searchers to iterate.
    :param response_fields: Recognized fields to extract.
    :param unknown_response_fields: Unknown fields to return as null.
    :param limit: Maximum number of results to yield.
    :param offset: Number of results to skip.
    :param total_count: Total matches before pagination.
    :param recognized_prefixes: Prefixes for dynamic row attributes.
    """

    def __init__(
        self,
        pairs: list[tuple[EntrySource, Searcher]],
        response_fields: list[str],
        unknown_response_fields: list[str],
        limit: int | None,
        offset: int,
        total_count: int,
        recognized_prefixes: tuple[str, ...],
    ) -> None:
        self.pairs = pairs
        self.recognized_prefixes = recognized_prefixes
        self.cur: Iterator[tuple[EntrySource, Any]] | None = (
            (source, item) for source, searcher in pairs for item in searcher
        )
        self.limit = limit
        self.response_fields = response_fields
        self.unknown_response_fields = unknown_response_fields
        self._count = 0
        self.offset = offset
        self._total_count = total_count
        self.more_data_available = True

    def count(self) -> int:
        """Return all current-filter matches, before pagination.

        The endpoint metadata needs the filtered total even after execution has
        applied page limits and offsets to its searchers. Retaining it here also
        keeps the value stable once this one-shot result iterator is consumed.
        """
        return self._total_count

    def __iter__(self) -> "StoreResults":
        return self

    def __next__(self) -> ResultRow:
        if self.cur is None:
            raise StopIteration
        try:
            while self.offset > 0:
                next(self.cur)
                self.offset -= 1
            source, item = next(self.cur)
            row = item[0][0]
            result: dict[str, Any] = {}
            for field in self.unknown_response_fields:
                result[field] = None
            for field in self.response_fields:
                if field in source.fields:
                    result[field] = source.fields[field](row)
                elif field.startswith(self.recognized_prefixes):
                    stripped = field
                    for prefix in self.recognized_prefixes:
                        if field.startswith(prefix):
                            stripped = field[len(prefix) :]
                            break
                    try:
                        result[stripped] = getattr(row, stripped)
                    except AttributeError:
                        # The row object has no such attribute; serve the
                        # requested field as null instead of failing the query.
                        result[field] = None
                else:
                    # A recognized (schema-advertised) property for which this
                    # source provides no extractor. Per the OPTIMADE spec, an
                    # OPTIONAL property with an unknown value that is explicitly
                    # requested via ``response_fields`` MUST be returned as
                    # ``null`` rather than causing an error.
                    result[field] = None
        except StopIteration:
            self.more_data_available = False
            self.cur = None
            raise

        if self.limit is not None and self._count == self.limit:
            self.more_data_available = True
            self.cur = None
            raise StopIteration

        self._count += 1

        relationships: dict[str, list[dict[str, Any]]] = {}
        if source.relationships is not None:
            relationships = source.relationships(row)

        property_metadata: dict[str, Any] = {}
        for prop in self.response_fields:
            extractor = source.property_metadata.get(prop)
            if extractor is not None:
                metadata = extractor(row)
                if metadata is not None:
                    property_metadata[prop] = metadata

        return ResultRow(values=result, relationships=relationships, property_metadata=property_metadata)


def execute_query(
    adapter: BackendAdapter,
    entries: list[str],
    response_fields: list[str],
    unknown_response_fields: list[str],
    response_limit: int | None,
    response_offset: int | None,
    filter_ast: FilterAst | None = None,
    *,
    sort: Sequence[tuple[str, bool]] | None = None,
    debug: bool = False,
) -> StoreResults:
    """Execute a translated query across the adapter's sources.

    :param adapter: Backend adapter providing sources and schema.
    :param entries: Entry endpoints to query.
    :param response_fields: Recognized fields to return.
    :param unknown_response_fields: Unknown fields to return as null.
    :param response_limit: Maximum number of returned rows.
    :param response_offset: Number of matching rows to skip.
    :param filter_ast: Parsed filter, when one was requested.
    :param sort: Fields and directions to sort by.
    :param debug: Enable backend diagnostics.
    :return: Lazy results for the requested page.
    :raises httk.serve.optimade.model.errors.TranslatorError: If sorting across multiple sources is requested.
    """

    pairs = translate_filter(filter_ast, entries, adapter, sort)
    total_count = sum(searcher.count() for _source, searcher in pairs)

    if sort and len(pairs) > 1:
        raise TranslatorError("Sorting across multiple data sources is not implemented.", 501, "Not implemented")

    if response_offset is not None and response_offset != 0:
        remaining_offset = response_offset
        for i, (_source, searcher) in enumerate(pairs):
            count = searcher.count()
            remaining_offset -= count
            if remaining_offset < 0:
                # In SQLite, having an OFFSET without a LIMIT results in a syntax
                # error. We must therefore set a dummy limit -1, which means no bound.
                searcher.set_limit(-1)
                searcher.add_offset(count + remaining_offset)
                pairs = pairs[i:]
                break
        else:
            # The offset is at or beyond the total number of results.
            # (httk v1 instead returned results from offset 0 here.)
            pairs = []

    if response_limit is not None and response_limit != 0:
        remaining_limit = response_limit
        for i, (_source, searcher) in enumerate(pairs):
            count = searcher.count() - searcher.offset
            remaining_limit -= count
            if remaining_limit < 0:
                # We need one more than asked for to know if there is more data.
                searcher.set_limit(count + remaining_limit + 1)
                pairs = pairs[: i + 1]
                break

    # Offset (and limit, but it doesn't matter) is already handled by the searcher.
    return StoreResults(
        pairs,
        response_fields,
        unknown_response_fields,
        response_limit,
        0,
        total_count,
        adapter.schema.recognized_prefixes,
    )
