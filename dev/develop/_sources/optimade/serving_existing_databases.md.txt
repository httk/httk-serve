# Serving an existing database

Use this page when a SQL database already exists and should be served over
OPTIMADE in place, with no reprocessing and no copy. You describe which
column serves which OPTIMADE property; *httk-serve* does the rest. To build a
new database with *httk-store* instead, see {doc}`serving_stores`; for small
in-memory data, see {doc}`serving_providers`.

## SQL databases

`TableStore` (from *httk-store*) queries existing tables read-only, and
`MappedSource` maps OPTIMADE property names to its columns:

```python
from httk.store import ListTable, TableSource, TableStore

from httk.serve.optimade import MappedSource, adapter_from_sources, serve

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
            fields={"nelements": lambda row: len(row["elements"])},
        )
    },
)
serve(adapter, port=8080)
```

The complete runnable script, including a sample database, is
{doc}`/examples/existing_database/serve`.

- The `keys` map (served property to column) makes properties filterable and,
  unless they are lists, sortable. A missing column fails at construction with
  a `ValueError`.
- `fields` holds computed or overriding extractors, applied to each row. Such
  properties are served but not filterable: a filter on one returns HTTP 501.
- A free-form formula column maps to `chemical_formula_descriptive`;
  `chemical_formula_reduced` has a strict normalized form.
- `id` is required and is always served as a string, so an integer primary key
  works (`id="3"`). `type` is the endpoint name and cannot be mapped.
- Values are presented as JSON: `datetime` becomes RFC 3339 UTC with `Z`
  (naive values are taken as UTC), `Decimal` becomes a number, and other types
  raise `TypeError`, telling you to map the property with a `fields` extractor.
- List fields come from a `ListTable` (child rows) or a `DelimitedColumn`
  (delimited text); see the `TableStore` guide linked below.
- Entry types use the standard OPTIMADE definitions. A custom `_prefix_`
  property needs an `EntryTypeDefinition.extended()` definition, passed as
  `definitions={"structures": definition}`.

## Other backends

Any object implementing the `httk.store.query` `Store` protocol can be served
the same way. The neutral truth table lives in `httk.store.query.conformance`:
load `conformance_rows()` into your backend and call
`check_query_conformance(store, target)` to verify it. See
[serving existing tables](https://docs.httk.org/httk-store/dev/main/details/db-tables.html) and
[implementing the protocol](https://docs.httk.org/httk-store/dev/main/details/db-querying.html).

## Deployment notes

- Requests are processed synchronously on the server's event loop, so a slow
  query delays other requests; index the columns you filter and sort on. File
  SQLite URLs work as is.
- The store is caller-owned: close it on shutdown.

## Limitations

- No revisions, alternatives or `as_of` queries. `include` and relationships
  are only available through a `relationships` extractor on `MappedSource`.
- `LENGTH` filters are not implemented (HTTP 501).
- Each request also runs an exact unfiltered `COUNT` for `meta.data_available`
  and a filtered `COUNT`.
- `OptimadeConfig(page_limit_max=...)` caps the page size (default 50), and
  `OptimadeConfig(cors_origins=...)` enables browser access.
- SQLite `LIKE` is ASCII case-insensitive.
