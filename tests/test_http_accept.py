"""Unit tests for the shared RFC 9110 ``Accept`` parsing and selection helpers."""

import pytest

from httk.serve.http import (
    MediaRange,
    best_quality_ignoring_parameterised,
    best_quality_matching_parameters,
    http_parameter_value,
    parse_accept,
    parse_media_type,
    split_http_list,
)


def test_split_http_list_splits_outside_quotes() -> None:
    assert split_http_list("a, b, c", ",") == ("a", " b", " c")


def test_split_http_list_keeps_delimiter_inside_quoted_string() -> None:
    # A comma inside a quoted string is not a list separator.
    assert split_http_list('a, "b,c", d', ",") == ("a", ' "b,c"', " d")
    # A semicolon inside a quoted parameter value is not a parameter separator.
    assert split_http_list('application/json; profile="p;q"', ";") == ("application/json", ' profile="p;q"')


def test_split_http_list_preserves_empty_items() -> None:
    assert split_http_list("a,,b", ",") == ("a", "", "b")
    assert split_http_list("a,", ",") == ("a", "")
    assert split_http_list("", ",") == ("",)


def test_split_http_list_rejects_broken_quoting() -> None:
    assert split_http_list('a, "unterminated', ",") is None
    assert split_http_list('"trailing escape\\', ",") is None


def test_http_parameter_value_tokens_and_quoted_strings() -> None:
    assert http_parameter_value("  utf-8  ") == "utf-8"
    assert http_parameter_value('"quoted value"') == "quoted value"
    assert http_parameter_value('"esc\\"aped"') == 'esc"aped'


def test_http_parameter_value_rejects_malformed() -> None:
    assert http_parameter_value("") is None
    assert http_parameter_value("   ") is None
    assert http_parameter_value('bad"token') is None
    assert http_parameter_value('"') is None
    assert http_parameter_value('"unterminated') is None
    assert http_parameter_value('"bad"quote"') is None
    assert http_parameter_value('"trailing\\') is None


def test_parse_media_type_bare_and_parameterised() -> None:
    assert parse_media_type("application/json") == ("application", "json", {})
    assert parse_media_type("Application/JSON; Charset=UTF-8") == ("application", "json", {"charset": "UTF-8"})
    assert parse_media_type("application/ld+json") == ("application", "ld+json", {})


def test_parse_media_type_rejects_non_json_and_malformed() -> None:
    for bad in (123, "text/plain", "application/xml", "notatype", 'application/"bad', "application/json; noequals"):
        with pytest.raises(ValueError):
            parse_media_type(bad)


def test_parse_media_type_rejects_duplicate_parameter_names() -> None:
    with pytest.raises(ValueError):
        parse_media_type("application/json; charset=utf-8; charset=ascii")


def test_parse_accept_returns_empty_tuple_on_broken_quoting() -> None:
    assert parse_accept('application/json, "unterminated') == ()


def test_parse_accept_parses_quality_and_parameters() -> None:
    (single,) = parse_accept("application/json; charset=utf-8; q=0.5")
    assert single == MediaRange("application", "json", (("charset", "utf-8"),), 0.5)


def test_parse_accept_default_quality_is_one() -> None:
    (single,) = parse_accept("application/json")
    assert single.quality == 1.0


def test_parse_accept_wildcards() -> None:
    assert parse_accept("*/*") == (MediaRange("*", "*", (), 1.0),)
    assert parse_accept("application/*") == (MediaRange("application", "*", (), 1.0),)


def test_parse_accept_rejects_invalid_wildcards_and_bad_quality() -> None:
    # A wildcard major with a concrete minor, and a partial wildcard minor, are dropped.
    assert parse_accept("*/json") == ()
    assert parse_accept("application/*json") == ()
    # A malformed q value drops the whole range.
    assert parse_accept("application/json; q=nope") == ()
    # A duplicate parameter name drops the whole range.
    assert parse_accept("application/json; a=1; a=2") == ()
    # A parameter without an equals sign drops the whole range.
    assert parse_accept("application/json; broken") == ()


def test_parse_accept_empty_items_are_dropped() -> None:
    # Empty list items fail media-type parsing and are silently dropped.
    assert parse_accept("application/json,,") == (MediaRange("application", "json", (), 1.0),)


_RANGES = parse_accept("application/json; charset=utf-8, application/*; q=0.3, */*; q=0.1")


def test_best_quality_matching_parameters_requires_present_and_equal_parameters() -> None:
    # The parameterised range matches only when the response carries the same parameter.
    assert best_quality_matching_parameters(_RANGES, "application", "json", {"charset": "utf-8"}) == 1.0
    # Against a response with no such parameter, the parameterised range is skipped and the
    # more specific surviving range (application/*, q=0.3) wins over */*.
    assert best_quality_matching_parameters(_RANGES, "application", "json", {}) == 0.3
    # A different response type only matches the wildcards.
    assert best_quality_matching_parameters(_RANGES, "text", "plain", {}) == 0.1


def test_best_quality_matching_parameters_returns_none_when_nothing_matches() -> None:
    ranges = parse_accept("text/plain")
    assert best_quality_matching_parameters(ranges, "application", "json", {}) is None


def test_best_quality_matching_parameters_specificity_prefers_more_parameters() -> None:
    # Two equally specific ranges; the one carrying a matching parameter wins the tiebreak.
    ranges = parse_accept("application/json; q=0.2, application/json; charset=utf-8; q=0.9")
    assert best_quality_matching_parameters(ranges, "application", "json", {"charset": "utf-8"}) == 0.9


def test_best_quality_ignoring_parameterised_discards_any_parameterised_range() -> None:
    # THE DIVERGENCE: the same parameterised range accepted above is discarded here, so the
    # surviving wildcard decides the outcome even when the response carries that parameter.
    assert best_quality_ignoring_parameterised(_RANGES, "application", "json") == 0.3
    # A response type reachable only through the parameterised range never matches.
    assert (
        best_quality_ignoring_parameterised(parse_accept("application/json; charset=utf-8"), "application", "json")
        is None
    )


def test_best_quality_ignoring_parameterised_returns_none_when_nothing_matches() -> None:
    assert best_quality_ignoring_parameterised(parse_accept("text/plain"), "application", "json") is None


def test_the_two_selectors_diverge_on_the_same_parameterised_range() -> None:
    # A single parameterised range against a matching response: kept by one selector, discarded by the other.
    ranges = parse_accept("application/json; charset=utf-8")
    assert best_quality_matching_parameters(ranges, "application", "json", {"charset": "utf-8"}) == 1.0
    assert best_quality_ignoring_parameterised(ranges, "application", "json") is None
