# *httk-optimade*

This site documents specifically the *httk-optimade* module. For the full
documentation of *httk₂* as a whole, see [docs.httk.org](https://docs.httk.org).

*httk-optimade* is a [*httk₂*](https://github.com/httk/httk2) module providing tooling for
serving an [OPTIMADE](https://www.optimade.org/) API on top of *httk₂* data stores.

```{admonition} Quick links
:class: tip

- **How it works**: {doc}`how_it_works`
- **API reference**: {doc}`reference/index`
```

```{toctree}
:maxdepth: 2
:caption: Documentation

reference/index
how_it_works
```

## Quick start

Wire a backend into a `BackendAdapter` and serve it:

```python
from httk.optimade import BackendAdapter, EntrySource, OptimadeConfig, serve

adapter = BackendAdapter(
    store=my_store,
    sources={
        "structures": (EntrySource(target=MyStructureTable, fields=my_field_map),),
    },
)

serve(adapter, OptimadeConfig(), port=8080)
```

A complete runnable example over an in-memory dataset is provided in
`examples/demo_server/` in the repository.
