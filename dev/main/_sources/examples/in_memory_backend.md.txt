# Standing up a backend from plain dict rows, without writing a provider

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

```{literalinclude} ../../examples/optimade/in_memory_backend.py
:language: python
:lines: 51-
```
