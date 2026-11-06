# How It Works

## Current

The default mode is the current, modern `httk-serve` workflow.

### Directory layout

A site source directory is expected to contain:

- `content/`: page sources (`.md`, `.rst`, `.html`)
- `templates/`: Jinja2 templates (for example `default.html.j2` and `base_default.html.j2`)
- `static/`: static files copied as-is in publish mode
- `functions/`: optional Python modules exposing `execute(...)`

### Runtime flow

1. Route resolution maps a URL path to a content page or static file.
2. Content rendering extracts metadata and body HTML.
3. Function injection evaluates `*-function` metadata entries when query/post constraints are satisfied.
4. Template rendering produces final HTML through Jinja2.
5. ASGI serving returns responses, or static publishing writes `.html` output files.

### Public API

The main API surface is:

- `httk.serve.web.create_asgi_app(...)`
- `httk.serve.web.serve(...)`
- `httk.serve.web.publish(...)`

### Example usage

Serve dynamically:

```python
from httk.serve.web import serve
serve("src", port=8080)
```

Publish statically:

```python
from httk.serve.web import publish
publish("src", "public", "http://127.0.0.1/")
```

To control link style in published output:

```python
publish("src", "public", "http://127.0.0.1/", use_urls_without_ext=False)  # -> about.html
publish("src", "public", "http://127.0.0.1/", use_urls_without_ext=True)   # -> about
```

To split static and dynamic hosting in publish mode:

```python
publish(
    "src",
    "public",
    "https://dynamic.example",          # dynamic host
    host_static="https://static.example",  # static host
)
```

Default classification:

- pages with at least one `*-function` metadata key are treated as `dynamic`
- pages without function metadata are treated as `static`

You can override per page in frontmatter:

```yaml
---
hosting: static   # or: dynamic
---
```

### Examples

Modern examples live under `examples/modern`:

- `minimal`
- `rst_site`
- `blog`
- `search_app`

For a ready-made starter repository, see {doc}`site_template_repository`.

## Legacy

`httk-serve` also supports a compatibility mode for legacy site structures and templates.

### Enable compatibility mode

Use `compatibility_mode=True` in API calls:

```python
from httk.serve.web import serve
serve("src", compatibility_mode=True)
```

### Compatibility behaviors

When compatibility mode is enabled, `httk-serve` additionally supports:

- `.httkweb` content and `.httkweb.html` template resolution
- legacy formatter constructs used by old templates (for example repeat/call/if forms)
- loading global metadata from `config.*` (or another name via `config_name`)
- running `functions/init.py` at engine startup
- `_functions/` directory fallback when `functions/` is not present

## Site resource lifecycle

`functions/init.py` can open a persistent, site-local resource once and register
its synchronous cleanup callback through the stable
`global_data["httk_serve_resources"]` entry.  `SiteResources` calls callbacks in
reverse registration order when its `SiteEngine` closes.  The registry is
idempotent, so it is safe for a server wrapper and ASGI shutdown to both close
the same engine.

```python
# src/functions/init.py
from httk.serve.web import SITE_RESOURCES_KEY


def execute(global_data, **kwargs):
    store = open_store()
    global_data["store"] = store
    global_data[SITE_RESOURCES_KEY].register(store.close)
```

Startup failures close resources registered so far before the original error is
reported.  `publish()`, `httk serve web check`, and `httk serve web list` close their
short-lived engines automatically; an ASGI app owns its engine until ASGI
lifespan shutdown.  Call `engine.close()` (or use `with SiteEngine(...)`) when
managing an engine directly.

### Examples

Migrated legacy examples are available under `examples/legacy`:

- `static_simple`
- `hello_world_app`
- `rst_templator`
- `blog`
- `search_app`

For legacy examples that use optional old `httk` subsystems, availability depends on those dependencies.

See also: {doc}`migration_legacy_to_jinja2`.
