#!/usr/bin/env python3
"""Serve an existing SQL database over OPTIMADE without copying it into httk."""

import sqlite3
import sys
from pathlib import Path

from httk.core.optimade import parse_optimade_filter
from httk.store import ListTable, TableSource, TableStore

from httk.serve.optimade import MappedSource, adapter_from_sources, execute_query, serve

HTTK_EXAMPLE_REQUIRES = ("httk.atomistic", "sqlalchemy")

# Stand-in for "your existing database": any schema you already have works.
if not Path("materials.sqlite").exists():
    with sqlite3.connect("materials.sqlite") as db:
        db.executescript(
            """
            CREATE TABLE materials (mat_id INTEGER PRIMARY KEY, formula TEXT, nsites INTEGER);
            CREATE TABLE material_elements (mat_id INTEGER, element TEXT);
            INSERT INTO materials VALUES (1, 'NaCl', 2), (2, 'Si', 2), (3, 'NaSi', 8), (4, 'TiO2', 6);
            INSERT INTO material_elements VALUES (1, 'Na'), (1, 'Cl'), (2, 'Si'), (3, 'Na'), (3, 'Si'),
                (4, 'Ti'), (4, 'O');
            """
        )

# The store queries the tables in place, read-only; the map says which column serves which property.
store = TableStore(
    "sqlite:///materials.sqlite",
    {"materials": TableSource("materials", lists={"elements": ListTable("material_elements", "mat_id", "element")})},
)
adapter = adapter_from_sources(
    store,
    {
        "structures": MappedSource(
            "materials",
            {"id": "mat_id", "chemical_formula_descriptive": "formula", "nsites": "nsites", "elements": "elements"},
            fields={"nelements": lambda row: len(row["elements"])},  # computed: served, not filterable
        )
    },
)

for filter_text, sort, limit in (('elements HAS "Na"', None, 10), ('id="3"', None, 10), (None, [("nsites", True)], 2)):
    ast = parse_optimade_filter(filter_text) if filter_text else None
    results = execute_query(
        adapter,
        ["structures"],
        ["id", "chemical_formula_descriptive", "nsites", "nelements"],
        [],
        limit,
        0,
        ast,
        sort=sort,
    )
    print(filter_text or f"sort -nsites, limit {limit}", "->", [dict(row.values) for row in results])

if "--serve" in sys.argv:
    print("Serving http://127.0.0.1:8080/v1/structures")
    serve(adapter, port=8080)

store.close()
