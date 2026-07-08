from dataclasses import dataclass, field
from typing import Any, Iterator

import pytest
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, OptimadeError, RawRequest, create_asgi_app
from httk.optimade.backend import default_field_handlers, simple_property_handlers, translate_filter
from httk.optimade.backend.handlers import set_handler
from httk.optimade.endpoints.entries import generate_entry_endpoint_reply
from httk.optimade.engine import process
from httk.optimade.engine.process import _make_related_resolver
from httk.optimade.engine.validate import validate_optimade_request
from httk.optimade.filter import parse_optimade_filter
from httk.optimade.model import (
    OptimadeConfig,
    ResultRow,
    TranslatorError,
    ValidatedParameters,
    ValidatedRequest,
)
from httk.optimade.schema.served import ServedSchema, build_served_schema

from fake_backend import FakeStore

REFERENCE_COLUMNS = {'doi': 'doi', 'title': 'title'}


def relationships_schema() -> ServedSchema:
    return build_served_schema(
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
    schema = build_served_schema({"structures": ["id", "type"]})
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


# --- relationship filtering ---------------------------------------------------


def _structures_rel_adapter(store: FakeStore) -> BackendAdapter:
    schema = relationships_schema()
    field_handlers = default_field_handlers()
    field_handlers['references'] = simple_property_handlers(
        'references', REFERENCE_COLUMNS, schema.entry_info['references']
    )
    structures_handlers = dict(field_handlers['structures'])
    structures_handlers['references.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('references', ops, values, inv, has_type, sv),
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
    (_source, searcher) = pairs[0]
    assert searcher.expressions[0].tree == ("has_any", ("column", "references"), ("ref-1",))  # type: ignore[attr-defined]


def test_relationship_target_filter_501() -> None:
    adapter = _structures_rel_adapter(FakeStore(rows_by_target={"structure-table": []}))
    with pytest.raises(TranslatorError) as excinfo:
        translate_filter(parse_optimade_filter('references.target.doi = "10.1/x"'), ["structures"], adapter)
    assert excinfo.value.response_code == 501


# --- ASGI end to end ----------------------------------------------------------


@dataclass
class StructRow:
    sid: str
    references: list[str] = field(default_factory=list)


@dataclass
class RefRow:
    sid: str
    title: str


STRUCT_FIELDS: dict[str, Any] = {
    "type": lambda x: "structures",
    "id": lambda x: x.sid,
    "nelements": lambda x: 2,
}

REF_FIELDS: dict[str, Any] = {
    "type": lambda x: "references",
    "id": lambda x: x.sid,
    "title": lambda x: x.title,
    "doi": lambda x: "10.1/" + x.sid,
}


def _structure_relationships(row: StructRow) -> dict[str, list[dict[str, Any]]]:
    if not row.references:
        return {}
    return {"references": [{"id": r, "description": "Reference for this structure"} for r in row.references]}


def make_relationships_client() -> TestClient:
    store = FakeStore(
        rows_by_target={
            "structure-table": [StructRow(sid="demo-1", references=["ref-1"])],
            "reference-table": [RefRow(sid="ref-1", title="T"), RefRow(sid="ref-2", title="U")],
        }
    )
    schema = relationships_schema()
    field_handlers = default_field_handlers()
    field_handlers['references'] = simple_property_handlers(
        'references', REFERENCE_COLUMNS, schema.entry_info['references']
    )
    structures_handlers = dict(field_handlers['structures'])
    structures_handlers['references.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('references', ops, values, inv, has_type, sv),
    }
    field_handlers['structures'] = structures_handlers
    adapter = BackendAdapter(
        store=store,
        sources={
            "structures": (
                EntrySource(target="structure-table", fields=STRUCT_FIELDS, relationships=_structure_relationships),
            ),
            "references": (EntrySource(target="reference-table", fields=REF_FIELDS),),
        },
        field_handlers=field_handlers,
        schema=schema,
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_asgi_single_structure_include_references() -> None:
    client = make_relationships_client()
    response = client.get("/structures/demo-1", params={"include": "references"})
    assert response.status_code == 200
    payload = response.json()
    rels = payload["data"]["relationships"]["references"]["data"]
    assert rels[0]["id"] == "ref-1"
    assert rels[0]["meta"]["description"] == "Reference for this structure"
    assert payload["included"][0]["type"] == "references"
    assert payload["included"][0]["id"] == "ref-1"


def test_asgi_include_bogus_400() -> None:
    client = make_relationships_client()
    response = client.get("/structures/demo-1", params={"include": "bogus"})
    assert response.status_code == 400
    assert response.json()["errors"][0]["status"] == 400


def test_resolver_passes_baseurl_for_partial_values() -> None:
    # Included resources with PartialValue attributes used to get relative
    # partial-data links because the resolver dropped the base URL.
    from httk.optimade.backend.partial import PartialDimension, PartialValue

    pv = PartialValue(dimensions=(PartialDimension("dim_x", length=2, sliceable=True),), fetch=lambda s: [1, 2])
    row = ResultRow(values={"id": "ref-1", "type": "references", "title": pv})
    qf = StubQueryFunction({"references": [row]})
    resolver = _make_related_resolver(qf, relationships_schema(), "http://example.org/")
    included = resolver({"references": {"ref-1"}})
    link = included[0]["meta"]["partial_data_links"]["title"][0]["link"]
    assert link.startswith("http://example.org/partial_data/references/ref-1/")
