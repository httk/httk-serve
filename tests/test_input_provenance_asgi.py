"""ASGI end-to-end serving of the collect pipeline's input-provenance shape.

``httk collect`` stores a staged structure input as a main structure plus an
alternative ``<main>~file_<hex>`` whose ``source_file`` is the input's
``FileRecord`` (also a top-level ``files`` entry); the run's input edge names
the alternative.
"""

from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

from httk.atomistic import Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord, UnitcellStructureView
from httk.core import FileEntry, FileRecord
from httk.core.provenance import Run, RunEdge, RunEntry
from httk.store import EntryIdScheme, SqliteStore
from starlette.testclient import TestClient

from httk.serve.optimade import create_asgi_app

_KIND = "file_0123456789abcdef"
_SHA256 = "50fb3e0738b8f05204d5ebd2522779fe3fcd160c68683c056ad1494e22c3f824"


def _view(*symbols: str) -> UnitcellStructureView:
    return UnitcellStructureView(
        UnitcellStructure(
            [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
            [[index / len(symbols), 0, 0] for index in range(len(symbols))],
            [Species(symbol, (symbol,), (1,)) for symbol in symbols],
            list(symbols),
        )
    )


def test_staged_structure_input_serves_through_its_file_alternative(tmp_path: Path) -> None:
    source = FileRecord(url="data/ws/jobs/a/POSCAR", name="POSCAR", size=120, sha256=_SHA256, format="vasp-poscar")
    with SqliteStore(
        tmp_path / "collected.sqlite",
        entry_records={StructureEntry: UnitcellStructureRecord, RunEntry: Run, FileEntry: FileRecord},
        entry_ids=EntryIdScheme("httk.test", "1"),
    ) as store:
        main = store.fetch(UnitcellStructureRecord, store.save(_view("Na")), eager=True).id
        other = store.fetch(UnitcellStructureRecord, store.save(_view("Si", "O")), eager=True).id
        file_id = store.fetch(FileRecord, store.save(source), eager=True).id
        exact = replace(UnitcellStructureRecord.from_structure(_view("Na")), source_file=source)
        store.save(exact, alternative_of=main, alternative_kind=_KIND)
        alt = f"{main}~{_KIND}"
        edges = (RunEdge("initial_structure", "structures", alt), RunEdge("reference", "structures", other))
        unknown = RunEdge("missing", "structures", f"{main}~file_ffffffffffffffff")
        run = store.fetch(Run, store.save(Run(inputs=(*edges, unknown), source_id="ws:a")), eager=True).id

        client = TestClient(create_asgi_app(store))

        has_input = client.get(f"/v1/_httk_runs/{run}").json()["data"]["relationships"]["_httk_has_input"]["data"]
        assert {(item["type"], item["id"]) for item in has_input} >= {("structures", alt), ("structures", other)}

        # include= names target entry types, not relationship keys.
        assert client.get(f"/v1/_httk_runs/{run}", params={"include": "_httk_has_input"}).status_code == 400
        # It resolves the alternative (one fetch per id) next to the batched main;
        # the unknown alternative is skipped like an unknown main id.
        included = client.get(f"/v1/_httk_runs/{run}", params={"include": "structures"}).json()["included"]
        assert sorted(item["id"] for item in included) == sorted([alt, other])
        single = client.get(f"/v1/structures/{main}/_httk_alts/{_KIND}").json()["data"]
        (included_alt,) = (item for item in included if item["id"] == alt)
        # Same representation as the per-group alternative route.
        assert included_alt == single

        assert [item["id"] for item in single["relationships"]["_httk_is_input"]["data"]] == [run]
        assert "_httk_is_input" not in client.get(f"/v1/structures/{main}").json()["data"].get("relationships", {})

        # Relationship filters live under the _httk_relationships path.
        has = f'_httk_relationships._httk_has_input.id HAS "{alt}"'
        selected = client.get("/v1/_httk_runs", params={"filter": has}).json()
        assert [item["id"] for item in selected["data"]] == [run]

        # source_file is no attribute: it serves as the alternative's files relationship,
        # naming the same deduplicated entry as the top-level save.
        assert single["relationships"]["files"]["data"] == [{"type": "files", "id": file_id}]
        # The single-alternative route honours an explicit include= like a main entry.
        alt_route = f"/v1/structures/{main}/_httk_alts/{_KIND}"
        assert "included" not in client.get(alt_route).json()
        (included_file,) = client.get(alt_route, params={"include": "files"}).json()["included"]
        assert (included_file["type"], included_file["id"]) == ("files", file_id)
        assert client.get(alt_route, params={"include": "_httk_has_input"}).status_code == 400
        assert client.get("/v1/files").json()["meta"]["data_returned"] == 1
        served_file = client.get(f"/v1/files/{quote(file_id, safe='')}").json()["data"]
        assert served_file["attributes"]["checksums"]["sha256"] == _SHA256
