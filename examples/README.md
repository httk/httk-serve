# httk-optimade examples

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
