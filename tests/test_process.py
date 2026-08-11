from collections.abc import Iterator
from typing import Any

import pytest
from materials_fixtures import materials_schema

from httk.serve.optimade.engine import process, process_init
from httk.serve.optimade.model import OptimadeConfig, OptimadeError, RawRequest, ResultRow


class StubResults:
    def __init__(self, rows: list[dict[str, Any]], more_data_available: bool = False) -> None:
        self.rows = rows
        self.more_data_available = more_data_available

    def count(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(ResultRow(values=row) for row in self.rows)


class StubQueryFunction:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.rows = rows if rows is not None else []
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        entries: list[str],
        response_fields: list[str],
        unknown_response_fields: list[str],
        page_limit: int,
        page_offset: int,
        filter_ast: Any = None,
        *,
        as_of: int | None = None,
        sort: Any = None,
        debug: bool = False,
    ) -> StubResults:
        self.calls.append(
            {
                "entries": entries,
                "response_fields": response_fields,
                "unknown_response_fields": unknown_response_fields,
                "page_limit": page_limit,
                "page_offset": page_offset,
                "filter_ast": filter_ast,
                "as_of": as_of,
                "sort": sort,
            }
        )
        return StubResults([dict(row) for row in self.rows])


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


def make_config() -> OptimadeConfig:
    config = OptimadeConfig()
    config.data_available = {"structures": 3, "calculations": 2}
    return config


def test_base_endpoint_is_html() -> None:
    output = process(make_request("/"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.content_type == "text/html"
    assert output.response_code == 200
    assert output.content is not None


def test_versions_endpoint_is_csv() -> None:
    output = process(make_request("/versions"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.content_type == "text/csv; header=present"
    assert output.content == "version\n1\n"


def test_info_endpoint() -> None:
    output = process(make_request("/info"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.json_response is not None
    assert output.json_response["data"]["type"] == "info"


def test_links_endpoint() -> None:
    output = process(make_request("/links"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.json_response is not None
    assert output.json_response["data"][-1]["id"] == "optimade"


def test_entry_info_endpoint() -> None:
    output = process(make_request("/info/structures"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.json_response is not None
    assert "elements" in output.json_response["data"]["properties"]


def test_entry_endpoint_lists_data() -> None:
    query_function = StubQueryFunction([{"id": "a", "type": "structures", "nelements": 2}])
    output = process(make_request("/structures"), query_function, "1.3.0", make_config(), materials_schema())
    assert output.json_response is not None
    assert output.json_response["data"][0]["id"] == "a"
    assert query_function.calls[0]["entries"] == ["structures"]
    assert query_function.calls[0]["filter_ast"] is None
    assert "id" in query_function.calls[0]["response_fields"]


def test_entry_endpoint_with_filter_parses_ast() -> None:
    query_function = StubQueryFunction()
    process(make_request("/structures?filter=nelements=3"), query_function, "1.3.0", make_config(), materials_schema())
    assert query_function.calls[0]["filter_ast"] == ("=", ("Identifier", "nelements"), ("Number", "3"))


def test_entry_endpoint_captures_and_reuses_microsecond_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    class MoreResultsQuery(StubQueryFunction):
        def __call__(self, *args: Any, **kwargs: Any) -> StubResults:
            results = super().__call__(*args, **kwargs)
            results.more_data_available = True
            return results

    query_function = MoreResultsQuery([{"id": "a", "type": "structures"}])
    monkeypatch.setattr("httk.serve.optimade.engine.processing.time.time_ns", lambda: 1234567891)
    output = process(
        make_request("/structures?page_limit=1"),
        query_function,
        "1.3.0",
        make_config(),
        materials_schema(),
        snapshot_cutoff_ns=lambda _entry, now_ns: (now_ns // 1000) * 1000 - 1,
    )

    assert query_function.calls[0]["as_of"] == 1234566999
    assert "_httk_as_of=1234566999" in output.json_response["links"]["next"]  # type: ignore[index]


def test_entry_endpoint_preserves_supplied_snapshot() -> None:
    query_function = StubQueryFunction([{"id": "a", "type": "structures"}])

    class MoreResultsQuery(StubQueryFunction):
        def __call__(self, *args: Any, **kwargs: Any) -> StubResults:
            results = super().__call__(*args, **kwargs)
            results.more_data_available = True
            return results

    output = process(
        make_request("/structures?page_limit=1&_httk_as_of=42"),
        MoreResultsQuery(query_function.rows),
        "1.3.0",
        make_config(),
        materials_schema(),
        snapshot_cutoff_ns=lambda _entry, _now_ns: 999,
    )

    assert output.json_response is not None
    assert "_httk_as_of=42" in output.json_response["links"]["next"]


def test_entry_endpoint_with_bad_filter_raises_400() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        process(
            make_request("/structures?filter=elements HAS"),
            StubQueryFunction(),
            "1.3.0",
            make_config(),
            materials_schema(),
        )
    assert excinfo.value.response_code == 400


def test_single_entry_endpoint_builds_id_filter() -> None:
    query_function = StubQueryFunction([{"id": "abc", "type": "structures"}])
    output = process(make_request("/structures/abc"), query_function, "1.3.0", make_config(), materials_schema())
    assert query_function.calls[0]["filter_ast"] == ("=", ("Identifier", "id"), ("String", "abc"))
    assert output.json_response is not None
    assert output.json_response["data"]["id"] == "abc"


def test_versioned_request() -> None:
    output = process(make_request("/v1/info"), StubQueryFunction(), "1.3.0", make_config(), materials_schema())
    assert output.json_response is not None
    assert output.json_response["meta"]["api_version"] == "1.3.0"


def test_versioned_request_with_caller_supplied_endpoint_preserves_next_link() -> None:
    request = RawRequest(
        baseurl="http://localhost/outer/v1/",
        representation="/v1/structures?page_limit=1",
        endpoint="structures",
    )

    class MoreResultsQuery(StubQueryFunction):
        def __call__(self, *args: Any, **kwargs: Any) -> StubResults:
            results = super().__call__(*args, **kwargs)
            results.more_data_available = True
            return results

    output = process(
        request,
        MoreResultsQuery([{"id": "a", "type": "structures"}]),
        "1.3.0",
        make_config(),
        materials_schema(),
    )

    assert output.json_response is not None
    assert output.json_response["links"]["next"].startswith("http://localhost/outer/v1/v1/structures?")


def test_process_init_fills_data_available() -> None:
    config = OptimadeConfig()
    query_function = StubQueryFunction([{"id": "a", "type": "structures"}, {"id": "b", "type": "structures"}])
    process_init(config, query_function, materials_schema())
    assert config.data_available == {"structures": 2, "calculations": 2}
    assert all(call["page_limit"] == 0 for call in query_function.calls)
