from dataclasses import dataclass, field
from typing import Any

from fake_backend import FakeStore
from starlette.testclient import TestClient

from httk.optimade import BackendAdapter, EntrySource, RawRequest, create_asgi_app
from httk.optimade.backend import (
    simple_property_handlers,
    translate_filter,
)
from httk.optimade.backend.handlers import set_handler
from httk.optimade.endpoints import generate_info_endpoint_reply
from httk.optimade.filter import parse_optimade_filter
from httk.optimade.model import OptimadeConfig, ValidatedParameters, ValidatedRequest
from httk.optimade.schema.served import ServedSchema, build_served_schema

# The full set of files properties, used for the schema/definition tests.
FILE_PROPERTIES = [
    'id',
    'type',
    'immutable_id',
    'last_modified',
    'url',
    'url_stable_until',
    'name',
    'size',
    'media_type',
    'version',
    'modification_timestamp',
    'description',
    'checksums',
    'atime',
    'ctime',
    'mtime',
]

# The subset actually served (with extractors) by the e2e demo adapter.
E2E_FILE_PROPERTIES = [
    'id',
    'type',
    'url',
    'name',
    'size',
    'media_type',
    'version',
    'description',
    'checksums',
]

FILE_DEFAULT_OVERRIDES = {
    'files': ['url', 'name', 'size', 'media_type', 'description'],
}

FILE_COLUMNS = {
    'url': 'url',
    'name': 'name',
    'media_type': 'media_type',
}

FILE_FIELDS: dict[str, Any] = {
    'type': lambda x: "files",
    'id': lambda x: x['__id'],
    'url': lambda x: x['url'],
    'name': lambda x: x['name'],
    'size': lambda x: x['size'],
    'media_type': lambda x: x['media_type'],
    'description': lambda x: x['description'],
    'checksums': lambda x: x['checksums'],
}

FILES = [
    {
        '__id': 'file-1',
        'url': 'https://example.org/files/calc-1/INCAR',
        'name': 'INCAR',
        'size': 512,
        'media_type': 'text/plain',
        'description': 'Input settings file',
        'checksums': {'sha256': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'},
    },
    {
        '__id': 'file-2',
        'url': 'https://example.org/files/calc-1/OUTCAR',
        'name': 'OUTCAR',
        'size': 204800,
        'media_type': 'text/plain',
        'description': 'Output log file',
        'checksums': {'sha256': '9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08'},
    },
]


def files_schema() -> ServedSchema:
    return build_served_schema(
        {'files': FILE_PROPERTIES},
        default_response_overrides=FILE_DEFAULT_OVERRIDES,
    )


def files_e2e_schema() -> ServedSchema:
    return build_served_schema(
        {'files': E2E_FILE_PROPERTIES},
        default_response_overrides=FILE_DEFAULT_OVERRIDES,
    )


def files_adapter(store: FakeStore) -> BackendAdapter:
    schema = files_e2e_schema()
    field_handlers = {'files': simple_property_handlers('files', FILE_COLUMNS, schema.entry_info['files'])}
    return BackendAdapter(
        store=store,
        sources={'files': (EntrySource(target='files', fields=FILE_FIELDS),)},
        field_handlers=field_handlers,
        schema=schema,
    )


def make_request(representation: str) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation)


def make_config() -> OptimadeConfig:
    config = OptimadeConfig()
    config.data_available = {"files": len(FILES)}
    return config


def make_files_client() -> TestClient:
    store = FakeStore(rows_by_target={'files': list(FILES)})
    adapter = files_adapter(store)
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


# --- Schema / info ------------------------------------------------------------


def test_files_schema_properties_present() -> None:
    schema = files_schema()
    properties = schema.entry_info['files']['properties']
    for name in (
        'id',
        'type',
        'url',
        'url_stable_until',
        'name',
        'size',
        'media_type',
        'version',
        'modification_timestamp',
        'description',
        'checksums',
        'atime',
        'ctime',
        'mtime',
    ):
        assert name in properties


def test_only_url_required_in_response_besides_id_and_type() -> None:
    properties = build_served_schema({'files': FILE_PROPERTIES}).entry_info['files']['properties']
    required = {name for name, info in properties.items() if info.get('required_response')}
    assert required == {'id', 'type', 'url'}


def test_info_lists_files_endpoint() -> None:
    schema = files_schema()
    request = ValidatedRequest(
        baseurl="http://localhost/",
        representation="/info",
        endpoint="info",
        version="1.3.0",
        query=ValidatedParameters(),
    )
    reply = generate_info_endpoint_reply(request, make_config(), schema)
    assert "files" in reply["data"]["attributes"]["available_endpoints"]
    assert reply["data"]["attributes"]["entry_types_by_format"]["json"] == ["files"]


# --- Property definitions -----------------------------------------------------


def test_url_definition_is_required_non_null_string() -> None:
    definitions = files_schema().property_definitions["files"]
    url = definitions["url"]
    assert url["x-optimade-type"] == "string"
    assert url["type"] == ["string"]


def test_timestamp_definition_has_date_time_format() -> None:
    definitions = files_schema().property_definitions["files"]
    stable = definitions["url_stable_until"]
    assert stable["x-optimade-type"] == "timestamp"
    assert stable["format"] == "date-time"
    # timestamp maps to a JSON string type, nullable since it is optional:
    assert stable["type"] == ["string", "null"]


def test_checksums_definition_is_object_with_inner_string_properties() -> None:
    definitions = files_schema().property_definitions["files"]
    checksums = definitions["checksums"]
    assert checksums["x-optimade-type"] == "dictionary"
    assert checksums["type"] == ["object", "null"]
    properties = checksums["properties"]
    assert set(properties) == {"md5", "sha1", "sha224", "sha256", "sha384", "sha512"}
    for inner in properties.values():
        assert inner["x-optimade-type"] == "string"


# --- Filtering ----------------------------------------------------------------


def test_url_filter_translates_to_string_comparison() -> None:
    store = FakeStore(rows_by_target={'files': list(FILES)})
    adapter = files_adapter(store)
    pairs = translate_filter(
        parse_optimade_filter('url = "https://example.org/files/calc-1/INCAR"'), ["files"], adapter
    )
    _source, searcher = pairs[0]
    assert searcher.expressions[0].tree == (  # type: ignore[attr-defined]
        "eq",
        ("column", "url"),
        "https://example.org/files/calc-1/INCAR",
    )


# --- ASGI end to end ----------------------------------------------------------


def test_asgi_files_listing_shows_url_and_name() -> None:
    client = make_files_client()
    response = client.get("/files")
    assert response.status_code == 200
    payload = response.json()
    assert {d["id"] for d in payload["data"]} == {"file-1", "file-2"}
    file1 = next(d for d in payload["data"] if d["id"] == "file-1")
    assert file1["type"] == "files"
    assert file1["attributes"]["url"] == "https://example.org/files/calc-1/INCAR"
    assert file1["attributes"]["name"] == "INCAR"


def test_asgi_info_files_works() -> None:
    client = make_files_client()
    response = client.get("/info/files")
    assert response.status_code == 200
    properties = response.json()["data"]["properties"]
    assert "url" in properties
    assert "checksums" in properties


def test_asgi_files_url_filter_200() -> None:
    client = make_files_client()
    response = client.get("/files", params={"filter": 'url = "https://example.org/files/calc-1/INCAR"'})
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


# --- calculations <-> files relationships -------------------------------------


@dataclass
class CalcRow:
    sid: str
    files: list[tuple[str, str]] = field(default_factory=list)
    file_ids: list[str] = field(default_factory=list)


@dataclass
class FileRow:
    sid: str
    url: str
    name: str


CALC_PROPERTIES = ['id', 'type', '_httk_total_energy']

CALC_FIELDS: dict[str, Any] = {
    "type": lambda x: "calculations",
    "id": lambda x: x.sid,
    "_httk_total_energy": lambda x: -1.0,
}

REL_FILE_FIELDS: dict[str, Any] = {
    "type": lambda x: "files",
    "id": lambda x: x.sid,
    "url": lambda x: x.url,
    "name": lambda x: x.name,
}


def _calc_relationships(row: CalcRow) -> dict[str, list[dict[str, Any]]]:
    if not row.files:
        return {}
    return {"files": [{"id": fid, "role": role} for fid, role in row.files]}


def calc_files_schema() -> ServedSchema:
    return build_served_schema(
        {
            'calculations': CALC_PROPERTIES,
            'files': ['id', 'type', 'url', 'name'],
        },
        default_response_overrides={
            'calculations': ['_httk_total_energy'],
            'files': ['url', 'name'],
        },
    )


def make_calc_files_client() -> TestClient:
    store = FakeStore(
        rows_by_target={
            "calc-table": [
                CalcRow(sid="calc-1", files=[("file-1", "input"), ("file-2", "output")], file_ids=["file-1", "file-2"])
            ],
            "file-table": [
                FileRow(sid="file-1", url="https://x/INCAR", name="INCAR"),
                FileRow(sid="file-2", url="https://x/OUTCAR", name="OUTCAR"),
            ],
        }
    )
    schema = calc_files_schema()
    field_handlers = {
        'files': simple_property_handlers('files', FILE_COLUMNS, schema.entry_info['files']),
        'calculations': simple_property_handlers('calculations', {}, schema.entry_info['calculations']),
    }
    calc_handlers = dict(field_handlers['calculations'])
    calc_handlers['files.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('file_ids', ops, values, inv, has_type, sv),
    }
    field_handlers['calculations'] = calc_handlers
    adapter = BackendAdapter(
        store=store,
        sources={
            "calculations": (EntrySource(target="calc-table", fields=CALC_FIELDS, relationships=_calc_relationships),),
            "files": (EntrySource(target="file-table", fields=REL_FILE_FIELDS),),
        },
        field_handlers=field_handlers,
        schema=schema,
    )
    app = create_asgi_app(adapter, baseurl="http://testserver/")
    return TestClient(app, base_url="http://testserver")


def test_asgi_calculation_shows_files_relationships_with_roles() -> None:
    client = make_calc_files_client()
    response = client.get("/calculations/calc-1")
    assert response.status_code == 200
    rels = response.json()["data"]["relationships"]["files"]["data"]
    by_id = {r["id"]: r for r in rels}
    assert by_id["file-1"]["meta"]["role"] == "input"
    assert by_id["file-2"]["meta"]["role"] == "output"
    assert by_id["file-1"]["type"] == "files"


def test_asgi_calculation_include_files_returns_included_resources() -> None:
    client = make_calc_files_client()
    response = client.get("/calculations/calc-1", params={"include": "files"})
    assert response.status_code == 200
    payload = response.json()
    included = {obj["id"]: obj for obj in payload["included"]}
    assert set(included) == {"file-1", "file-2"}
    assert included["file-1"]["type"] == "files"
    assert included["file-1"]["attributes"]["name"] == "INCAR"


def test_asgi_calculations_filter_files_id_has_200() -> None:
    client = make_calc_files_client()
    response = client.get("/calculations", params={"filter": 'files.id HAS "file-1"'})
    assert response.status_code == 200
    assert isinstance(response.json()["data"], list)


def test_files_id_has_translates_to_set_handler_tree() -> None:
    store = FakeStore(rows_by_target={"calc-table": []})
    schema = calc_files_schema()
    field_handlers = {'calculations': simple_property_handlers('calculations', {}, schema.entry_info['calculations'])}
    calc_handlers = dict(field_handlers['calculations'])
    calc_handlers['files.id'] = {
        'HAS': lambda entry, ops, values, sv, has_type, inv: set_handler('file_ids', ops, values, inv, has_type, sv),
    }
    field_handlers['calculations'] = calc_handlers
    adapter = BackendAdapter(
        store=store,
        sources={
            "calculations": (EntrySource(target="calc-table", fields=CALC_FIELDS),),
            "files": (EntrySource(target="file-table", fields=REL_FILE_FIELDS),),
        },
        field_handlers=field_handlers,
        schema=schema,
    )
    pairs = translate_filter(parse_optimade_filter('files.id HAS "file-1"'), ["calculations"], adapter)
    _source, searcher = pairs[0]
    assert searcher.expressions[0].tree == ("has_any", ("column", "file_ids"), ("file-1",))  # type: ignore[attr-defined]
