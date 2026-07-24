"""Typed protocols for the store/searcher contract the OPTIMADE backend uses.

The store/searcher protocols are defined in :mod:`httk.data.query` (httk-data
is where httk's data stores live, and this backend programs against their
shared query contract); this module re-exports them for the backend's use,
together with the OPTIMADE-specific query-callable types from the result
model.
"""

from httk.data.query import (
    SearchColumn,
    Searcher,
    SearchExpression,
    SearchVariable,
    Store,
)

from ..model.results import QueryFunction, QueryResults

__all__ = [
    "SearchExpression",
    "SearchColumn",
    "SearchVariable",
    "Searcher",
    "Store",
    "QueryFunction",
    "QueryResults",
]
