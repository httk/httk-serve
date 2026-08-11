from collections.abc import Iterable, Iterator, Mapping
from typing import Any

import pytest
from definition_fixtures import served_schema
from fake_backend import FakeStore
from httk.core import (
    EntryProvider,
    EntryTypeDefinition,
    PropertyDefinition,
    RelatedEntry,
)
from starlette.testclient import TestClient

from httk.serve.optimade import (
    BackendAdapter,
    EntrySource,
    OptimadeError,
    RawRequest,
    adapter_from_providers,
    create_asgi_app,
)
from httk.serve.optimade.backend import simple_property_handlers, translate_filter
from httk.serve.optimade.backend.handlers import set_handler
from httk.serve.optimade.endpoints.entries import generate_entry_endpoint_reply
from httk.serve.optimade.engine import process
from httk.serve.optimade.engine.processing import _make_related_resolver
from httk.serve.optimade.engine.validate import validate_optimade_request
from httk.serve.optimade.filter import parse_optimade_filter
from httk.serve.optimade.model import (
    OptimadeConfig,
    ResultRow,
    ValidatedParameters,
    ValidatedRequest,
)
from httk.serve.optimade.schema.served import ServedSchema

REFERENCE_KEYS = {'doi': 'doi', 'title': 'title'}


def relationships_schema() -> ServedSchema:
    return served_schema(
        {
            'structures': ['id', 'type', 'nelements'],
            'references': ['id', 'type', 'title', 'doi'],
        },
        default_response_overrides={
            'structures': ['nelements'],
            'references': ['title', 'doi'],
        },
    )


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


# --- include parsing ----------------------------------------------------------


def test_include_defaults_to_references() -> None:
    schema = relationships_schema()
    validated = validate_optimade_request(make_request("/structures"), "1.3.0", schema)
    assert validated.include_paths == ["references"]
    # Not echoed when the client did not provide it:
    assert validated.query.include is None


def test_include_empty_string_means_none() -> None:
    schema = relationships_schema()
    validated = validate_optimade_request(make_request("/structures?include="), "1.3.0", schema)
    assert validated.include_paths == []
    assert validated.query.include == ""
    assert validated.query.as_query_dict()["include"] == ""


def test_include_explicit_paths() -> None:
    schema = relationships_schema()
    validated = validate_optimade_request(make_request("/structures?include=references"), "1.3.0", schema)
    assert validated.include_paths == ["references"]
    assert validated.query.include == "references"


def test_include_default_filtered_when_references_unserved() -> None:
    schema = served_schema({"structures": ["id", "type"]})
    validated = validate_optimade_request(make_request("/structures"), "1.3.0", schema)
    assert validated.include_paths == []


def test_include_unknown_path_400() -> None:
    schema = relationships_schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?include=bogus"), "1.3.0", schema)
    assert excinfo.value.response_code == 400


def test_include_dotted_path_400() -> None:
    schema = relationships_schema()
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?include=references.id"), "1.3.0", schema)
    assert excinfo.value.response_code == 400


# --- entries reply emits relationships ----------------------------------------


class StubResults:
    def __init__(self, rows: list[ResultRow], more_data_available: bool = False) -> None:
        self.rows = rows
        self.more_data_available = more_data_available

    def count(self) -> int:
        return len(self.rows)

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(self.rows)


def make_validated(endpoint: str, include_paths: list[str] | None = None, **query_kwargs: Any) -> ValidatedRequest:
    return ValidatedRequest(
        baseurl="http://localhost/",
        representation="/" + endpoint,
        endpoint=endpoint,
        version="1.3.0",
        query=ValidatedParameters(**query_kwargs),
        include_paths=include_paths if include_paths is not None else [],
    )


def make_config() -> OptimadeConfig:
    config = OptimadeConfig()
    config.data_available = {"structures": 1, "references": 2}
    return config


def test_entry_reply_emits_relationships_with_meta() -> None:
    row = ResultRow(
        values={"id": "demo-1", "type": "structures"},
        relationships={"references": [{"id": "ref-1", "description": "Reference for this structure"}]},
    )
    reply = generate_entry_endpoint_reply(make_validated("structures"), make_config(), StubResults([row]))
    rels = reply["data"][0]["relationships"]
    assert rels["references"]["data"][0] == {
        "type": "references",
        "id": "ref-1",
        "meta": {"description": "Reference for this structure"},
    }


def test_entry_reply_included_via_resolver() -> None:
    row = ResultRow(
        values={"id": "demo-1", "type": "structures"},
        relationships={"references": [{"id": "ref-1", "description": "d"}]},
    )

    calls: list[dict[str, set[str]]] = []

    def resolver(collected: dict[str, set[str]]) -> list[dict[str, Any]]:
        calls.append(collected)
        return [{"type": "references", "id": "ref-1", "attributes": {"title": "T"}}]

    reply = generate_entry_endpoint_reply(
        make_validated("structures", include_paths=["references"]),
        make_config(),
        StubResults([row]),
        resolver,
    )
    assert calls == [{"references": {"ref-1"}}]
    assert reply["included"] == [{"type": "references", "id": "ref-1", "attributes": {"title": "T"}}]


def test_entry_reply_no_included_when_path_excluded() -> None:
    row = ResultRow(
        values={"id": "demo-1", "type": "structures"},
        relationships={"references": [{"id": "ref-1"}]},
    )

    def resolver(collected: dict[str, set[str]]) -> list[dict[str, Any]]:
        raise AssertionError("resolver should not be called")

    reply = generate_entry_endpoint_reply(
        make_validated("structures", include_paths=[]),
        make_config(),
        StubResults([row]),
        resolver,
    )
    assert "included" not in reply


# --- resolver dedupe ----------------------------------------------------------


class StubQueryFunction:
    def __init__(self, rows_by_entry: dict[str, list[ResultRow]]) -> None:
        self.rows_by_entry = rows_by_entry
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
        self.calls.append({"entries": entries, "response_fields": response_fields, "filter_ast": filter_ast})
        rows = self.rows_by_entry.get(entries[0], [])
        return StubResults(list(rows))


def test_resolver_dedupes_by_type_and_id() -> None:
    schema = relationships_schema()
    duplicate = ResultRow(values={"id": "ref-1", "type": "references", "title": "T"})
    other = ResultRow(values={"id": "ref-2", "type": "references", "title": "U"})
    qf = StubQueryFunction({"references": [duplicate, other, duplicate]})
    resolver = _make_related_resolver(qf, schema, "http://x/")
    included = resolver({"references": {"ref-1", "ref-2"}})
    ids = sorted(obj["id"] for obj in included)
    assert ids == ["ref-1", "ref-2"]
    # OR-chain of two ids was built:
    assert qf.calls[0]["filter_ast"][0] == "OR"


# --- process() compound document ----------------------------------------------


def test_process_compound_document_includes_references() -> None:
    structure_row = ResultRow(
        values={"id": "demo-1", "type": "structures"},
        relationships={"references": [{"id": "ref-1", "description": "Reference for this structure"}]},
    )
    reference_row = ResultRow(values={"id": "ref-1", "type": "references", "title": "T", "doi": "10.1/x"})
    qf = StubQueryFunction({"structures": [structure_row], "references": [reference_row]})
    output = process(make_request("/structures/demo-1"), qf, "1.3.0", make_config(), relationships_schema())
    assert output.json_response is not None
    payload = output.json_response
    assert payload["data"]["relationships"]["references"]["data"][0]["id"] == "ref-1"
    assert payload["included"][0]["type"] == "references"
    assert payload["included"][0]["id"] == "ref-1"


# --- relationship filtering (hand-wired mechanism regression) -----------------


def _structures_rel_adapter(store: FakeStore) -> BackendAdapter:
    schema = relationships_schema()
    field_handlers = {
        'references': simple_property_handlers('references', REFERENCE_KEYS, schema.entry_info['references']),
        'structures': simple_property_handlers('structures', {}, schema.entry_info['structures']),
    }
    structures_handlers = dict(field_handlers['structures'])
    structures_handlers['references.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type: set_handler('references', ops, values, has_type, sv),
    }
    field_handlers['structures'] = structures_handlers
    return BackendAdapter(
        store=store,
        sources={
            'structures': (EntrySource(target='structure-table', fields={}),),
            'references': (EntrySource(target='reference-table', fields={}),),
        },
        field_handlers=field_handlers,
        schema=schema,
    )


def test_relationship_id_has_produces_set_handler_tree() -> None:
    adapter = _structures_rel_adapter(FakeStore(rows_by_target={"structure-table": []}))
    pairs = translate_filter(parse_optimade_filter('references.id HAS "ref-1"'), ["structures"], adapter)
    _source, searcher = pairs[0]
    assert searcher.expressions[0].tree == ("has_any", ("column", "references"), ("ref-1",))  # type: ignore[attr-defined]


# --- ASGI end to end (auto path: adapter_from_providers + RelatedEntry) -------


def _rel_structures_definition() -> EntryTypeDefinition:
    return EntryTypeDefinition(
        "structures",
        "A structures entry.",
        {
            "id": PropertyDefinition.from_simple("id", description="id", required_response=True),
            "type": PropertyDefinition.from_simple("type", description="type", required_response=True),
            "nelements": PropertyDefinition.from_simple("nelements", description="n", fulltype="integer"),
        },
    )


def _rel_references_definition() -> EntryTypeDefinition:
    return EntryTypeDefinition(
        "references",
        "A references entry.",
        {
            "id": PropertyDefinition.from_simple("id", description="id", required_response=True),
            "type": PropertyDefinition.from_simple("type", description="type", required_response=True),
            "title": PropertyDefinition.from_simple("title", description="Title."),
            "doi": PropertyDefinition.from_simple("doi", description="DOI."),
            "year": PropertyDefinition.from_simple("year", description="Year.", fulltype="integer"),
            "keywords": PropertyDefinition.from_simple("keywords", description="Keywords.", fulltype="list of string"),
        },
    )


class AutoLinkedProvider(EntryProvider):
    """Structures related to references purely via declared RelatedEntry tuples.

    demo-1 relates to ref-1; demo-2 relates to ref-2 and ref-3; demo-3 has no
    relationships (exercising the empty synthetic ``__rel_references`` field).
    """

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {"structures": _rel_structures_definition(), "references": _rel_references_definition()}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        if entry_type == "structures":
            return {"id": "__id", "type": "type", "nelements": "nelements"}
        return {"id": "__id", "type": "type", "title": "title", "doi": "doi", "year": "year", "keywords": "keywords"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        if entry_type == "structures":
            return [
                {"__id": "demo-1", "type": "structures", "nelements": 2},
                {"__id": "demo-2", "type": "structures", "nelements": 3},
                {"__id": "demo-3", "type": "structures", "nelements": 1},
            ]
        return [
            {
                "__id": "ref-1",
                "type": "references",
                "title": "T",
                "doi": "10.1/a",
                "year": 2021,
                "keywords": ["alpha", "shared"],
            },
            {"__id": "ref-2", "type": "references", "title": "U", "doi": "10.9/b", "year": 1999, "keywords": ["beta"]},
            {"__id": "ref-3", "type": "references", "title": "V", "doi": "10.1/c", "year": 2005, "keywords": []},
        ]

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        if entry_type == "structures":
            return {
                "demo-1": (RelatedEntry("references", "ref-1", description="Reference for this structure"),),
                "demo-2": (RelatedEntry("references", "ref-2"), RelatedEntry("references", "ref-3")),
            }
        return {}


def make_auto_client() -> TestClient:
    adapter = adapter_from_providers([AutoLinkedProvider()])
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def _filtered_ids(client: TestClient, filter_string: str) -> list[str]:
    response = client.get("/structures", params={"filter": filter_string})
    assert response.status_code == 200
    return [entry["id"] for entry in response.json()["data"]]


def test_asgi_auto_relationships_block_with_meta() -> None:
    client = make_auto_client()
    response = client.get("/structures/demo-1")
    assert response.status_code == 200
    rels = response.json()["data"]["relationships"]["references"]["data"]
    assert rels == [
        {"type": "references", "id": "ref-1", "meta": {"description": "Reference for this structure"}},
    ]


def test_asgi_auto_include_references_compound_document() -> None:
    client = make_auto_client()
    response = client.get("/structures/demo-1", params={"include": "references"})
    assert response.status_code == 200
    payload = response.json()
    rels = payload["data"]["relationships"]["references"]["data"]
    assert rels[0]["id"] == "ref-1"
    assert rels[0]["meta"]["description"] == "Reference for this structure"
    assert [obj["id"] for obj in payload["included"]] == ["ref-1"]
    assert payload["included"][0]["type"] == "references"
    assert payload["included"][0]["attributes"]["title"] == "T"


def test_asgi_include_bogus_400() -> None:
    client = make_auto_client()
    response = client.get("/structures/demo-1", params={"include": "bogus"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["status"] == 400


def test_asgi_auto_relationship_id_has_filters_without_hand_wiring() -> None:
    client = make_auto_client()
    assert _filtered_ids(client, 'references.id HAS "ref-1"') == ["demo-1"]
    assert _filtered_ids(client, 'references.id HAS "ref-3"') == ["demo-2"]


# --- relationship-property filtering (two-phase semi-join) ---------------------


def test_related_property_stringmatching_matches() -> None:
    client = make_auto_client()
    assert _filtered_ids(client, 'references.doi CONTAINS "10.1"') == ["demo-1", "demo-2"]
    assert _filtered_ids(client, 'references.doi STARTS WITH "10.9"') == ["demo-2"]


def test_related_property_numeric_comparison_matches() -> None:
    client = make_auto_client()
    assert _filtered_ids(client, "references.year >= 2000") == ["demo-1", "demo-2"]
    assert _filtered_ids(client, "references.year < 2000") == ["demo-2"]


def test_related_property_is_known_matches() -> None:
    client = make_auto_client()
    # Every reference has a doi; entries WITH some related reference match,
    # the relationship-less demo-3 does not.
    assert _filtered_ids(client, "references.doi IS KNOWN") == ["demo-1", "demo-2"]


def test_related_property_has_on_list_matches() -> None:
    client = make_auto_client()
    assert _filtered_ids(client, 'references.keywords HAS "alpha"') == ["demo-1"]
    assert _filtered_ids(client, 'references.keywords HAS "beta"') == ["demo-2"]


def test_related_id_non_has_comparison_matches() -> None:
    # Non-HAS filtering on <type>.id routes through the semi-join uniformly.
    client = make_auto_client()
    assert _filtered_ids(client, 'references.id != "ref-1"') == ["demo-2"]
    assert _filtered_ids(client, 'references.id = "ref-1"') == ["demo-1"]


def test_related_property_not_composition() -> None:
    # NOT over the semi-join: entries where NO related reference's doi contains
    # "10.1" — including the relationship-less demo-3.
    client = make_auto_client()
    assert _filtered_ids(client, 'NOT (references.doi CONTAINS "10.1")') == ["demo-3"]


def test_related_property_empty_match_and_complement() -> None:
    client = make_auto_client()
    # No reference matches: the semi-join yields a constant-false expression.
    assert _filtered_ids(client, 'references.doi CONTAINS "nomatch"') == []
    # Its complement is ALL entries, including the relationship-less demo-3.
    assert _filtered_ids(client, 'NOT (references.doi CONTAINS "nomatch")') == ["demo-1", "demo-2", "demo-3"]


def test_related_property_per_node_independence() -> None:
    # Documented semantics: each dotted node is resolved independently. demo-2's
    # ref-2 matches only the doi conjunct and its ref-3 matches only the year
    # conjunct, yet demo-2 matches the conjunction (some related reference per
    # conjunct, not one reference matching both).
    client = make_auto_client()
    assert _filtered_ids(client, 'references.doi CONTAINS "10.9" AND references.year >= 2000') == ["demo-2"]
    # Sanity: no single reference matches both conjuncts.
    assert _filtered_ids(client, 'references.doi CONTAINS "10.9"') == ["demo-2"]
    assert _filtered_ids(client, "references.year >= 2000") == ["demo-1", "demo-2"]


def test_not_relationship_id_has_matches_relationship_less_entry() -> None:
    # demo-3 has an empty synthetic __rel_references field on its row, so the
    # inverted set membership is well-defined and matches it.
    client = make_auto_client()
    assert _filtered_ids(client, 'NOT (references.id HAS "ref-1")') == ["demo-2", "demo-3"]


# --- remaining 501 floor -------------------------------------------------------


def _assert_filter_501(filter_string: str) -> None:
    client = make_auto_client()
    response = client.get("/structures", params={"filter": filter_string})
    assert response.status_code == 501
    assert response.json()["errors"][0]["status"] == 501


def test_nested_relationship_path_501() -> None:
    _assert_filter_501("references.structures.x = 1")


def test_dotted_length_501() -> None:
    _assert_filter_501("references.keywords LENGTH 2")


def test_relationship_id_has_non_equal_op_501() -> None:
    _assert_filter_501('references.id HAS ALL > "ref-1", > "ref-2"')


def test_resolver_passes_baseurl_for_partial_values() -> None:
    # Included resources with PartialValue attributes used to get relative
    # partial-data links because the resolver dropped the base URL.
    from httk.serve.optimade.backend.partial import PartialDimension, PartialValue

    pv = PartialValue(dimensions=(PartialDimension("dim_x", length=2, sliceable=True),), fetch=lambda s: [1, 2])
    row = ResultRow(values={"id": "ref-1", "type": "references", "title": pv})
    qf = StubQueryFunction({"references": [row]})
    resolver = _make_related_resolver(qf, relationships_schema(), "http://example.org/")
    included = resolver({"references": {"ref-1"}})
    link = included[0]["meta"]["partial_data_links"]["title"][0]["link"]
    assert link.startswith("http://example.org/partial_data/references/ref-1/")


def test_resolver_handles_many_related_ids() -> None:
    # The resolver used to build a linear OR chain, which overflowed the
    # translator's recursion for large id sets; the tree must be balanced.
    from fake_backend import FakeStore

    from httk.serve.optimade.backend import (
        BackendAdapter,
        EntrySource,
        simple_property_handlers,
    )

    schema = relationships_schema()
    entry_info = schema.entry_info["references"]
    adapter = BackendAdapter(
        store=FakeStore(rows_by_target={"ref-table": []}),
        sources={"references": (EntrySource(target="ref-table", fields={}),), "structures": ()},
        field_handlers={"references": simple_property_handlers("references", {}, entry_info)},
        schema=schema,
    )
    resolver = _make_related_resolver(adapter.query_function(), schema, "http://x/")
    included = resolver({"references": {f"ref-{i}" for i in range(3000)}})
    assert included == []
