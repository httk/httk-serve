# *httk-optimade*

*httk-optimade* is a [*httk v2*](https://github.com/httk/httk2) module providing tooling for
serving an [OPTIMADE](https://www.optimade.org/) API on top of httk data stores.

```{admonition} Quick links
:class: tip

- {doc}`how_it_works`
- {doc}`reference`
```

```{toctree}
:maxdepth: 2
:caption: Documentation

how_it_works
reference
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
