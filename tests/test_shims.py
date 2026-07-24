"""The compatibility shims keep their historical public surface.

Phase 3 of the filter-language migration moved the parser to
``httk.core.optimade_filter`` and the generic handlers/translation to
``httk.data.optimade_query``; ``httk.optimade.filter`` and
``httk.optimade.backend.handlers`` remain as pure re-export shims.
"""

import httk.core.optimade_filter
import httk.data.optimade_query
import httk.optimade.backend.handlers
import httk.optimade.filter


def test_filter_shim_exports_historical_all() -> None:
    assert httk.optimade.filter.__all__ == [
        "ParserError",
        "ParserSyntaxError",
        "parse_optimade_filter",
        "parse_optimade_filter_raw",
    ]
    for name in httk.optimade.filter.__all__:
        assert getattr(httk.optimade.filter, name) is getattr(httk.core.optimade_filter, name)


def test_handlers_shim_exports_historical_all() -> None:
    assert httk.optimade.backend.handlers.__all__ == [
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
    for name in httk.optimade.backend.handlers.__all__:
        assert getattr(httk.optimade.backend.handlers, name) is getattr(httk.data.optimade_query, name)


def test_parse_optimade_filter_is_the_core_function() -> None:
    assert httk.optimade.filter.parse_optimade_filter is httk.core.optimade_filter.parse_optimade_filter
    assert httk.optimade.parse_optimade_filter is httk.core.optimade_filter.parse_optimade_filter
