# *httk-serve*

This site documents specifically the *httk-serve* module. For the full
documentation of *httk₂* as a whole, see [docs.httk.org](https://docs.httk.org).

*httk-serve* provides the web and OPTIMADE capabilities of *httk₂*: it turns
data supplied through the neutral `httk.core.EntryProvider` contract into a
standards-compliant OPTIMADE API, and ships the website machinery (widgets,
templates, deployment) that presents it. It carries no knowledge of what it
serves — domain modules and stores supply providers; *httk-serve* speaks the
protocol.

```{admonition} Quick links
:class: tip

- **Serve your data over OPTIMADE**: {doc}`optimade/serving_providers` —
  write an `EntryProvider` (or use one from *httk-store*), serve it, query it
- **Query other databases**: {doc}`optimade/client`
- **How the OPTIMADE side works**: {doc}`optimade/how_it_works`
- **Build and deploy the website**: {doc}`web/index` — widgets, templates,
  Apache/nginx deployment
- **API reference**: {doc}`reference/index`
```

## Install

Preferably work in a Python virtual environment, then do:
```bash
git clone https://github.com/httk/httk-serve
cd httk-serve
python -m pip install -e .
```

## Usage (tiny example)

Any `EntryProvider` — hand-written or database-backed — serves in a few lines:

```python
from httk.serve.optimade import adapter_from_providers, serve

serve(adapter_from_providers([provider]), port=8080)
```

The API is then live (`curl 'http://localhost:8080/v1/structures'`). See
{doc}`optimade/serving_providers` for the complete walkthrough from provider
to running service.

```{toctree}
:maxdepth: 2

dsp
web/index
optimade/index
reference/index
```
