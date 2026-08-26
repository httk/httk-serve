#!/usr/bin/env python3
"""Serve a live OPTIMADE structures endpoint directly from ``SqlStore``."""

from httk.atomistic import Species, StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.store import Backend, EntryIdScheme, SqlStore

from httk.serve.optimade import serve

# The application queries this store on every request. Nothing is copied into
# an EntryProvider or an in-memory serving table.
store = SqlStore(
    Backend.sqlite(),
    entry_records={StructureEntry: UnitcellStructureRecord},
    entry_ids=EntryIdScheme("httk.example", "1"),
)
store.save(
    UnitcellStructure(
        [[5, 0, 0], [0, 5, 0], [0, 0, 5]],
        [[0, 0, 0]],
        [Species("Si", ("Si",), (1,))],
        ["Si"],
        chemical_formula_descriptive="Si",
    )
)

# Run by hand: this blocks until interrupted.
HTTK_EXAMPLE_NO_AUTORUN = True

if __name__ == "__main__":
    print("Serving http://127.0.0.1:8080/v1/structures")
    serve(store, port=8080)
