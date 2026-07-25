# httk-optimade examples

Every script here is also a page in the documentation (generated from its module
docstring; see `docs/conf.py`) and is run by the smoke test in
`tests/test_examples.py`. The two servers below are the exception: they bind a
port and serve until interrupted, so they declare `HTTK_EXAMPLE_NO_AUTORUN =
True` and are run by hand.

- `query_in_process.py` — walk a live OPTIMADE API over real HTTP without
  binding a port, using starlette's `TestClient` against `create_asgi_app`:
  `/v1/info`, entry listings, filters, sorting, pagination via `links.next`,
  and `include=` compound documents. Run it with:

  ```
  python examples/query_in_process.py
  ```

- `in_memory_backend.py` — stand up a backend without writing a provider:
  `InMemoryStore` over plain dict rows plus `EntrySource`, `build_served_schema`
  and `BackendAdapter`. Run it with:

  ```
  python examples/in_memory_backend.py
  ```

- `provider_server/` — a minimal server fed by an `httk.core.EntryProvider`,
  with declared relationships, wired up by `adapter_from_providers`. Run it with:

  ```
  python examples/provider_server/serve.py
  ```

- `demo_server/` — a complete OPTIMADE server over a small in-memory dataset.
  It demonstrates how to implement the backend protocols (`Store`, `Searcher`,
  search expressions) and how to wire a `BackendAdapter` with `EntrySource`
  field maps into `httk.optimade.serve()`. Run it with:

  ```
  python examples/demo_server/serve.py
  ```

  and try, e.g.:

  ```
  curl 'http://localhost:8080/v1/info'
  curl 'http://localhost:8080/structures?filter=elements HAS "Ga" AND nelements=2'
  ```

  The demo also exercises the optional OPTIMADE features:

  ```
  # sorting (a leading '-' sorts descending)
  curl 'http://localhost:8080/structures?sort=-nelements'

  # relationships: filter structures by a related reference, and pull the
  # related references into the compound document with include
  curl 'http://localhost:8080/structures?filter=references.id HAS "ref-1"'
  curl 'http://localhost:8080/structures/demo-1?include=references'

  # the files entry type, and the input/output file roles on a calculation
  curl 'http://localhost:8080/files'
  curl 'http://localhost:8080/calculations/calc-1'

  # a trajectory: compact 'constant' lists, plus large per-frame properties
  # served as null with meta.partial_data_links
  curl 'http://localhost:8080/trajectories/traj-1'

  # inline slice of a trajectory dimension (stop is inclusive)
  curl 'http://localhost:8080/trajectories/traj-1?dimension_slices=dim_frames[1:3:]'

  # fetch a large property in the JSON Lines partial-data format
  curl 'http://localhost:8080/partial_data/trajectories/traj-1/cartesian_site_positions'
  ```
