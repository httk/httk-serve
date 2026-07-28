"""Compatibility shim for the generic OPTIMADE filter handlers.

The handler tables and generic handlers now live in
:mod:`httk.data.optimade_query`; this module re-exports their historical
public names for backwards compatibility. Import from
:mod:`httk.data.optimade_query` in new code.

Note that the handlers now raise
:class:`~httk.data.optimade_query.FilterTranslationError` (which carries a
neutral failure category instead of HTTP semantics);
:func:`~httk.optimade.backend.translation.translate_filter` wraps it into
:class:`~httk.optimade.model.errors.TranslatorError` with the appropriate
HTTP status. Also note that :func:`~httk.data.optimade_query.simple_property_handlers`
now takes a plain property-name -> ``fulltype`` mapping as its third argument
instead of an entry-info dictionary, and that ``'HAS'`` handlers changed shape:
they no longer take a trailing ``inv`` argument and no longer return a
``needs_post`` flag alongside the expression. A ``'HAS'`` handler is now called
as ``handler(property, ops, values, search_variable, has_type)`` and returns a
plain :class:`~httk.data.query.SearchExpression`; ``NOT`` is applied by the
caller as ``~``, and the backend decides for itself whether the resulting
expression also needs post-filter evaluation.
"""

from httk.data.optimade_query import (
    HandlerTable,
    constant_comparison_handler,
    constant_set_handler,
    constant_stringmatching_handler,
    false_handler,
    invert_op,
    known_unknown_handler,
    number_handler,
    set_handler,
    simple_property_handlers,
    string_handler,
    stringmatching_handler,
    timestamp_handler,
    true_handler,
    unknown_comparison_handler,
    unknown_has_handler,
    unknown_length_handler,
    unknown_stringmatching_handler,
    unknown_unknown_handler,
)

# Historical export order is a compatibility contract.
__all__ = [  # noqa: RUF022
    "HandlerTable",
    "invert_op",
    "true_handler",
    "false_handler",
    "unknown_unknown_handler",
    "known_unknown_handler",
    "unknown_comparison_handler",
    "unknown_stringmatching_handler",
    "unknown_has_handler",
    "unknown_length_handler",
    "string_handler",
    "stringmatching_handler",
    "constant_comparison_handler",
    "constant_stringmatching_handler",
    "number_handler",
    "timestamp_handler",
    "set_handler",
    "constant_set_handler",
    "simple_property_handlers",
]
