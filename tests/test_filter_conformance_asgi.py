"""OPTIMADE filter-error conformance at the HTTP boundary (status codes per the specification)."""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar

import pytest
from httk.atomistic import StructureEntry, StructureEntryProvider, UnitcellStructureRecord
from httk.core import (
    EntryProvider,
    EntryTypeDefinition,
    PropertyDefinition,
    load_entry_type_definition,
    standard_entry_type,
)
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, StoredPropertyProjection, Unique
from httk.store import Backend, EntryIdScheme, SqlStore
from httk.store.backend.sql import StoredEntrySource
from starlette.testclient import TestClient
from test_structure_providers_asgi import _entries

from httk.serve.optimade import adapter_from_providers, adapter_from_stores, create_asgi_app

CALCULATIONS_DEFINITION = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/calculations"


@pytest.fixture(params=("in-memory", "stored"))
def structures(request) -> Iterator[TestClient]:
    if request.param == "in-memory":
        adapter = adapter_from_providers([StructureEntryProvider(_entries())])
        with TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver") as client:
            yield client
        return
    with Backend.sqlite() as database:
        store = SqlStore(
            database, entry_records={StructureEntry: UnitcellStructureRecord}, entry_ids=EntryIdScheme("httk.test", "1")
        )
        for structure in _entries().values():
            store.save(structure)
        adapter = adapter_from_stores([StoredEntrySource(store, StructureEntry, "conformance")])
        with TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver") as client:
            yield client


def _detail(response: Any) -> str:
    return response.json()["errors"][0]["detail"]


@pytest.mark.parametrize("empty", ["", "   "])
def test_empty_filter_is_no_filter(structures, empty: str) -> None:
    unfiltered = structures.get("/v1/structures")
    response = structures.get("/v1/structures", params={"filter": empty})

    assert response.status_code == 200, response.text
    assert [item["id"] for item in response.json()["data"]] == [item["id"] for item in unfiltered.json()["data"]]


@pytest.mark.parametrize("filter_string", ["nelements=true", "true", "NOT false"])
def test_lowercase_boolean_is_an_unknown_property_with_a_hint(structures, filter_string: str) -> None:
    response = structures.get("/v1/structures", params={"filter": filter_string})

    assert response.status_code == 400, response.text
    assert "unrecognized property name: " + filter_string.split()[-1].split("=")[-1] in _detail(response)
    assert "uppercase TRUE or FALSE" in _detail(response)


@pytest.mark.parametrize(
    "filter_string",
    [
        "nelements=TRUE",
        "nelements HAS 1",
        'chemical_formula_descriptive HAS "Na"',
        "nelements LENGTH 1",
        'elements = "Na"',
        'elements CONTAINS "Na"',
    ],
)
def test_type_mismatch_is_not_implemented(structures, filter_string: str) -> None:
    # OPTIMADE: "Different types of values MUST be reported as 501 Not Implemented".
    response = structures.get("/v1/structures", params={"filter": filter_string})

    assert response.status_code == 501, response.text
    assert "Type mismatch" in _detail(response)


def test_non_rfc3339_timestamp_is_bad_request(structures) -> None:
    # OPTIMADE: timestamp interpretation failures MUST be reported as 400.
    response = structures.get("/v1/structures", params={"filter": 'last_modified > "yesterday"'})

    assert response.status_code == 400, response.text


@pytest.mark.parametrize(
    "filter_string", ["bananas=3", "bananas IS KNOWN", 'bananas HAS "x"', "bananas LENGTH 1", "nelements=bananas"]
)
def test_unknown_unprefixed_property_is_bad_request(structures, filter_string: str) -> None:
    # OPTIMADE: an unknown property without a database-specific prefix MUST be a 400 naming it.
    response = structures.get("/v1/structures", params={"filter": filter_string})

    assert response.status_code == 400, response.text
    assert "bananas" in _detail(response)


def test_unknown_foreign_prefixed_property_matches_nothing(structures) -> None:
    response = structures.get("/v1/structures", params={"filter": "_exmpl_x = 3"})

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["data_returned"] == 0


class _TitlesOnly(EntryProvider):
    """A references provider serving only ``title`` of the standard references properties."""

    def entry_types(self) -> dict[str, EntryTypeDefinition]:
        return {"references": standard_entry_type("references")}

    def property_keys(self, entry_type: str) -> dict[str, str]:
        return {"id": "__id", "type": "type", "title": "title"}

    def records(self, entry_type: str) -> list[dict[str, Any]]:
        return [{"__id": "r-1", "type": "references", "title": "T"}]


@pytest.mark.parametrize(
    ("filter_string", "status", "returned"),
    [('doi = "x"', 200, 0), ("year > 2000", 200, 0), ('title = "T"', 200, 1), ("bananas = 1", 400, None)],
)
def test_unserved_standard_property_matches_nothing(filter_string: str, status: int, returned: int | None) -> None:
    # A standard references property this deployment does not serve is unknown-valued, not an error.
    app = create_asgi_app(adapter_from_providers([_TitlesOnly()]), baseurl="http://testserver")
    with TestClient(app, base_url="http://testserver") as client:
        assert "doi" not in client.get("/v1/info/references").json()["data"]["properties"]
        response = client.get("/v1/references", params={"filter": filter_string})

    assert response.status_code == status, response.text
    assert response.json()["meta"].get("data_returned") == returned


@pytest.mark.parametrize("filter_string", ["nelements", "NOT nelements AND nsites = 1"])
def test_boolean_property_shorthand_is_not_implemented(structures, filter_string: str) -> None:
    # OPTIMADE: an unsupported OPTIONAL construct MUST be a 501 naming the construct.
    response = structures.get("/v1/structures", params={"filter": filter_string})

    assert response.status_code == 501, response.text
    assert "boolean property shorthand" in _detail(response)


def test_standard_is_known_filters(structures) -> None:
    response = structures.get("/v1/structures", params={"filter": "nelements IS KNOWN"})

    assert response.status_code == 200, response.text
    assert response.json()["meta"]["data_returned"] == 2


# --- An application projection that forwards every operator to compare() ---


def _forward_everything(context: Any, operator: str, literal: object) -> Any:
    return context.compare(context.field("prop"), operator, context.constant(literal))


class ExampleCalculation:
    type = "calculations"
    definition_id = CALCULATIONS_DEFINITION

    @staticmethod
    def entry_type_definition() -> EntryTypeDefinition:
        return load_entry_type_definition(CALCULATIONS_DEFINITION).extended(
            {
                "_httk_exmpl_prop": PropertyDefinition.from_simple(
                    "_httk_exmpl_prop", description="An application property."
                )
            }
        )


@dataclass(frozen=True)
class ExampleRecord:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="serve_filter_conformance_example")

    prop: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)

    __httk_stored_properties__: ClassVar[Any] = {
        "_httk_exmpl_prop": StoredPropertyProjection(response=lambda record: record.prop, query=_forward_everything),
    }


register_entry_family(
    name="test-filter-conformance-calculations",
    family=f"{__name__}:ExampleCalculation",
    definition_id=CALCULATIONS_DEFINITION,
)
register_entry_record(
    name="test-filter-conformance-example",
    family="test-filter-conformance-calculations",
    record=f"{__name__}:ExampleRecord",
)


@pytest.mark.parametrize("filter_string", ["_httk_exmpl_prop IS KNOWN", "_httk_exmpl_prop IS UNKNOWN"])
def test_forwarding_projection_is_known_is_not_implemented(filter_string: str) -> None:
    with Backend.sqlite() as database:
        store = SqlStore(
            database, entry_records={ExampleCalculation: ExampleRecord}, entry_ids=EntryIdScheme("httk.test", "1")
        )
        store.save(ExampleRecord("x"))
        adapter = adapter_from_stores((StoredEntrySource(store, ExampleCalculation, "example"),))
        with TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver") as client:
            assert client.get("/v1/calculations", params={"filter": '_httk_exmpl_prop = "x"'}).status_code == 200
            response = client.get("/v1/calculations", params={"filter": filter_string})

    assert response.status_code == 501, response.text
    assert filter_string.split()[-1] in _detail(response)
    assert "StoredPropertyProjection" in _detail(response)
