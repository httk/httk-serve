# Widgets

Widgets are small, static page components. Put a trusted site-local Python module
in `src/widgets/` and invoke it as a paragraph by itself:

```python
# src/widgets/hello.py
from httk.serve.web.widgets import trusted_html


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

## Declared widget assets

Reviewed built-in and site-local widgets can declare small immutable assets with
their trusted HTML. `WidgetAsset.path` is a safe POSIX path relative to the
internal `/_httk/serve/assets/` root; it is not a filesystem path. Its non-empty
immutable `bytes` content is capped by `MAX_WIDGET_ASSET_BYTES`, and its content
type must be one of the supported explicit values (`text/css` or
`text/javascript`). A `WidgetRenderResult` takes an immutable tuple of assets:

```python
from httk.serve.web.widgets import WidgetAsset, WidgetRenderResult


def render(context):
    internal_root = f"{context.page['relbaseurl'].rstrip('/')}/_httk/serve"
    return WidgetRenderResult(
        f'<link rel="stylesheet" href="{internal_root}/assets/site-example.css">',
        assets=(WidgetAsset("site-example.css", b".example{}", "text/css"),),
    )
```

The engine owns an isolated registry for each site instance. Re-declaring the
same path with identical bytes and content type is allowed; conflicting
declarations fail the page render. During static publication, only assets used
by rendered pages are written under `public/_httk/serve/assets/`, once each. A site
static file may not collide with that output path. Dynamic requests can retrieve
only registered safe paths, never package or filesystem paths.

`httk.text` (also available as `text`) is a small built-in useful for examples.
Built-ins always use the `httk.` namespace; local widgets always use `site.` and
therefore cannot shadow them.

## Paginated tables

`httk.serve.table` (also available as `table`) is the built-in cursor-paginated table.
Put its provider beside other site functions; it is an ordinary contained Python
module, not an ASGI application:

```python
# src/functions/materials.py
from httk.serve.web import TablePage


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
buttons POST a compact JSON envelope to the reserved `/_httk/serve/table/page` route,
which requests exactly one more bounded page. There is no OFFSET policy, result
materialization, server-held database cursor, SQL text, or general function
dispatch in httk-serve. The browser state contains an HMAC-SHA256-authenticated,
canonical JSON token binding the provider, route, widget id, page size, literal
provider arguments, original query snapshot, page context, columns, direction,
opaque backend cursor, optional revision, version, and expiry. A token is not
encrypted, so do not put secrets in provider arguments or cursors.

By default the engine uses an in-process random 32-byte signing key. For more
than one worker, restarts, or deployments behind a process manager, configure a
stable secret of at least 32 bytes either explicitly or through the environment:

```python
app = create_asgi_app("src", table_token_secret=os.environ["HTTK_SERVE_WEB_TABLE_TOKEN_SECRET"])
# serve(..., table_token_secret=...) accepts the same option.
```

`HTTK_SERVE_WEB_TABLE_TOKEN_SECRET` is also read when no explicit value is supplied.
Tampered, expired, cross-route, and cross-widget tokens are rejected before the
provider is called. Set `TablePage.revision` when the backend can pin a dataset
revision; its continuation request receives that value and a changed revision
causes a reset response. `revision=None` is the ergonomic default and explicitly
has live (non-snapshot) backend semantics.

Published static pages render only their first page. Their table controls are
disabled with a clear live-site message and no live table assets are emitted.
This phase deliberately does not split interactive table assets across static
and dynamic hosts. Package JS and CSS are served only by the dynamic app under
the reserved `/_httk/serve/assets/` route. The idempotent JS supports multiple tables,
uses accessible busy/live/disabled states, shows recoverable inline errors, and
dispatches a bubbling `httk-serve:table-updated` event after replacing rows for
site-local enhancers.

## OPTIMADE tables

`httk.serve.optimade_table` (also available as `optimade_table`) is a separate,
browser-driven OPTIMADE table. Its v1 Python declaration renders an accessible
empty table shell, an inert JSON configuration script scoped to the widget id,
and its three internal assets in both live and published output:

```md
{{ widget("optimade_table", base_url="https://optimade.example/v1", columns=["chemical_formula_reduced", "nsites"]) }}
```

The declaration accepts `base_url`, `entry_type="structures"`, `columns`,
`page_size=50`, `caption="OPTIMADE results"`, `filter`, `filter_query`,
`sort`, `sort_query`, `allowed_origins=()`, `detail_route`, `detail_column`, and
`detail_query="id"`. URLs, identifiers, columns, origins, and display text are
strictly bounded and validated. `filter_query` names a browser URL parameter
whose complete value overrides `filter`, while `sort_query` similarly overrides
`sort`; neither is access control. Detail
links require both a safe site-local `detail_route` and a selected
`detail_column`. A column mapping may set `format="formula"`, or use
`{"name": "number", "digits": 2, "scale": 1, "suffix": " eV"}` or
`{"name": "join", "separator": ", "}`. Formula digit runs are rendered in
`sub` elements; all formatter output remains text-only.

At page load, the browser validates the local configuration and negotiates the
remote OPTIMADE API. An unversioned base is negotiated through `/versions` for
OPTIMADE major 1; an explicit `/v1`, `/v1.3`, or `/v1.3.0` base is checked
directly. It then validates `/info` and `/info/<entry_type>` before requesting
the first page. The selected columns are requested as `response_fields`; every
response is bounded, has its content type checked, and is validated as an
OPTIMADE envelope before the table changes. Equivalent tables on one page share
that discovery work, while their results and pager state remain independent.

The widget is browser-to-service traffic: httk-serve does not proxy requests or
hold remote cursors. Deploy the OPTIMADE endpoint on the page origin, or ensure
that its CORS policy permits the published page origin. `allowed_origins` is a
client-side allow-list for explicitly requested OPTIMADE origins and
continuation URLs; redirects are rejected before the browser follows them. It
does not grant CORS access and is not an access-control boundary. Origin hosts
must be ASCII; write internationalized domain names in the browser-compatible
punycode form that appears in `window.location.origin`.

`filter_query` is useful for static publications and ordinary GET forms. When
the browser URL contains that parameter, its **first complete value** replaces
the authored `filter`; an empty value means no filter. Filters are never
concatenated. The override is limited to 4096 characters, matching the shell
limit, and an overlong value is shown as a recoverable table error. `sort_query`
uses the same replacement and limit rules for the authored `sort`. No URL,
history, cookie, storage, or form field is modified by the table.

Only the current page is rendered. A widget holds at most 100 previous page
URLs in JavaScript memory; Previous refetches the preceding page and Next uses
the validated OPTIMADE continuation URL. Those URLs are never written into the
DOM, events, browser storage, or page history. Empty, loading, loaded, and
error states update the table's native controls and polite status message;
errors expose a Retry control. Published static pages therefore have the same
client behavior as live pages, subject to the remote service being reachable
and CORS-enabled from that publication.

Remote values are inserted as text, never HTML. Null values render as an
accessible dash and complex values have deterministic, bounded JSON-like
presentation. If detail links are configured, only `detail_column` becomes a
site-local link: the resource id replaces `detail_query` while other detail
route query values are retained. The widget dispatches a bubbling
`httk-serve:optimade-table-updated` event only after a page commits; its detail has
only the entry type, result count, page index, and next/previous availability.

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
httk serve web list src
httk serve web check src
httk serve web serve src --reload
```

`check` validates every content page, including widget parsing, names, local
module/provider signatures and first-page results, duplicate ids, a static
render, and collisions with the reserved `/_httk/serve/` runtime route. `list` prints
canonical widget names and their source locations, including `httk.serve.table`.
`serve --reload` uses uvicorn's process-level reload supervisor.
