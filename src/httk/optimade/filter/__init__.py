"""Compatibility shim for the OPTIMADE filter-language parser.

The OPTIMADE filter language now lives in :mod:`httk.core.optimade_filter`
(beside the vendored OPTIMADE property definitions in
``httk.core.optimade_defs``); this package re-exports its historical public
names for backwards compatibility. Import from :mod:`httk.core` (or
:mod:`httk.core.optimade_filter`) in new code.
"""

from httk.core.optimade_filter import (
    ParserError,
    ParserSyntaxError,
    parse_optimade_filter,
    parse_optimade_filter_raw,
)

__all__ = [
    "ParserError",
    "ParserSyntaxError",
    "parse_optimade_filter",
    "parse_optimade_filter_raw",
]
