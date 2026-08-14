# httk-serve

*httk-serve* is a [*httk₂*](https://github.com/httk/httk2) distribution providing a generic implementation of the [OPTIMADE](https://www.optimade.org/) protocol. Durable deployments can pass an `httk.store.EntryStore` directly to `create_asgi_app`, which discovers registered OPTIMADE families and queries them lazily; `EntryProvider` remains the in-memory path for generated and compatibility datasets.

The served API version is **OPTIMADE v1.3.0**. Implemented optional parts of the
specification include sorting, the `references`, `files`, and `trajectories` entry types,
relationships with the `include` query parameter, per-property metadata, the partial data
protocol (JSON Lines format and `dimension_slices`) with the compact list representation,
and the `license`/`available_licenses`/warnings meta and base-info fields. Optional parts
that remain unimplemented: cross-source sort merging, filtering on relationship
`.target.*`/`.description`/`.role` properties, the sparse JSON Lines layout, and
rejection of unrecognized query parameters. See
`docs/optimade/how_it_works.md` for the protocol architecture and backend/web seams.

Multiple Starlette services can be composed at explicit paths. For example, an
OPTIMADE index and database can share a parent application without coupling
their URL configuration:

```python
from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.optimade import OptimadeIndexConfig, create_index_asgi_app

index = create_index_asgi_app(OptimadeIndexConfig(links=[...]), baseurl="https://example.org/optimade/index/")
app = compose_asgi_apps([ASGIAppMount("/optimade/index", index)], root=ASGIAppMount("/", website_app))
```

The implementation was ported from the OPTIMADE server in httk v1 (which served OPTIMADE
v1.0.0) and then upgraded to v1.3.0. The legacy client-side `validation/` subpackage has
intentionally not been ported; use the official
[`optimade-validator`](https://github.com/Materials-Consortia/optimade-python-tools) tool
to check conformance of a running server.

## HTTP helpers

`httk.serve.http` provides lightweight, mountable JSON, JSON-LD, and explicit
file-map applications without requiring a website source tree. Protocol-owned
schemas, vocabulary rules, and discovery document construction remain with
the caller.

## Web tooling

`httk.serve.web` provides Jinja2 rendering, legacy `.httkweb` compatibility,
static publication, and an ASGI runtime. Its trusted widgets include
`httk.serve.table` for provider-backed pagination and
`httk.serve.optimade_table` for browser-side OPTIMADE access. Use
`httk serve web serve`, `httk serve web check`, or `httk serve web list`.
