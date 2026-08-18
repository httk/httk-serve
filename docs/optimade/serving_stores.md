# Serving directly from an entry store

For a durable deployment, pass an `httk.store.EntryStore` directly to
`create_asgi_app`. This is the preferred path when records already live in a
store:

```python
from httk.atomistic import StructureEntry, UnitcellStructure, UnitcellStructureRecord
from httk.store import Backend, SqlStore
from httk.serve.optimade import create_asgi_app

store = SqlStore(
    Backend.duckdb("materials.duckdb"),
    entry_records={StructureEntry: UnitcellStructureRecord},
)
store.save(UnitcellStructure(...))

app = create_asgi_app(store, baseurl="https://materials.example/optimade")
```

No provider snapshot or in-memory table is constructed. The application
discovers every family in `store.entry_layout` that has a registered OPTIMADE
entry-type definition, translates filters and sorting to the store backend,
applies offset/limit before hydrating records, and queries the store for every
request. A record saved after `app` is created is therefore visible to later
requests.

The store remains caller-owned. Closing the ASGI application does not close the
store or its database; the deployment's lifespan code should dispose them.

## Discovery and mixed stores

`adapter_from_store(store, **schema_options)` exposes the adapter explicitly
when an application needs to inspect or wrap it:

```python
from httk.serve.optimade import adapter_from_store, create_asgi_app

adapter = adapter_from_store(store)
print(adapter.schema.all_entries)
app = create_asgi_app(adapter)
```

Discovery uses the store's declared entry-family layout, not every private
dataclass table reachable from stored objects. Families without an OPTIMADE
definition are ignored. DSP publication declarations can consequently share a
store with structures without becoming an OPTIMADE endpoint.

Every discovered OPTIMADE family must have complete, valid
`StoredPropertyProjection` mappings for its served record classes. Adapter
construction validates that contract eagerly. For a mixed store that contains
defined families intended only for another service or not yet projection-ready,
construct an explicit `adapter_from_stores` source list containing only the
families this endpoint should publish.

All concrete record classes configured for one family are served together.
Each record class owns its exact response/query/sort behavior through
`StoredPropertyProjection`; properties lacking an exact query or sort mapping
are not silently approximated. Public IDs are the canonical content IDs,
optionally prefixed when constructing an explicit `StoredEntrySource` federation.

## Several stores

Use `adapter_from_stores` only when an endpoint must federate explicitly named
stores or needs public-ID prefixes:

```python
from httk.store.backend.sql import StoredEntrySource
from httk.serve.optimade import adapter_from_stores

adapter = adapter_from_stores(
    (
        StoredEntrySource(primary, StructureEntry, "primary", "p-"),
        StoredEntrySource(archive, StructureEntry, "archive", "a-"),
    )
)
```

Filtering and bounds remain store-side. Sorted pages are merged from bounded
candidate streams and only the final page is hydrated. Duplicate visible IDs
raise rather than choosing a record implicitly.

## When providers are still useful

`EntryProvider` and `adapter_from_providers` remain appropriate for small
generated datasets, compatibility adapters, and entry types that do not have a
durable storage representation. That path intentionally materializes provider
records in an in-memory query store. It should not be used merely to copy a
database into memory before serving it.

See `examples/optimade/store_server/` for a runnable server and
`examples/optimade/query_in_process.py` for a socket-free live-store example.
