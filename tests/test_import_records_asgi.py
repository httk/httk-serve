"""ASGI end-to-end serving of ``imports`` entries: projected properties, a filter and the structure relationship."""

from fractions import Fraction as F

from httk.atomistic import (
    ASUStructure,
    ASUStructureImportRecord,
    ASUStructureRecord,
    Species,
    StructureEntry,
    UnitcellStructureImportRecord,
    WyckoffSite,
)
from httk.core import FileEntry, FileRecord, ImportEntry, ImportRecord
from httk.store import Backend, EntryIdScheme, SqlStore
from starlette.testclient import TestClient

from httk.serve.optimade import create_asgi_app


def test_import_records_serve_with_filter_and_structure_relationship() -> None:
    a = F(564, 100)
    structure = ASUStructureRecord.from_structure(
        ASUStructure(
            [[a, 0, 0], [0, a, 0], [0, 0, a]],
            225,
            [WyckoffSite("a", (), "Na"), WyckoffSite("b", (), "Cl")],
            [Species("Na", ("Na",), (1,)), Species("Cl", ("Cl",), (1,))],
        )
    )
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={
                ImportEntry: (ImportRecord, ASUStructureImportRecord, UnitcellStructureImportRecord),
                StructureEntry: ASUStructureRecord,
                FileEntry: FileRecord,
            },
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        imported = store.fetch(
            ASUStructureImportRecord,
            store.save(ASUStructureImportRecord("cif/1.cif", "build-cod 1.0", "imported", structure=structure)),
            eager=True,
        )
        store.save(ImportRecord("cif/2.cif", "build-cod 1.0", "failed", error="No cell."))
        assert imported.structure is not None

        client = TestClient(create_asgi_app(store))
        response = client.get("/v1/_httk_imports", params={"filter": '_httk_status = "imported"'})
        assert response.status_code == 200, response.text
        (row,) = response.json()["data"]
        assert (row["id"], row["attributes"]["_httk_status"]) == (imported.id, "imported")
        assert row["relationships"]["structures"]["data"] == [{"type": "structures", "id": imported.structure.id}]
