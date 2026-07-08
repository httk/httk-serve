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
