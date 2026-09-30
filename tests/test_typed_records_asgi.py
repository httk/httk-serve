"""ASGI coverage for a ``records`` family that mixes generic and typed backings.

A family without its own ``entry_type_definition()`` serves the union of its
backings' ``__httk_property_definitions__``, so a typed record with a native
float column serves ``_httk_total_energy`` through ``adapter_from_store`` next to
generic ``DataRecord`` rows (which do not project it and count as null).
"""

import datetime
from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from httk.core.data_records import RECORDS_DEFINITION_ID, DataRecord, DataRecordEntry
from httk.core.provenance import RUNS_DEFINITION_ID, Run, RunEdge, RunEntry
from httk.core.register import load_property_definition
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, StoredPropertyProjection, StrongLink, Unique
from httk.store import EntryFamilyDeclaration, EntryIdScheme, EntryRecordDeclaration
from httk.store.backend.sql import Backend, SqlStore
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_store, create_asgi_app

_TOTAL_ENERGY = "https://schemas.httk.org/defs/v0.1/properties/core/total_energy"


def _energy_query(ctx, operator: str, literal: object):
    value = ctx.field("total_energy")
    if operator == "IS_UNKNOWN":
        return ctx.is_null(value)
    if operator == "IS_KNOWN":
        return ctx.not_(ctx.is_null(value))
    return ctx.compare(value, operator, ctx.constant(literal))


@dataclass(frozen=True)
class ServedEnergyRecord:
    """A test-local typed ``records`` backing mirroring the ``DataRecord`` storage contract."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="serve_typed_energy", identity_name="serve_typed_energy"
    )
    __httk_property_definitions__: ClassVar = {
        "_httk_total_energy": load_property_definition(_TOTAL_ENERGY).served_form()
    }
    __httk_stored_properties__: ClassVar = {
        "_httk_total_energy": StoredPropertyProjection(
            response=lambda record: record.total_energy,
            query=_energy_query,
            sort=lambda ctx: ctx.field("total_energy"),
        )
    }

    total_energy: float
    product_of: Annotated[tuple[RunEdge, ...], StrongLink("product_of", reverse="has_product", role="subject")] = ()
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)
    last_modified: Annotated[datetime.datetime | None, IdentitySkip()] = field(default=None, compare=False)

    @property
    def type(self) -> str:
        return "records"


def _declarations() -> tuple[EntryFamilyDeclaration, ...]:
    """Declare the core ``runs`` and ``records`` families explicitly.

    ``ServedEnergyRecord`` is never registered globally (an in-process layout
    built from ``known_entry_records`` would otherwise gain it); the explicit
    declarations keep the core family and record names, so they pass the
    registry-conflict checks.
    """
    return (
        EntryFamilyDeclaration(
            name="runs",
            family=RunEntry,
            definition_id=RUNS_DEFINITION_ID,
            records=(EntryRecordDeclaration(name="core-run", record=Run, definition_id=RUNS_DEFINITION_ID),),
        ),
        EntryFamilyDeclaration(
            name="records",
            family=DataRecordEntry,
            definition_id=RECORDS_DEFINITION_ID,
            records=(
                EntryRecordDeclaration(name="core-data-record", record=DataRecord, definition_id=RECORDS_DEFINITION_ID),
                EntryRecordDeclaration(
                    name="test-serve-typed-energy", record=ServedEnergyRecord, definition_id=RECORDS_DEFINITION_ID
                ),
            ),
        ),
    )


def test_records_family_serves_typed_backing_property_through_adapter_from_store() -> None:
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_families=_declarations(),
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        run = store.fetch(Run, store.save(Run()), eager=True)
        generic = store.fetch(DataRecord, store.save(DataRecord.from_value(_TOTAL_ENERGY, "e", -1.0)), eager=True)
        low = store.fetch(
            ServedEnergyRecord,
            store.save(ServedEnergyRecord(-5.0, product_of=(RunEdge("run", "runs", run.id),))),
            eager=True,
        )
        store.fetch(ServedEnergyRecord, store.save(ServedEnergyRecord(2.5)), eager=True)
        store.replace(low, ServedEnergyRecord(-6.0, product_of=(RunEdge("run", "runs", run.id),)))

        with TestClient(
            create_asgi_app(adapter_from_store(store), baseurl="http://testserver"), base_url="http://testserver"
        ) as client:
            info = client.get("/v1/info/_httk_records")
            assert info.status_code == 200, info.text
            energy = info.json()["data"]["properties"]["_httk_total_energy"]
            assert energy["$id"] == _TOTAL_ENERGY
            assert energy["x-optimade-unit"] == "eV"
            assert energy["x-optimade-type"] == "float"
            # DataRecord does not project the nullable property, so its rows sort as NULL.
            assert energy["sortable"] is True

            filtered = client.get("/v1/_httk_records", params={"filter": "_httk_total_energy < 0"})
            assert filtered.status_code == 200, filtered.text
            assert [(row["id"], row["attributes"]["_httk_total_energy"]) for row in filtered.json()["data"]] == [
                (low.id, -6.0)
            ]
            unknown = client.get("/v1/_httk_records", params={"filter": "_httk_total_energy IS UNKNOWN"})
            assert [row["id"] for row in unknown.json()["data"]] == [generic.id]

            # NULLs (the DataRecord row) sort last in both directions, across pages.
            for sort, expected in (
                ("_httk_total_energy", [-6.0, 2.5, None]),
                ("-_httk_total_energy", [2.5, -6.0, None]),
            ):
                values = []
                url, params = "/v1/_httk_records", {"sort": sort, "page_limit": 2}
                while url:
                    page = client.get(url, params=params)
                    assert page.status_code == 200, page.text
                    values += [row["attributes"].get("_httk_total_energy") for row in page.json()["data"]]
                    url, params = page.json()["links"].get("next"), None
                assert values == expected

            revisions = client.get(f"/v1/_httk_records/{low.id}/_httk_revs")
            assert revisions.status_code == 200, revisions.text
            assert [row["attributes"]["_httk_total_energy"] for row in revisions.json()["data"]] == [-5.0, -6.0]

            included = client.get(
                "/v1/_httk_records", params={"filter": "_httk_total_energy < 0", "include": "_httk_runs"}
            )
            assert included.status_code == 200, included.text
            assert [row["id"] for row in included.json()["included"]] == [run.id]
