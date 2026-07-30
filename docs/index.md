# *httk-optimade*

This site documents specifically the *httk-optimade* module. For the full
documentation of *httk₂* as a whole, see [docs.httk.org](https://docs.httk.org).

*httk-optimade* is a [*httk₂*](https://github.com/httk/httk2) module providing a generic
implementation of the [OPTIMADE](https://www.optimade.org/) protocol; the entry schemas it
serves are supplied by providers (such as *httk-atomistic*'s structure provider) through
the neutral `httk.core.EntryProvider` contract.

```{admonition} Quick links
:class: tip

- **Serving entry providers (usage guide)**: {doc}`serving_providers`
- **Reading remote services (usage guide)**: {doc}`client`
- **Worked examples (runnable scripts)**: {doc}`examples/index`
- **Notebook: walking an OPTIMADE API in-process**: {doc}`notebooks/examples`
- **How it works**: {doc}`how_it_works`
- **API reference**: {doc}`reference/index`
```

```{toctree}
:maxdepth: 2
:caption: Documentation

serving_providers
client
examples/index
notebooks/examples
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
