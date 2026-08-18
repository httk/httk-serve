"""Queryability is enforced at the serve engine choke point.

Honoring ``x-optimade-requirements.query-support: "none"`` and rejecting
adapter-hidden (schema-absent but prefixed) properties is a protocol-boundary
policy applied to the parsed client filter in
:func:`~httk.serve.optimade.engine.processing._reject_hidden_filter_properties`,
before any backend/adapter rewriting. The neutral store and the backends stay
permissive for trusted callers, so these tests drive the full engine through a
``TestClient``. Two entry types (``gadgets``, ``sprockets``) exercise both plain
and depth-1 relationship identifiers.
"""

from dataclasses import dataclass
from typing import Any, ClassVar

import pytest
from fake_backend import FakeStore
from httk.core import EntryTypeDefinition, PropertyDefinition, load_entry_type_definition
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import StorageInfo, StoredPropertyProjection
from httk.store import Backend, SqlStore
from httk.store.backend.sql import StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_stores, create_asgi_app
from httk.serve.optimade.backend import BackendAdapter, EntrySource
from httk.serve.optimade.schema.served import build_served_schema

CALCULATIONS_DEFINITION = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/calculations"


def _query_support_none(prop: PropertyDefinition) -> PropertyDefinition:
    """Return a copy of ``prop`` declaring ``query-support: "none"``."""
    doc = dict(prop.as_optimade())
    doc["x-optimade-requirements"] = {"query-support": "none"}
    return PropertyDefinition.from_optimade(prop.name, doc)


# --- In-memory path (engine flag over the served schema) -------------------


def _properties(*specs: tuple[str, bool]) -> dict[str, PropertyDefinition]:
    props = {
        "id": PropertyDefinition.from_simple("id", description="The id.", required_response=True),
        "type": PropertyDefinition.from_simple("type", description="The entry type.", required_response=True),
    }
    for name, hidden in specs:
        base = PropertyDefinition.from_simple(name, description=name, fulltype="integer")
        props[name] = _query_support_none(base) if hidden else base
    return props


def _inmemory_client() -> TestClient:
    gadgets = EntryTypeDefinition(
        "gadgets", "A gadgets entry.", _properties(("_httk_visible", False), ("_httk_secret", True))
    )
    sprockets = EntryTypeDefinition("sprockets", "A sprockets entry.", _properties(("_httk_hidden", True)))
    schema = build_served_schema(
        {"gadgets": gadgets, "sprockets": sprockets},
        {"gadgets": tuple(gadgets.properties), "sprockets": tuple(sprockets.properties)},
    )
    adapter = BackendAdapter(
        store=FakeStore(),
        sources={
            "gadgets": (
                EntrySource(target="gadget-table", fields={"id": lambda _r: "", "type": lambda _r: "gadgets"}),
            ),
            "sprockets": (
                EntrySource(target="sprocket-table", fields={"id": lambda _r: "", "type": lambda _r: "sprockets"}),
            ),
        },
        schema=schema,
    )
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


def test_in_memory_queryable_property_accepted() -> None:
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": "_httk_visible = 1"})
    assert response.status_code == 200


def test_in_memory_non_queryable_property_rejected() -> None:
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": "_httk_secret = 1"})
    assert response.status_code == 400
    assert int(response.json()["errors"][0]["status"]) == 400


def test_in_memory_hidden_prefixed_property_rejected() -> None:
    # A prefixed property absent from the served schema (an adapter-hidden
    # projection) is rejected at the boundary.
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": '_httk_nonexistent = 1'})
    assert response.status_code == 400


def test_relationship_dotted_non_queryable_property_rejected() -> None:
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": "sprockets._httk_hidden = 1"})
    assert response.status_code == 400


# A non-relationship dotted head (its trailing segments are silently ignored by
# the translator) must not smuggle a hidden/non-queryable head past the walker.
@pytest.mark.parametrize(
    "filter_string",
    (
        '_httk_secret.x = 1',
        '_httk_secret.x.y = 1',
        '_httk_secret.x.y.z = 1',
        '_httk_secret.x CONTAINS "1"',
        '_httk_nonexistent.x = 1',
        '_httk_nonexistent.x.y = 1',
        '_httk_nonexistent.x.y.z = 1',
    ),
)
def test_in_memory_dotted_suffix_bypass_is_rejected(filter_string: str) -> None:
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": filter_string})
    assert response.status_code == 400


def test_in_memory_legit_relationship_filter_accepted() -> None:
    # A real depth-1 relationship filter on a queryable related property is not a
    # false positive: head is a served type, trailing `id` is queryable.
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": 'sprockets.id = "sprockets-1"'})
    assert response.status_code == 200


def test_unprefixed_unknown_property_is_not_rejected() -> None:
    # Unprefixed unknown names keep the translator's warn/unknown semantics (match
    # nothing), not an engine-level 400.
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"filter": "bogus = 1"})
    assert response.status_code == 200


def test_non_queryable_property_stays_unsortable() -> None:
    with _inmemory_client() as client:
        response = client.get("/gadgets", params={"sort": "_httk_secret"})
    assert response.status_code == 400


# --- Stored (SQL-backed) path ---------------------------------------------


def _string_query(field: str):
    return lambda context, operator, literal: context.compare(context.field(field), operator, context.constant(literal))


class SecretCalculation:
    type = "calculations"
    definition_id = CALCULATIONS_DEFINITION

    @staticmethod
    def entry_type_definition() -> EntryTypeDefinition:
        return load_entry_type_definition(CALCULATIONS_DEFINITION).extended(
            {
                "_httk_public": PropertyDefinition.from_simple(
                    "_httk_public", description="A queryable stored property."
                ),
                "_httk_secret": _query_support_none(
                    PropertyDefinition.from_simple("_httk_secret", description="A non-queryable stored property.")
                ),
            }
        )


@dataclass(frozen=True)
class SecretRecord:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="serve_queryability_secret")

    public: str
    secret: str

    __httk_stored_properties__: ClassVar[Any] = {
        "_httk_public": StoredPropertyProjection(response=lambda record: record.public, query=_string_query("public")),
        "_httk_secret": StoredPropertyProjection(response=lambda record: record.secret, query=_string_query("secret")),
    }


register_entry_family(
    name="test-queryability-calculations",
    family=f"{__name__}:SecretCalculation",
    definition_id=CALCULATIONS_DEFINITION,
)
register_entry_record(
    name="test-queryability-secret",
    family="test-queryability-calculations",
    record=f"{__name__}:SecretRecord",
)


def _stored_client(store: SqlStore) -> TestClient:
    store.save(SecretRecord("open", "classified"))
    adapter = adapter_from_stores((StoredEntrySource(store, SecretCalculation, "alpha"),))
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


def test_stored_queryable_property_filters() -> None:
    with Backend.sqlite() as database:
        store = SqlStore(database, entry_records={SecretCalculation: SecretRecord})
        with _stored_client(store) as client:
            response = client.get("/calculations", params={"filter": '_httk_public = "open"'})
            assert response.status_code == 200
            assert len(response.json()["data"]) == 1
