"""Standing up a backend from plain dict rows, without writing a provider

An `EntryProvider` is the neutral contract a *data* package implements so that
any serving module can expose it. But when the data is already sitting in
memory — rows loaded from a JSON file, records built in a notebook, fixtures in
a test — there is a shorter path: hand the rows to `InMemoryStore`, describe
what is served, and wire the two together with a `BackendAdapter`. That is the
whole backend, and it is what this example does in about forty lines.

Four pieces, and no others:

`InMemoryStore`
: A ready-made implementation of the backend `Store` protocol over
  `{target: [row, ...]}`, where a row is any plain mapping. It supports the
  same search-expression surface a SQL store does, so it is not a toy: it is
  the store `adapter_from_providers` itself loads provider records into, and
  the one the SQL backend in *httk-store* is checked against for parity.

`EntrySource`
: Where an entry endpoint's rows come from, and how a row becomes a response.
  `target` is the store key to search; `fields` maps each served OPTIMADE
  property to a function extracting it from a row — which is where a backend
  key gets renamed (`__id` to `id`), a constant gets injected (`type`), or a
  value gets computed on the fly. `sort_keys` maps a property to the row key to
  sort on.

`build_served_schema`
: The declaration of what exists: which entry types, which of their properties
  are served, which come back by default, and which may be sorted on. The
  property definitions themselves are the vendored OPTIMADE standard, loaded
  with `standard_entry_type` — so `/v1/info/files` documents types, units and
  descriptions without a line of schema written here.

`BackendAdapter`
: The binding of store to endpoints. Note what is *not* passed: no
  `field_handlers` table. When it is omitted, the adapter derives filter
  handlers from the schema, assuming each property is filtered against a row
  key of the same name. That assumption holds here (`size`, `name` and
  `media_type` are stored under those names), so filtering works with no
  handler code. A backend whose row keys differ supplies its own tables —
  `examples/optimade/demo_server/` shows that, along with relationships, partial data
  and a custom entry type.

The query at the end goes through `execute_query`, i.e. the engine directly,
skipping HTTP. The same adapter can equally be handed to `serve(adapter)` or
`create_asgi_app(adapter)` — see `examples/optimade/query_in_process.py`, which walks
the HTTP surface of an adapter built this way.

Run it with `python examples/optimade/in_memory_backend.py`.
"""

from typing import Any

from httk.core import standard_entry_type

from httk.serve.optimade import BackendAdapter, EntrySource, InMemoryStore
from httk.serve.optimade.backend import execute_query
from httk.serve.optimade.filter import parse_optimade_filter
from httk.serve.optimade.schema.served import build_served_schema

# The backend's own rows: plain dicts, in whatever shape the data already has.
# '__id' is deliberately *not* named 'id', to have something for an extractor
# to rename below.
FILES: list[dict[str, Any]] = [
    {"__id": "file-1", "name": "INCAR", "size": 512, "media_type": "text/plain", "stored": "2021-03-15T10:20:00Z"},
    {"__id": "file-2", "name": "OUTCAR", "size": 204800, "media_type": "text/plain", "stored": "2021-03-15T10:25:00Z"},
    {
        "__id": "file-3",
        "name": "trajectory.xyz",
        "size": 1048576,
        "media_type": "chemical/x-xyz",
        "stored": "2021-03-16T08:00:00Z",
    },
]

# The subset of the standard 'files' properties this backend serves. Every name
# must be described by the entry type definition; 'id' and 'type' are always
# required, the rest are served because they are listed here.
SERVED_PROPERTIES = ["id", "type", "last_modified", "name", "size", "media_type", "url"]


def make_adapter() -> BackendAdapter:
    """A complete OPTIMADE backend over FILES: store, schema, sources."""
    schema = build_served_schema(
        {"files": standard_entry_type("files")},
        {"files": SERVED_PROPERTIES},
        # Returned when the client does not ask for specific response_fields.
        default_response_overrides={"files": ["name", "size", "media_type"]},
        # A promise published in /v1/info/files: only these may appear in ?sort=.
        sortable={"files": ["size", "name"]},
    )
    source = EntrySource(
        target="files",
        fields={
            "id": lambda row: row["__id"],  # renamed from the backend key
            "type": lambda row: "files",  # constant for this endpoint
            "name": lambda row: row["name"],
            "size": lambda row: row["size"],
            "media_type": lambda row: row["media_type"],
            "last_modified": lambda row: row["stored"],
            "url": lambda row: "https://example.org/files/" + row["name"],  # computed
        },
        sort_keys={"size": "size", "name": "name"},
    )
    return BackendAdapter(store=InMemoryStore({"files": FILES}), sources={"files": (source,)}, schema=schema)


def main() -> None:
    adapter = make_adapter()

    # execute_query(adapter, entry_types, response_fields, ..., limit, offset, filter, sort)
    # drives the engine directly: no HTTP, no JSON:API envelope, just rows.
    rows = execute_query(
        adapter,
        ["files"],
        ["id", "name", "size", "url"],
        [],
        100,
        0,
        parse_optimade_filter('size > 1000 AND media_type = "text/plain"'),
    )
    print('filter: size > 1000 AND media_type = "text/plain"')
    for row in rows:
        print("  ", row.values)

    # Sorting uses the backend keys declared in EntrySource.sort_keys; the
    # boolean is 'descending'.
    print("\nsort: -size")
    for row in execute_query(adapter, ["files"], ["id", "size"], [], 100, 0, sort=[("size", True)]):
        print("  ", row.values)

    # The schema is what the API documents about itself.
    print("\nserved entry types:", adapter.schema.all_entries)
    print("sortable files properties:", adapter.schema.sortable_response_fields["files"])
    print("default response fields:", adapter.schema.default_response_fields["files"])


if __name__ == "__main__":
    main()
