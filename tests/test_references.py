from typing import Any

from fake_backend import FakeStore
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, RawRequest, create_asgi_app
from httk.optimade.backend import (
    simple_property_handlers,
    translate_filter,
)
from httk.optimade.endpoints import generate_info_endpoint_reply
from httk.optimade.engine.validate import validate_optimade_request
from httk.optimade.filter import parse_optimade_filter
from httk.optimade.model import OptimadeConfig, ValidatedParameters, ValidatedRequest
from httk.optimade.schema.served import ServedSchema, build_served_schema

REFERENCE_PROPERTIES = [
    'id',
    'type',
    'title',
    'journal',
    'year',
    'doi',
    'authors',
    'url',
    'bib_type',
]

REFERENCE_DEFAULT_OVERRIDES = {
    'references': ['title', 'journal', 'year', 'doi', 'authors'],
}

REFERENCE_COLUMNS = {
    'doi': 'doi',
    'year': 'year',
    'title': 'title',
    'journal': 'journal',
}

REFERENCE_FIELDS: dict[str, Any] = {
    'type': lambda x: "references",
    'id': lambda x: x['__id'],
    'title': lambda x: x['title'],
    'journal': lambda x: x['journal'],
    'year': lambda x: x['year'],
    'doi': lambda x: x['doi'],
    'authors': lambda x: x['authors'],
}

REFERENCES = [
    {
        '__id': 'ref-1',
        'title': 'A study of gallium titanium compounds',
        'journal': 'Journal of Demo Materials',
        'year': '2021',
        'doi': '10.1234/demo.2021.1',
        'authors': [{'name': 'Ada Lovelace'}, {'name': 'Alan Turing'}],
    },
    {
        '__id': 'ref-2',
        'title': 'Silicon dioxide polymorphs revisited',
        'journal': 'Demo Letters',
        'year': '2019',
        'doi': '10.1234/demo.2019.7',
        'authors': [{'name': 'Grace Hopper'}],
    },
]


def references_schema() -> ServedSchema:
    return build_served_schema(
        {'references': REFERENCE_PROPERTIES},
        default_response_overrides=REFERENCE_DEFAULT_OVERRIDES,
    )


def references_adapter(store: FakeStore) -> BackendAdapter:
    schema = references_schema()
    field_handlers = {
        'references': simple_property_handlers('references', REFERENCE_COLUMNS, schema.entry_info['references'])
    }
    return BackendAdapter(
        store=store,
        sources={'references': (EntrySource(target='references', fields=REFERENCE_FIELDS),)},
        field_handlers=field_handlers,
        schema=schema,
    )


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


def make_config() -> OptimadeConfig:
    config = OptimadeConfig()
    config.data_available = {"references": len(REFERENCES)}
    return config


def make_client() -> TestClient:
    store = FakeStore(rows_by_target={'references': list(REFERENCES)})
    adapter = references_adapter(store)
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


# --- Schema / info ------------------------------------------------------------


def test_info_lists_references_endpoint() -> None:
    schema = references_schema()
    request = ValidatedRequest(
        baseurl="http://localhost/",
        representation="/info",
        endpoint="info",
        version="1.3.0",
        query=ValidatedParameters(),
    )
    reply = generate_info_endpoint_reply(request, make_config(), schema)
    assert "references" in reply["data"]["attributes"]["available_endpoints"]
    assert reply["data"]["attributes"]["entry_types_by_format"]["json"] == ["references"]


def test_entry_info_references_property_definitions() -> None:
    schema = references_schema()
    definitions = schema.property_definitions["references"]
    assert "doi" in definitions
    assert "authors" in definitions
    # authors is a list of person objects (dicts):
    assert definitions["authors"]["x-optimade-type"] == "list"
    assert definitions["authors"]["items"]["x-optimade-type"] == "dictionary"


# --- Filtering ----------------------------------------------------------------


def test_doi_filter_translates_to_string_comparison() -> None:
    store = FakeStore(rows_by_target={'references': list(REFERENCES)})
    adapter = references_adapter(store)
    pairs = translate_filter(parse_optimade_filter('doi = "10.1234/demo.2021.1"'), ["references"], adapter)
    _source, searcher = pairs[0]
    assert searcher.expressions[0].tree == ("eq", ("column", "doi"), "10.1234/demo.2021.1")  # type: ignore[attr-defined]


# --- ASGI end to end ----------------------------------------------------------


def test_asgi_references_listing_has_authors() -> None:
    client = make_client()
    response = client.get("/references")
    assert response.status_code == 200
    payload = response.json()
    assert {d["id"] for d in payload["data"]} == {"ref-1", "ref-2"}
    ref1 = next(d for d in payload["data"] if d["id"] == "ref-1")
    assert ref1["type"] == "references"
    assert ref1["attributes"]["authors"] == [{"name": "Ada Lovelace"}, {"name": "Alan Turing"}]
    assert ref1["attributes"]["doi"] == "10.1234/demo.2021.1"


def test_asgi_references_doi_filter_200() -> None:
    client = make_client()
    response = client.get("/references", params={"filter": 'doi = "10.1234/demo.2021.1"'})
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_unserved_spec_property_null_filled() -> None:
    # 'publisher' is a spec references property that is not served here; it must
    # be accepted as a response field and null-filled in the response.
    schema = references_schema()
    validated = validate_optimade_request(make_request("/references?response_fields=publisher"), "1.3.0", schema)
    assert "publisher" in validated.unrecognized_response_fields

    client = make_client()
    response = client.get("/references", params={"response_fields": "publisher"})
    assert response.status_code == 200
    attributes = response.json()["data"][0]["attributes"]
    assert attributes["publisher"] is None
