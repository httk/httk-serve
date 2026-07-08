"""Query execution: runs translated searchers and adapts results for the endpoints."""

from typing import Any, Iterator, Sequence

from ..filter.parser import FilterAst
from ..model.errors import OptimadeError, TranslatorError
from ..model.results import ResultRow
from .adapter import BackendAdapter, EntrySource
from .protocols import Searcher
from .translation import translate_filter


class StoreResults:
    """Results of a query over one or more searchers.

    Implements the :class:`~httk.optimade.model.results.QueryResults`
    protocol. Iteration yields one
    :class:`~httk.optimade.model.results.ResultRow` per entry, whose values
    map response-field names to values extracted from the matched row objects.
    """

    def __init__(
        self,
        pairs: list[tuple[EntrySource, Searcher]],
        response_fields: list[str],
        unknown_response_fields: list[str],
        limit: int | None,
        offset: int,
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
        self.more_data_available = True

    def count(self) -> int:
        count = 0
        for _source, searcher in self.pairs:
            count += searcher.count() - searcher.offset
        return count

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
                    for prefix in self.recognized_prefixes:
                        if field.startswith(prefix):
                            field = field[len(prefix) :]
                            break
                    result[field] = getattr(row, field)
                else:
                    raise OptimadeError("Unexpected field requested:" + str(field), 500, "Internal server error")
        except StopIteration:
            self.more_data_available = False
            self.cur = None
            raise

        if self.limit is not None and self._count == self.limit:
            self.more_data_available = True
            self.cur = None
            raise StopIteration

        self._count += 1

        return ResultRow(values=result)


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

    pairs = translate_filter(filter_ast, entries, adapter, sort)

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
        pairs, response_fields, unknown_response_fields, response_limit, 0, adapter.schema.recognized_prefixes
    )
