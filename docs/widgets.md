# Widgets

Widgets are small, static page components. Put a trusted site-local Python module
in `src/widgets/` and invoke it as a paragraph by itself:

```python
# src/widgets/hello.py
from httk.web.widgets import trusted_html


def render(context, *, name: str) -> str:
    return f"Hello, {name}!"
```

```md
{{ widget("site.hello", name="Ada") }}
```

The result is rendered after Markdown, reStructuredText, HTML, or compatibility
content conversion and before the site page template. Plain strings are HTML
escaped. Return `trusted_html("<strong>...</strong>")`, `Markup`, or
`WidgetRenderResult` only for reviewed HTML. Widget arguments are parsed as
literals; they are never evaluated as Python.

`httk.text` (also available as `text`) is a small built-in useful for examples.
Built-ins always use the `httk.` namespace; local widgets always use `site.` and
therefore cannot shadow them.

## Paginated tables

`httk.table` (also available as `table`) is the built-in cursor-paginated table.
Put its provider beside other site functions; it is an ordinary contained Python
module, not an ASGI application:

```python
# src/functions/materials.py
from httk.web import TablePage


def provide(context, request, *, family: str = "all"):
    # request.page_size is 1..500; request.cursor is opaque (or None).
    # Pass context.query to your data layer's own filter implementation.
    rows, next_cursor, previous_cursor, total = find_materials(
        family=family,
        filters=context.query,
        cursor=request.cursor,
        limit=request.page_size,
    )
    return TablePage.from_rows(
        rows,
        columns=["formula", {"key": "band_gap", "label": "Band gap (eV)", "align": "end"}],
        next_cursor=next_cursor,
        previous_cursor=previous_cursor,
        total=total,
    )
```

The common one-line form is:

```md
{{ widget("table", provider="materials") }}
```

Other literal properties, apart from `id`, `page_size`, `caption`, and
`row_template`, are passed to `provide()` as provider arguments and are bound
into page-navigation state:

```md
{{ widget("table", provider="materials", family="oxide", page_size=100, caption="Oxides") }}
```

`ProviderContext` supplies immutable `route`, `widget_id`, `query`, `page`, and
`global_data` snapshots. `context.url_for("details", query={"id": "mp/1"})`
builds a safely encoded site-local URL without exposing a request object.
`TableRequest` supplies the bounded `page_size`, opaque `cursor`, and optional
`revision`. `TablePage` contains structured mapping rows, `TableColumn`s,
opaque next/previous cursors, and an optional total. It accepts a simple mapping
with the same field names as a convenience. Rows are copied into bounded,
JSON-like presentation data; raw HTML, lazy records, and arbitrary objects are
not table values.

The default table renderer escapes all provider values. It presents text,
numbers, booleans, and simple sequences. For richer rows, use an explicit,
trusted site template:

```md
{{ widget("table", provider="materials", row_template="material_row") }}
```

```html
<!-- src/templates/material_row.html.j2 -->
<tr>
  <td><a href="{{ row.detail_url }}">{{ row.formula }}</a></td>
  <td>{{ row.band_gap }}</td>
</tr>
```

Row templates use the configured template engine and normal autoescaping. Their
intentional context is only `row`, `columns`, `table` (`route` and `widget_id`),
`page`, and `query`; they do not receive the request or engine globals. Template
names must stay inside `src/templates/`. A caption defaults to “Data table”; set
`caption=` for a more useful accessible name.

On the live site the first render requests only the first page. Next/previous
buttons POST a compact JSON envelope to the reserved `/_httk/table/page` route,
which requests exactly one more bounded page. There is no OFFSET policy, result
materialization, server-held database cursor, SQL text, or general function
dispatch in httk-web. The browser state contains an HMAC-SHA256-authenticated,
canonical JSON token binding the provider, route, widget id, page size, literal
provider arguments, original query snapshot, page context, columns, direction,
opaque backend cursor, optional revision, version, and expiry. A token is not
encrypted, so do not put secrets in provider arguments or cursors.

By default the engine uses an in-process random 32-byte signing key. For more
than one worker, restarts, or deployments behind a process manager, configure a
stable secret of at least 32 bytes either explicitly or through the environment:

```python
app = create_asgi_app("src", table_token_secret=os.environ["HTTK_WEB_TABLE_TOKEN_SECRET"])
# serve(..., table_token_secret=...) accepts the same option.
```

`HTTK_WEB_TABLE_TOKEN_SECRET` is also read when no explicit value is supplied.
Tampered, expired, cross-route, and cross-widget tokens are rejected before the
provider is called. Set `TablePage.revision` when the backend can pin a dataset
revision; its continuation request receives that value and a changed revision
causes a reset response. `revision=None` is the ergonomic default and explicitly
has live (non-snapshot) backend semantics.

Published static pages render only their first page. Their table controls are
disabled with a clear live-site message and no live table assets are emitted.
This phase deliberately does not split interactive table assets across static
and dynamic hosts. Package JS and CSS are served only by the dynamic app under
the reserved `/_httk/assets/` route. The idempotent JS supports multiple tables,
uses accessible busy/live/disabled states, shows recoverable inline errors, and
dispatches a bubbling `httk:table-updated` event after replacing rows for
site-local enhancers.

There is no interactive header sorting in this phase. Put sort and filter
controls in ordinary GET forms; the original query snapshot reaches the provider
and stays bound across pager requests. Page size defaults to 50 and is strictly
limited to 500. Continuation requests, tokens, cursors, rows, rendered HTML, and
JSON responses all have explicit size bounds.

Widget invocations must occupy their complete source paragraph/block. Code
examples in Markdown fences or indented code, RST literal/doctest blocks, and
HTML `pre`, `script`, `style`, `textarea`, and `code` content remain literal.
Prefix an otherwise standalone invocation with a backslash to render it literally:

```md
\{{ widget("site.hello", name="Ada") }}
```

Use the umbrella command during authoring:

```console
httk web list src
httk web check src
httk web serve src --reload
```

`check` validates every content page, including widget parsing, names, local
module/provider signatures and first-page results, duplicate ids, a static
render, and collisions with the reserved `/_httk/` runtime route. `list` prints
canonical widget names and their source locations, including `httk.table`.
`serve --reload` uses uvicorn's process-level reload supervisor.
