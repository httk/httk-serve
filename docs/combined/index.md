# Combined server

`httk serve combined` serves one *httk-store* file as a website plus its OPTIMADE
API, and optionally the stored files themselves, from a single process:

| Path | Content |
| --- | --- |
| `/` | the web site (default: a browser for the store) |
| `/optimade/` | the OPTIMADE API (`/optimade` redirects) |
| `/data/...` | bytes of registered stored files, only with `--data-path` |

```bash
httk serve combined check  STORE --data-path /path/to/workspaces
httk serve combined serve  STORE --data-path /path/to/workspaces --port 8080
```

`STORE` is an SQLite or DuckDB store, e.g. written by `httk collect --into`.
Other options:

| Option | Meaning |
| --- | --- |
| `--data-path PATH` or `NAME=PATH` | Filesystem location of the stored files, served at `/data/`; see below. `NAME=PATH` is repeatable. Exclusive with `--data-url`. |
| `--data-url URL` | External file server that replaces the leading `data` of stored file URLs (absolute http(s) URL, or a root-relative `/...` path); no `/data/` route. |
| `--public-base-url URL` | Public URL of the whole server; default derives from each request. |
| `--site DIR` | Serve your own site at `/` instead of the default one. |
| `--config TOML`, `--records MODULE:CLASS` | As for {doc}`../optimade/deployment`. |
| `--host`, `--port`, `--uds`, `--root-path`, `--workers N`, `--no-access-log` | As for `httk serve optimade serve`. |

`check` opens the store and requests the OPTIMADE endpoints and site pages
in-process without serving.

From Python:

```python
from httk.serve.combined import create_combined_app

app = create_combined_app(store, data_path="/path/to/workspaces")  # an ASGI application
```

It takes the same choices as keyword arguments (`site`, `data_path`,
`data_url`, `public_base_url`, `root_path`); the caller keeps ownership of
`store`.

## Serving files

Stored file URLs look like `data/<workspace name>/<path>`. `--data-path` names
where the files are on disk, and the server sends the bytes itself at `/data/...`.
Either one `PATH` holding each workspace at `PATH/<workspace name>`:

```bash
httk serve combined serve STORE --data-path /srv/workspaces
```

or one or more `NAME=PATH` entries, giving workspace `NAME`'s files at `PATH`
(no symlinks needed, so the old `ln -s` recipe is unnecessary):

```bash
httk serve combined serve STORE --data-path diamond=/work/diamond --data-path ice=/work/ice
```

In Python, `data_path` is a path or a `{name: path}` mapping.

Only files registered in the store are served (exact stored-URL match); the
final path component may not be a symlink and the on-disk size must match the
registered size. Files download as attachments. Served `files.url` values are
absolute: `<request origin>[<root path>]/data/<name>/<path>`, or
`<public base URL>/data/<name>/<path>`.

To use an external file server instead, give `--data-url URL` (or `data_url=`):
it replaces the leading `data` of stored URLs, and the remainder is
percent-encoded.

## Default site

- **Home**: the entry types served, with counts and the kinds of result records.
- **Browse**: pick a type or kind, build conditions from the queryable
  properties only, or type a raw OPTIMADE filter; results link to entries.
- **Entry**: record values with units, file downloads, structures, and run
  provenance: inputs and outputs, produced by / used by, followed by, and
  alternatives with their staged input files.

`--site DIR` replaces it with your own site (at least an index page). The
default site's source is in the package, under `httk/serve/combined/site/`;
copy it as a starting point. Its pages use the built-in widgets
{doc}`../web/widgets` describes (`optimade_catalog`, `optimade_browser`,
`optimade_entry`).

## Security

There is no authentication: anyone who can reach the server can read the store
through OPTIMADE. Only registered files are downloadable. A custom multi-worker
site using `httk.serve.table` needs `HTTK_SERVE_WEB_TABLE_TOKEN_SECRET` set,
so all workers share the cursor token secret.
