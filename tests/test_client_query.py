"""No-network tests for the synchronous remote OPTIMADE query/result layer."""

import datetime
import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from urllib.parse import parse_qs, urlsplit

import pytest
from httk.atomistic import OptimadeStructure
from httk.core import load_entry_type_definition
from httk.core.optimade import OptimadeFile, OptimadeResource, parse_optimade_filter
from httk.data import CountUnavailableError as NeutralCountUnavailableError
from httk.data import MultipleResultsError, NoResultError, UnsupportedQueryError

from httk.serve.optimade import (
    ALL_ADVERTISED,
    CountUnavailableError,
    OptimadePaginationError,
    OptimadeResponseError,
    OptimadeStore,
    OptimadeTransportError,
)

FILES = "https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/files"
STRUCTURES = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/structures"
_DEFAULT = object()


@dataclass
class FakeResponse:
    status_code: int
    text: str


class QueryClient:
    def __init__(
        self,
        endpoint: str,
        properties: dict[str, object],
        query_responses: list[FakeResponse],
        *,
        describedby: str | None,
        base_url: str = "https://example.test/v1",
    ) -> None:
        self.endpoint = endpoint
        self.base_url = base_url
        self.requests: list[str] = []
        self.query_responses = list(query_responses)
        self.closed = False
        info_entry: dict[str, object] = {
            "data": {"type": "info", "properties": properties},
        }
        if describedby is not None:
            info_entry["links"] = {"describedby": describedby}
        self.discovery = {
            base_url + "/info": response({"data": {"type": "info", "attributes": {"available_endpoints": [endpoint]}}}),
            base_url + "/info/" + endpoint: response(info_entry),
        }

    def get(self, url: str) -> FakeResponse:
        self.requests.append(url)
        if url in self.discovery:
            return self.discovery[url]
        if not self.query_responses:
            raise AssertionError(f"unexpected external request: {url}")
        return self.query_responses.pop(0)

    def close(self) -> None:
        self.closed = True


def response(value: object, status_code: int = 200) -> FakeResponse:
    return FakeResponse(status_code, json.dumps(value, separators=(",", ":")))


def page(
    resources: Sequence[object],
    *,
    next_link: object | None = None,
    more: bool = False,
    available: object | None = None,
    returned: object = _DEFAULT,
) -> FakeResponse:
    value: dict[str, object] = {"data": resources, "meta": {"more_data_available": more}}
    if available is not None:
        value["meta"]["data_available"] = available  # type: ignore[index]
    value["meta"]["data_returned"] = len(resources) if returned is _DEFAULT else returned  # type: ignore[index]
    if next_link is not None:
        value["links"] = {"next": next_link}
    return response(value)


def property_document(
    definition_id: str | None,
    *,
    sortable: bool = False,
    response_default: bool = False,
) -> dict[str, object]:
    value: dict[str, object] = {}
    if definition_id is not None:
        value["$id"] = definition_id
    implementation: dict[str, object] = {}
    if sortable:
        implementation["sortable"] = True
    if response_default:
        implementation["response-default"] = True
    if implementation:
        value["x-optimade-implementation"] = implementation
    return value


def schema_properties(
    definition_id: str,
    names: tuple[str, ...],
    *,
    renames: dict[str, str] | None = None,
    sortable: tuple[str, ...] = (),
    response_defaults: tuple[str, ...] = (),
) -> dict[str, object]:
    schema = load_entry_type_definition(definition_id)
    renames = {} if renames is None else renames
    return {
        renames.get(name, name): property_document(
            schema.properties[name].definition_id,
            sortable=name in sortable,
            response_default=name in response_defaults,
        )
        for name in names
    }


def resource(
    identifier: str,
    endpoint: str,
    attributes: dict[str, object] | None = None,
    **extra: object,
) -> dict[str, object]:
    value: dict[str, object] = {"id": identifier, "type": endpoint}
    if attributes is not None:
        value["attributes"] = attributes
    value.update(extra)
    return value


def make_files(
    query_responses: list[FakeResponse],
    *,
    renames: dict[str, str] | None = None,
    sortable: tuple[str, ...] = (),
    response_fields: object = None,
    response_defaults: tuple[str, ...] = (),
) -> tuple[OptimadeStore, QueryClient]:
    names = ("id", "type", "immutable_id", "last_modified", "url")
    client = QueryClient(
        "renamed-files",
        schema_properties(
            FILES,
            names,
            renames=renames,
            sortable=sortable,
            response_defaults=response_defaults,
        ),
        query_responses,
        describedby=FILES,
    )
    return OptimadeStore(client.base_url, client=client, response_fields=response_fields), client


def make_structures(
    query_responses: list[FakeResponse],
    *,
    sortable: tuple[str, ...] = (),
) -> tuple[OptimadeStore, QueryClient]:
    names = (
        "id",
        "type",
        "immutable_id",
        "last_modified",
        "elements",
        "nelements",
        "elements_ratios",
        "chemical_formula_descriptive",
        "chemical_formula_reduced",
        "chemical_formula_anonymous",
        "nperiodic_dimensions",
        "nsites",
        "structure_features",
    )
    client = QueryClient(
        "structures-vendor-name",
        schema_properties(STRUCTURES, names, sortable=sortable),
        query_responses,
        describedby=STRUCTURES,
    )
    return OptimadeStore(client.base_url, client=client), client


def test_negotiated_base_routes_queries_through_effective_versioned_url() -> None:
    requested = "https://example.test/db"
    effective = requested + "/v1"
    client = QueryClient(
        "files",
        schema_properties(FILES, ("id", "type")),
        [page([])],
        describedby=FILES,
        base_url=effective,
    )
    client.discovery[requested + "/versions"] = FakeResponse(200, "version\n1\n")
    store = OptimadeStore(requested, client=client)
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    searcher.output(variable, "record")

    assert list(searcher) == []

    query_url = client.requests[-1]
    assert query_url.startswith(effective + "/files?")
    assert not query_url.startswith(requested + "/files?")


def query_parameters(client: QueryClient, index: int = 2) -> dict[str, list[str]]:
    return parse_qs(urlsplit(client.requests[index]).query)


def test_structural_filters_exact_literals_and_transport_renaming() -> None:
    store, client = make_structures([page([])], sortable=("nelements", "id"))
    descriptor = store.entry_types[0]
    searcher = store.searcher()
    variable = searcher.variable(OptimadeStructure)
    injection = 'x") OR id IS KNOWN OR ("'
    expression = (
        (variable.nelements >= 2)
        & variable.chemical_formula_descriptive.contains(injection)
        & variable.elements.has("Si")
        & variable.elements.has_any("Si", "O")
        & variable.elements_ratios.has_only(Fraction(1, 8), Fraction(-3, 20), Decimal("0.1250"))
        & variable.id.is_in("one", "two")
        & variable.chemical_formula_descriptive.endswith("O2")
        & (variable.last_modified >= datetime.datetime(2026, 7, 30, tzinfo=datetime.UTC))
        & (variable.last_modified != None)
    )
    searcher.add((expression | variable.always_false()) & variable.always_true())
    searcher.add_sort(variable.nelements, descending=True)
    searcher.add_sort(variable.id)
    searcher.output(variable, "structure")

    assert list(searcher) == []

    parameters = query_parameters(client)
    rendered = parameters["filter"][0]
    assert "nelements >= 2" in rendered
    assert 'chemical_formula_descriptive CONTAINS "x\\") OR id IS KNOWN OR (\\""' in rendered
    assert 'elements HAS "Si"' in rendered
    assert 'elements HAS ANY "Si", "O"' in rendered
    assert "elements_ratios HAS ONLY 0.125, -0.15, 0.125" in rendered
    assert '(id = "one") OR (id = "two")' in rendered
    assert 'chemical_formula_descriptive ENDS WITH "O2"' in rendered
    assert 'last_modified >= "2026-07-30T00:00:00+00:00"' in rendered
    assert "last_modified IS KNOWN" in rendered
    assert "id IS UNKNOWN" in rendered
    assert "id IS KNOWN" in rendered
    assert parameters["sort"] == ["-nelements,id"]
    assert descriptor.backend is OptimadeStructure
    parse_optimade_filter(rendered)


@pytest.mark.parametrize(
    ("fraction", "rendered"),
    [
        (Fraction(1, 2), "0.5"),
        (Fraction(1, 4), "0.25"),
        (Fraction(1, 5), "0.2"),
        (Fraction(1, 8), "0.125"),
        (Fraction(7, 20), "0.35"),
        (Fraction(-1, 8), "-0.125"),
    ],
)
def test_terminating_fraction_spellings_are_exact(fraction: Fraction, rendered: str) -> None:
    store, client = make_structures([page([])])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    searcher.add(variable.elements_ratios.has_any(fraction))
    searcher.output(variable, "record")

    list(searcher)

    assert f"elements_ratios HAS ANY {rendered}" in query_parameters(client)["filter"][0]


def test_operator_and_portability_failures_happen_before_http() -> None:
    store, client = make_structures([])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    discovery_requests = len(client.requests)

    with pytest.raises(UnsupportedQueryError, match="second root"):
        searcher.variable(store.entry_types[0])
    with pytest.raises(UnsupportedQueryError, match="traversal"):
        _value = variable.id.relationship
    with pytest.raises(UnsupportedQueryError, match="field-to-field"):
        _value = variable.id.__eq__(variable.type)
    with pytest.raises(UnsupportedQueryError, match="HAS ANY on non-list"):
        variable.id.has_any("x")
    with pytest.raises(UnsupportedQueryError, match="scalar is_in on list"):
        variable.elements.is_in("Si")
    with pytest.raises(UnsupportedQueryError, match="binary float"):
        variable.elements_ratios.has_any(0.5)
    with pytest.raises(UnsupportedQueryError, match="non-terminating"):
        variable.elements_ratios.has_any(Fraction(1, 3))
    with pytest.raises(UnsupportedQueryError, match="string matching"):
        variable.nelements.contains("2")
    with pytest.raises(UnsupportedQueryError, match="query-support 'equality only'"):
        variable.chemical_formula_reduced.contains("Si")
    with pytest.raises(UnsupportedQueryError, match="query-support 'equality only'"):
        variable.chemical_formula_reduced.startswith("Si")
    with pytest.raises(UnsupportedQueryError, match="query-support 'equality only'"):
        variable.chemical_formula_reduced.endswith("Si")
    with pytest.raises(UnsupportedQueryError, match="query-support 'equality only'"):
        _value = variable.chemical_formula_reduced < "Si"
    with pytest.raises(UnsupportedQueryError, match="HAS ANY on non-list"):
        variable.chemical_formula_reduced.has_any("Si")
    with pytest.raises(UnsupportedQueryError, match="different searchers"):
        other, _other_client = make_structures([])
        other_variable = other.searcher().variable(other.entry_types[0])
        (variable.id == "x") & (other_variable.id == "x")

    assert len(client.requests) == discovery_requests


def test_equality_only_membership_with_null_is_rendered_as_explicit_unknown() -> None:
    store, client = make_structures([page([])])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    searcher.add(variable.chemical_formula_reduced.is_in(None, "Si"))
    searcher.output(variable, "record")

    assert list(searcher) == []
    rendered = query_parameters(client)["filter"][0]
    assert "chemical_formula_reduced IS UNKNOWN" in rendered
    assert 'chemical_formula_reduced = "Si"' in rendered


def test_typed_projection_decodes_timestamp_and_result_rows_are_named() -> None:
    renames = {"last_modified": "vendor_time", "immutable_id": "vendor_immutable"}
    timestamp = "2026-07-30T12:34:56+00:00"
    store, client = make_files(
        [
            page(
                [
                    resource(
                        "f-1",
                        "renamed-files",
                        {
                            "vendor_time": timestamp,
                            "vendor_immutable": "stable",
                            "unknown_vendor": {"kept": [1, 2]},
                        },
                        relationships={"references": {"data": [{"id": "r1", "type": "references"}]}},
                    )
                ]
            )
        ]
        * 3,
        renames=renames,
    )
    searcher = store.searcher(response_fields=("id",))
    variable = searcher.variable(OptimadeFile)
    results = searcher.results(record=variable, modified=variable.last_modified)

    row = results.one()

    assert row.names == ("record", "modified")
    assert row["record"] is row.record
    assert row[1] == datetime.datetime(2026, 7, 30, 12, 34, 56, tzinfo=datetime.UTC)
    assert isinstance(row.record, OptimadeFile)
    assert row.record.raw["relationships"] is not None
    assert row.record.raw["attributes"]["unknown_vendor"] is not None  # type: ignore[index]
    assert query_parameters(client)["response_fields"] == ["id,vendor_time"]
    assert next(iter(results.scalars("modified"))) == row.modified
    assert [value for value in results.column("modified")] == [row.modified]
    with pytest.raises(TypeError, match="object output"):
        results.column("record")
    with pytest.raises(NotImplementedError):
        results.cursor()


def test_default_response_policy_preserves_whole_record_for_advertised_scalar() -> None:
    store, client = make_files(
        [
            page(
                [
                    resource(
                        "f-1",
                        "renamed-files",
                        {
                            "last_modified": "2026-07-30T12:34:56+00:00",
                            "vendor_extension": {"kept": True},
                        },
                    )
                ]
            )
        ],
        response_defaults=("last_modified",),
    )
    searcher = store.searcher()  # None means do not send a response_fields parameter.
    variable = searcher.variable(store.entry_types[0])
    row = searcher.results(record=variable, modified=variable.last_modified).one()

    assert row.modified == datetime.datetime(2026, 7, 30, 12, 34, 56, tzinfo=datetime.UTC)
    assert row.record.raw["attributes"]["vendor_extension"] == {"kept": True}  # type: ignore[index]
    assert "response_fields" not in query_parameters(client)


def test_default_response_policy_uses_all_advertised_fields_without_default_metadata() -> None:
    store, client = make_files(
        [page([resource("f-1", "renamed-files", {"last_modified": "2026-07-30T12:34:56+00:00"})])]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    results = searcher.results(record=variable, modified=variable.last_modified)

    assert results.one().modified == datetime.datetime(2026, 7, 30, 12, 34, 56, tzinfo=datetime.UTC)

    # With no advertised default set, the conservative fallback is the full
    # advertised endpoint vocabulary, never a scalar-only thin resource.
    assert query_parameters(client)["response_fields"] == ["id,type,immutable_id,last_modified,url"]


def test_default_response_policy_adds_nondefault_scalar_after_declared_defaults() -> None:
    store, client = make_files(
        [
            page(
                [
                    resource(
                        "f-1",
                        "renamed-files",
                        {"immutable_id": "stable", "last_modified": "2026-07-30T12:34:56+00:00"},
                    )
                ]
            )
        ],
        response_defaults=("immutable_id",),
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    assert searcher.results(record=variable, modified=variable.last_modified).one().modified == datetime.datetime(
        2026, 7, 30, 12, 34, 56, tzinfo=datetime.UTC
    )
    assert query_parameters(client)["response_fields"] == ["immutable_id,id,type,last_modified"]


def test_scalar_only_default_policy_requests_its_projection() -> None:
    store, client = make_files(
        [page([resource("f-1", "renamed-files", {"last_modified": "2026-07-30T12:34:56+00:00"})])]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    assert [row.modified for row in searcher.results(modified=variable.last_modified)] == [
        datetime.datetime(2026, 7, 30, 12, 34, 56, tzinfo=datetime.UTC)
    ]
    assert query_parameters(client)["response_fields"] == ["last_modified"]


def test_structure_list_projection_decodes_tuples_and_exact_fractions() -> None:
    store, _client = make_structures(
        [
            page(
                [
                    resource(
                        "s1",
                        "structures-vendor-name",
                        {"elements": ["O", "Si"], "elements_ratios": [0.3333333333333333, 0.6666666666666667]},
                    )
                ]
            )
        ]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    row = searcher.results(elements=variable.elements, ratios=variable.elements_ratios).one()

    assert row.elements == ("O", "Si")
    assert row.ratios == (
        Fraction(3333333333333333, 10**16),
        Fraction(6666666666666667, 10**16),
    )


def test_generic_descriptor_queries_exact_advertised_legacy_fields_without_semantic_binding() -> None:
    client = QueryClient(
        "legacy-things",
        {"legacy": {"type": "list"}},
        [page([resource("g1", "legacy-things", {"legacy": ["value"]})])],
        describedby=None,
    )
    store = OptimadeStore(client.base_url, client=client)
    descriptor = store.entry_types[0]
    assert descriptor.backend is OptimadeResource
    searcher = store.searcher(response_fields=ALL_ADVERTISED)
    variable = searcher.variable(OptimadeResource)
    searcher.add(variable.id.startswith("g") & variable.legacy.has("value"))
    row = searcher.results(item=variable, identifier=variable.id).one()

    assert isinstance(row.item, OptimadeResource)
    assert row.identifier == "g1"
    assert row.item["attributes"]["legacy"] == ("value",)  # type: ignore[index]
    assert "id STARTS WITH" in query_parameters(client)["filter"][0]
    assert 'legacy HAS "value"' in query_parameters(client)["filter"][0]


def test_backend_class_requires_an_unambiguous_endpoint() -> None:
    base = "https://example.test/v1"
    properties = schema_properties(FILES, ("id", "type"))
    client = QueryClient("one", properties, [], describedby=FILES, base_url=base)
    client.discovery[base + "/info"] = response(
        {"data": {"type": "info", "attributes": {"available_endpoints": ["one", "two"]}}}
    )
    client.discovery[base + "/info/two"] = response(
        {"data": {"type": "info", "properties": properties}, "links": {"describedby": FILES}}
    )
    store = OptimadeStore(base, client=client)

    with pytest.raises(UnsupportedQueryError, match="multiple endpoints"):
        store.searcher().variable(OptimadeFile)
    variable = store.searcher().variable(store.entry_type("one"))
    assert variable.id is not None


def test_count_uses_data_available_and_len_applies_offset_limit_and_cache() -> None:
    store, client = make_files([page([], available=9, returned=0)])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    searcher.output(variable, "record")
    searcher.add_offset(3)
    searcher.set_limit(4)
    results = searcher.results()

    assert searcher.count() == 9
    assert searcher.count() == 9
    assert len(results) == 4
    assert len(client.requests) == 3
    params = query_parameters(client)
    assert params["page_limit"] == ["1"]
    assert params["response_fields"] == ["id"]


@pytest.mark.parametrize("available", [None, True, -1, "9"])
def test_count_requires_valid_data_available(available: object | None) -> None:
    store, _client = make_files([page([], available=available)])
    searcher = store.searcher()
    searcher.variable(store.entry_types[0])
    with pytest.raises(CountUnavailableError):
        searcher.count()


def test_count_unavailable_error_preserves_optimade_and_neutral_categories() -> None:
    assert issubclass(CountUnavailableError, OptimadeResponseError)
    assert issubclass(CountUnavailableError, NeutralCountUnavailableError)


def test_first_one_and_slice_use_small_frozen_probes() -> None:
    rows = [resource("a", "renamed-files"), resource("b", "renamed-files"), resource("c", "renamed-files")]
    store, client = make_files(
        [
            page(rows),
            page(rows),
            page(rows),
            page([], available=3),
        ]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    results = searcher.results(record=variable)
    frozen = results[1:3]
    searcher.add(variable.id == "later-mutation")

    assert results.first().record.id == "a"
    with pytest.raises(MultipleResultsError):
        results.one()
    assert [row.record.id for row in frozen] == ["b", "c"]
    assert len(frozen) == 2
    assert query_parameters(client, 2)["page_limit"] == ["1"]
    assert "filter" not in query_parameters(client, 2)
    assert query_parameters(client, 3)["page_limit"] == ["2"]
    with pytest.raises(ValueError):
        results[::2]
    with pytest.raises(TypeError):
        results[0]  # type: ignore[index]


def test_empty_and_single_one_errors() -> None:
    store, _client = make_files([page([]), page([resource("a", "renamed-files")])])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    results = searcher.results(record=variable)
    with pytest.raises(NoResultError):
        results.one()
    assert results.one().record.id == "a"


def test_pagination_uses_raw_token_but_resources_retain_only_redacted_documents() -> None:
    secret = "cursor-secret"
    semantic_url = "https://user:semantic@example.test/file?token=semantic-secret"
    semantic_key = "https://example.test/key?api_key=semantic-key"
    semantic_relationship = "https://user:semantic@example.test/related?token=semantic-related"
    raw_first = json.dumps(
        {
            "data": [
                resource(
                    "a",
                    "renamed-files",
                    {
                        "url": semantic_url,
                        semantic_key: "exact-key",
                        "nested": {"links": {"next": "?token=semantic-nested"}},
                    },
                    relationships={"references": {"links": {"related": semantic_relationship}}},
                    vendor_extension={"url": "?token=semantic-extension"},
                )
            ],
            "links": {"next": f"/v1/renamed-files?token={secret}"},
            "meta": {"more_data_available": True, "data_returned": 1, "spelling": 1.2300},
        },
        separators=(",", ":"),
    ).replace("1.23", "1.2300")
    store, client = make_files(
        [
            FakeResponse(200, raw_first),
            page([resource("b", "renamed-files")]),
            FakeResponse(200, raw_first),
            page([resource("b", "renamed-files")]),
        ]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    results = searcher.results(record=variable)

    first_pass = [row.record for row in results]
    second_pass = [row.record for row in results]

    assert [item.id for item in first_pass] == ["a", "b"]
    assert [item.id for item in second_pass] == ["a", "b"]
    assert secret in client.requests[3]
    assert secret not in first_pass[0].resource.document.text
    assert secret not in first_pass[1].resource.document.source_url
    assert "1.2300" in first_pass[0].resource.document.text
    assert first_pass[0].resource.document is not first_pass[1].resource.document
    raw = first_pass[0].raw
    assert raw["attributes"]["url"] == semantic_url  # type: ignore[index]
    assert raw["attributes"][semantic_key] == "exact-key"  # type: ignore[index]
    assert raw["attributes"]["nested"]["links"]["next"] == "?token=semantic-nested"  # type: ignore[index]
    assert raw["relationships"]["references"]["links"]["related"] == semantic_relationship  # type: ignore[index]
    assert raw["vendor_extension"]["url"] == "?token=semantic-extension"  # type: ignore[index]


def test_page_resources_share_one_document_and_link_objects_work() -> None:
    store, _client = make_files(
        [
            page(
                [resource("a", "renamed-files"), resource("b", "renamed-files")],
                next_link={"href": "?page_offset=2"},
                more=True,
            ),
            page([resource("c", "renamed-files")]),
        ]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])
    values = [row.record for row in searcher.results(record=variable)]

    assert [item.id for item in values] == ["a", "b", "c"]
    assert values[0].resource.document is values[1].resource.document
    assert values[1].resource.document is not values[2].resource.document


def test_pagination_guards_cycles_max_pages_cross_origin_and_missing_next() -> None:
    initial_suffix = "/v1/renamed-files?page_limit=100"
    cases = [
        (
            [page([resource("a", "renamed-files")], next_link=initial_suffix, more=True)],
            {},
            "cycle",
        ),
        (
            [
                page([resource("a", "renamed-files")], next_link="?page_offset=1", more=True),
            ],
            {"max_pages": 1},
            "max_pages",
        ),
        (
            [page([resource("a", "renamed-files")], next_link="https://other.test/items", more=True)],
            {},
            "cross-origin",
        ),
        (
            [page([resource("a", "renamed-files")], more=True)],
            {},
            "without a usable",
        ),
    ]
    for query_responses, options, pattern in cases:
        store, _client = make_files(query_responses)
        if options:
            store.max_pages = options["max_pages"]
        searcher = store.searcher()
        variable = searcher.variable(store.entry_types[0])
        with pytest.raises(OptimadePaginationError, match=pattern):
            [row for row in searcher.results(record=variable)]


def test_default_ports_are_same_origin_and_cross_origin_can_be_enabled() -> None:
    same_origin_store, _same_origin_client = make_files(
        [
            page([resource("a", "renamed-files")], next_link="https://example.test:443/next"),
            page([resource("b", "renamed-files")]),
        ]
    )
    same_searcher = same_origin_store.searcher()
    same_variable = same_searcher.variable(same_origin_store.entry_types[0])
    assert [row.record.id for row in same_searcher.results(record=same_variable)] == ["a", "b"]

    store, client = make_files(
        [
            page([resource("a", "renamed-files")], next_link="https://other.test/next"),
            page([resource("b", "renamed-files")]),
        ]
    )
    store.allow_cross_origin_pagination = True
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    assert [row.record.id for row in searcher.results(record=variable)] == ["a", "b"]
    assert "other.test" in client.requests[3]


def test_non_http_pagination_is_rejected_even_when_cross_origin_is_allowed() -> None:
    store, client = make_files([page([resource("a", "renamed-files")], next_link="file:///tmp/not-http")])
    store.allow_cross_origin_pagination = True
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    with pytest.raises(OptimadePaginationError, match=r"HTTP\(S\)"):
        [row for row in searcher.results(record=variable)]

    assert len(client.requests) == 3


def test_query_only_credentials_are_redacted_from_transport_diagnostics() -> None:
    store, client = make_files([])
    original_get = client.get

    def broken_get(url: str) -> FakeResponse:
        if url in client.discovery:
            return original_get(url)
        raise RuntimeError("why?question stayed; ?page=1 stayed; offline at ?page_cursor=opaque&token=query-secret")

    client.get = broken_get  # type: ignore[method-assign]
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    with pytest.raises(OptimadeTransportError) as excinfo:
        [row for row in searcher.results(record=variable)]

    assert "query-secret" not in str(excinfo.value)
    assert "why?question stayed" in str(excinfo.value)
    assert "?page=1 stayed" in str(excinfo.value)


def test_frozen_result_survives_refresh_and_slicing_with_old_schema_snapshot() -> None:
    store, _client = make_files([page([resource("a", "renamed-files")])])
    old_descriptor = store.entry_types[0]
    searcher = store.searcher()
    variable = searcher.variable(old_descriptor)
    frozen = searcher.results(record=variable)

    store.refresh()
    assert store.entry_types[0] is not old_descriptor
    sliced = frozen[:1]
    record = sliced.one().record

    assert record.id == "a"
    assert record.resource.schema is old_descriptor.schema


@pytest.mark.parametrize(
    "bad_page",
    [
        {"data": [], "meta": {"data_returned": True}},
        {"data": [], "meta": {"data_returned": 1}},
        {"data": [resource("x", "other-endpoint")], "meta": {"data_returned": 1}},
        {
            "data": [resource("x", "renamed-files", attributes=["not", "an", "object"])],
            "meta": {"data_returned": 1},
        },
        {
            "data": [resource("x", "renamed-files", relationships=["not", "an", "object"])],
            "meta": {"data_returned": 1},
        },
        {
            "data": [resource("x", "renamed-files", attributes=None)],
            "meta": {"data_returned": 1},
        },
        {
            "data": [resource("x", "renamed-files", relationships=None)],
            "meta": {"data_returned": 1},
        },
    ],
)
def test_entry_envelope_validation_rejects_inconsistent_page_shapes(bad_page: dict[str, object]) -> None:
    store, _client = make_files([response(bad_page)])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    with pytest.raises(OptimadeResponseError):
        list(searcher.results(record=variable))


@pytest.mark.parametrize(
    "meta",
    [
        {"more_data_available": False},
        {"more_data_available": False, "data_returned": 1},
    ],
)
def test_entry_envelope_accepts_missing_or_valid_data_returned(meta: dict[str, object]) -> None:
    store, _client = make_files([response({"data": [resource("f-1", "renamed-files")], "meta": meta})])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    assert [item.record.id for item in searcher.results(record=variable)] == ["f-1"]


def test_entry_envelope_is_validated_before_any_page_item_is_yielded() -> None:
    store, _client = make_files(
        [
            response(
                {
                    "data": [
                        resource("first", "renamed-files", {"url": "one"}),
                        resource("second", "renamed-files", attributes=["bad"]),
                    ],
                    "meta": {"data_returned": 2},
                }
            )
        ]
    )
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    with pytest.raises(OptimadeResponseError):
        next(iter(searcher.results(record=variable)))


@pytest.mark.parametrize(
    "bad",
    [
        FakeResponse(200, "not json"),
        response({"data": {}}),
        response({"data": [1]}),
        response({"data": [{}]}),
        response({"data": [{"id": "", "type": "renamed-files"}]}),
        response({"data": [{"id": "x", "type": None}]}),
        response({"errors": [{"detail": "failed at /next?token=secret"}]}),
        response({"data": [], "links": {"next": {"href": 3}}}),
        response({"data": [], "links": {"next": " "}}),
    ],
)
def test_malformed_entry_pages_fail_safely(bad: FakeResponse) -> None:
    store, _client = make_files([bad])
    searcher = store.searcher()
    variable = searcher.variable(store.entry_types[0])

    with pytest.raises(OptimadeResponseError) as excinfo:
        [row for row in searcher.results(record=variable)]

    assert "secret" not in str(excinfo.value)
