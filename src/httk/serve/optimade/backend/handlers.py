"""Re-exports of the generic OPTIMADE filter handlers.

The handler tables and generic handlers are implemented in
:mod:`httk.store.query.optimade_filters` and exposed here under the
``httk.serve.optimade.backend.handlers`` namespace.

The handlers raise
:class:`~httk.store.FilterTranslationError` (which carries a
neutral failure category instead of HTTP semantics);
:func:`~httk.serve.optimade.backend.translation.translate_filter` wraps it into
:class:`~httk.serve.optimade.model.errors.TranslatorError` with the appropriate
HTTP status. :func:`~httk.store.query.optimade_filters.simple_property_handlers` takes a
plain property-name -> ``fulltype`` mapping as its third argument. A ``'HAS'``
handler is called as
``handler(property, ops, values, search_variable, has_type)`` and returns a
plain :class:`~httk.store.query.SearchExpression`; the caller applies ``NOT`` as
``~``, and the backend determines whether the resulting expression also needs
post-filter evaluation.
"""

from httk.store.query.optimade_filters import (
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
