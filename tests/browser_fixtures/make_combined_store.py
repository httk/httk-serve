"""Build the ``httk serve combined`` browser-test store and file tree in a directory.

Usage: ``python make_combined_store.py DIR``. Writes ``DIR/collected.sqlite`` and
``DIR/files/ws/...`` (served at ``/data/ws/...``) and prints a JSON summary of the
ids and file names the browser spec asserts on.
"""

import hashlib
import json
import sys
from dataclasses import replace
from pathlib import Path

from httk.atomistic import Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord, UnitcellStructureView
from httk.core import FileEntry, FileRecord
from httk.core.data_records import DataRecordEntry, TotalEnergyRecord
from httk.core.provenance import Run, RunEdge, RunEntry
from httk.store import EntryIdScheme, SqliteStore

KIND = "file_0123456789abcdef"
ODD_NAME = "out put#1.txt"
FILES = {
    "POSCAR": b"Na\n1.0\n4 0 0\n0 4 0\n0 0 4\nNa\n1\nDirect\n0 0 0\n",
    ODD_NAME: b"0123456789abcdef\n",
    "OUTCAR": b"free energy TOTEN = -1.5 eV\n",
    "summary.json": b'{"ok": true}\n',
}


def _view(*symbols: str) -> UnitcellStructureView:
    return UnitcellStructureView(
        UnitcellStructure(
            [[4, 0, 0], [0, 4, 0], [0, 0, 4]],
            [[index / len(symbols), 0, 0] for index in range(len(symbols))],
            [Species(symbol, (symbol,), (1,)) for symbol in symbols],
            list(symbols),
        )
    )


def main(directory: Path) -> None:
    jobs = directory / "files" / "ws" / "jobs" / "a"
    jobs.mkdir(parents=True)
    records: dict[str, FileRecord] = {}
    for name, content in FILES.items():
        (jobs / name).write_bytes(content)
        sha = hashlib.sha256(content).hexdigest()
        records[name] = FileRecord(url=f"data/ws/jobs/a/{name}", name=name, size=len(content), sha256=sha)
    # On disk under the files directory but never registered: must not be served.
    (jobs / "unregistered.txt").write_bytes(b"secret\n")
    lost = FileRecord(url=None, name="lost.txt", size=3, sha256="e" * 64)

    with SqliteStore(
        directory / "collected.sqlite",
        entry_records={
            StructureEntry: UnitcellStructureRecord,
            RunEntry: Run,
            FileEntry: FileRecord,
            DataRecordEntry: TotalEnergyRecord,
        },
        entry_ids=EntryIdScheme("httk.test", "1"),
    ) as store:

        def save[T](record: object, cls: type[T] | None = None) -> str:
            return store.fetch(cls or type(record), store.save(record), eager=True).id

        main_id = save(_view("Na"), UnitcellStructureRecord)
        other = save(_view("Si", "O"), UnitcellStructureRecord)
        ids = {name: save(record) for name, record in records.items()}
        ids["lost.txt"] = save(lost)
        exact = replace(UnitcellStructureRecord.from_structure(_view("Na")), source_file=records["POSCAR"])
        store.save(exact, alternative_of=main_id, alternative_kind=KIND)
        alt = f"{main_id}~{KIND}"
        energy = save(TotalEnergyRecord(total_energy=-1.5))
        job = save(
            Run(
                inputs=(RunEdge("initial_structure", "structures", alt),),
                outputs=(
                    RunEdge("energy", "records", energy),
                    RunEdge("odd", "files", ids[ODD_NAME]),
                    RunEdge("outcar", "files", ids["OUTCAR"]),
                    RunEdge("lost", "files", ids["lost.txt"]),
                ),
                source_id="ws:a",
            )
        )
        post = save(
            Run(
                inputs=(RunEdge("job", "runs", job),),
                outputs=(RunEdge("summary", "files", ids["summary.json"]),),
                source_id="ws:a/postprocess",
            )
        )
    summary = {
        "store": str(directory / "collected.sqlite"),
        "files_dir": str(directory / "files"),
        "main": main_id,
        "other": other,
        "alt": alt,
        "energy": energy,
        "job": job,
        "post": post,
        "file_ids": ids,
        "odd_name": ODD_NAME,
        "odd_bytes": FILES[ODD_NAME].decode(),
        "lost_sha256": lost.sha256,
    }
    print(json.dumps(summary))


if __name__ == "__main__":
    main(Path(sys.argv[1]))
