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
`page_size=50`, `page_size_options`, `page_size_query`, `caption="OPTIMADE results"`,
`filter`, `filter_query`,
`sort`, `sort_query`, `allowed_origins=()`, `detail_route`, `detail_column`,
`detail_query="id"`, `summary`, and `advanced_filter`. URLs, identifiers, columns, origins, and display text are
strictly bounded and validated. `filter_query` names a browser URL parameter
whose complete value overrides `filter`, while `sort_query` similarly overrides
`sort`; neither is access control. Detail
links require both a safe site-local `detail_route` and a selected
`detail_column`. A column mapping may set `format="formula"`, or use
`{"name": "number", "digits": 2, "scale": 1, "suffix": " eV"}` or
`{"name": "join", "separator": ", "}`. Formula digit runs are rendered in
`sub` elements; all formatter output remains text-only.

A column mapping may also carry an optional `description`. Every column header
always gets a `title` hover hint that starts with the prefixed OPTIMADE field
name (its `key`), so a reader can discover the exact filterable field name by
hovering the header; when `description` is set, the hint becomes
`<key> — <description>`. The hint is baked into the `<th>` at render time and
needs no JavaScript. The `description` is bounded like other display text and,
when present, is also carried in the inert configuration.

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

`summary` is an optional, off-by-default results summary rendered above the
table. `summary=None` disables it and changes nothing else; `summary=True`
enables it with defaults (noun `"entries"`); a mapping may set `noun` and a
`fields` mapping of property name to a `{label, format, values}` presentation
overlay. Each field's label and number/formula/join `format` default to the
matching column's, so `fields` only needs entries for filter-only properties or
overridden labels; `values` maps enum values to display labels. Once enabled, the
summary shows a `Showing X of Y <noun>.` count (from the OPTIMADE `data_returned`
and `data_available` meta counts) and describes the active filter and sort as
pills. Filter description is all-or-nothing: a filter containing `OR`, `NOT`,
parentheses, or any clause the widget cannot render in human terms produces no
filter pills rather than a misleading partial description. The sort pill drops
`id` components, which are pagination tiebreakers, and is omitted entirely when
the effective sort equals the authored default. No summary output is emitted when
`summary` is unset.

When `sort_query` is set, column headers whose field the OPTIMADE service
advertises as sortable (a strict `sortable: true` on the property in
`/info/<entry_type>`) become sort links after discovery succeeds. Clicking one
navigates to the current URL with only the `sort_query` parameter changed:
ascending by default, appending an `,id` tiebreaker except for the `id` column
itself. Clicking the current primary sort column again reverses its direction,
and that column's header carries `aria-sort="ascending"` or `"descending"`.
Every other URL parameter, including the filter, is preserved verbatim. Headers
of non-advertised columns, and all headers when `sort_query` is unset, are never
linked. Navigation is a full page load; there is no dynamic (no-reload) sorting.

`advanced_filter` is an optional, off-by-default fold-out disclosure rendered
above the table. `advanced_filter=None` disables it and changes nothing else;
`advanced_filter=True` enables it with defaults; a mapping may set `label` (the
disclosure heading) and `help_url` (an absolute HTTP(S) URL or site-relative
path to an "available fields" reference, linked only when given and opened in a
new tab). It requires
`filter_query`, because the disclosure is a plain GET `<details>`/`<form>` that
submits a raw OPTIMADE filter under that parameter name; enabling it without
`filter_query` is rejected. The input is prefilled with the effective (authored
or URL-selected) filter, and when `sort_query` is set and present in the URL, a
single hidden input carries that **raw** parameter value (the user's alias, not
the resolved sort) so the form round-trips the current sort. No other URL
parameters are re-emitted: a site's own filter-building form would re-normalize
the filter from its own field parameters, so carrying them would fight the raw
filter the disclosure submits.

The disclosure's own `<form>` also submits a hidden marker parameter named
`<filter_query>_advanced` (for `filter_query="filter"` that is
`filter_advanced`). The disclosure renders **open** on load exactly when that
marker parameter is present in the URL — that is, only when the current view was
submitted from the advanced form itself. Three flows follow: a sidebar-style
search that writes only `?filter=…` (no marker) leaves the disclosure **closed**;
an advanced submit produces `?filter=…&filter_advanced=1` (plus the raw `sort`
when configured) and the disclosure is **open** on the next load; and because the
header-sort links preserve every existing URL parameter, the marker survives a
sort click, so the disclosure stays open after re-sorting an advanced search.
Sites must not use the `<filter_query>_advanced` parameter name for anything
else, since its mere presence controls the disclosure's open state.

Put any other sort and filter controls in ordinary GET forms; the original query
snapshot reaches the provider and stays bound across pager requests. Page size
defaults to 50; the widget configuration accepts up to 500, but the size a
service actually serves is bounded by that service's own maximum page limit.
Continuation requests, tokens,
cursors, rows, rendered HTML, and JSON responses all have explicit size bounds.

A page-size dropdown follows the same opt-in URL-wiring grammar as `filter_query`
and `sort_query`: it renders only when `page_size_query` names the URL parameter
that carries the chosen size, and `page_size_options` lists the offered sizes (a
sequence of 1-8 distinct integers, each 1..500; default `(50, 100, 500)`). The
options are sorted ascending and always include the current `page_size` so the
active state is selectable. On load the browser reads that parameter and uses it
as the page size only when it exactly matches one of the options, otherwise it
falls back to the authored `page_size`. Changing the dropdown navigates (via
`location.assign`) to the same URL with only the page-size parameter changed —
every other parameter, including the filter, sort, and the advanced-form marker,
is preserved — so the effective size survives sort clicks and vice versa. Like
the other controls it writes no history, cookie, or storage state of its own.
The achievable page size is capped by the OPTIMADE service's own maximum page
limit — for httk-serve services this is `OptimadeConfig.page_limit_max` (default
50), and a larger `page_limit` is rejected with HTTP 403 per the OPTIMADE spec —
so pick `page_size_options` your service actually accepts.

## OPTIMADE field definitions

`httk.serve.optimade_fields` (also available as `optimade_fields`) renders a
static, server-side table of OPTIMADE property definitions. Unlike
`optimade_table` it performs no browser fetch and ships no JavaScript: a served
site knows its property definitions at startup, so the table is rendered once
from a `properties` mapping the site supplies. Its only asset is a stylesheet.

The declaration accepts `properties` and `caption="Field definitions"`.
`properties` is a mapping of served property name to that property's OPTIMADE
property-definition mapping — the same `{served_name: definition}` shape a
served schema exposes, where each definition carries `$id`, `title`,
`description`, and `sortable`. It must be a non-empty mapping of at most 512
identifier-named entries, each value itself a mapping. A site wrapper normally
passes its `ServedSchema.property_definitions[entry]` mapping straight in.

Rows are sorted alphabetically by name. Each row shows the property name in a
`<code>` element and the **first paragraph** of its `description` (the text up
to the first blank line, bounded like other display text). A missing or
non-string `description` renders an empty cell rather than raising. The name is
linked to the human-readable definition page at the property's `$id` (opened in
a new tab) when that `$id` is an HTTP(S) URL with a host and no credentials;
ad-hoc synthesized ids — those whose path contains `/ad-hoc/`, which are not
published anywhere — and any non-HTTP(S) or malformed id render the name as
plain unlinked text. A bad `$id` never raises. All names, links, and text are
escaped.

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
