# *httk-web*

This site documents specifically the *httk-web* module. For the full
documentation of *httk₂* as a whole, see [docs.httk.org](https://docs.httk.org).

*httk-web* is a [*httk₂*](https://github.com/httk/httk2) module providing web serving and static publishing functionality under the namespace `httk.web`.

See [Widgets](widgets.md) for safe static page components and the `httk web`
authoring commands.

```{admonition} Quick links
:class: tip

- **API reference**: {doc}`reference/index`
- {doc}`how_it_works`
- {doc}`deployment_apache`
- {doc}`deployment_nginx`
- {doc}`migration_legacy_to_jinja2`
- {doc}`site_template_repository`
````

## Install

Preferably work in a Python virtual environment, then do:
```bash
git clone https://github.com/httk/httk-web
cd httk-web
python -m pip install -e .
```

## Usage (tiny example)

```python
from httk.web import serve

# Serve a site source directory on http://127.0.0.1:8080
serve("path/to/site/src")
```

```{toctree}
:maxdepth: 2
:caption: Documentation

reference/index
widgets
how_it_works
deployment_apache
deployment_nginx
migration_legacy_to_jinja2
site_template_repository
```
