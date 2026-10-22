# Combined server

`httk serve combined` serves one *httk-store* file as a website plus its OPTIMADE
API, and optionally the stored files themselves, from a single process:

| Path | Content |
| --- | --- |
| `/` | the web site (default: a browser for the store) |
| `/optimade/` | the OPTIMADE API (`/optimade` redirects) |
| `/data/...` | bytes of registered stored files, only with `--files-dir` |

```bash
httk serve combined check  STORE --files-dir files
httk serve combined serve  STORE --files-dir files --port 8080
```

`STORE` is an SQLite or DuckDB store, e.g. written by `httk collect --into`.
Other options:

| Option | Meaning |
| --- | --- |
| `--files-dir DIR` | Serve registered files at `/data/`; see below. Exclusive with `--data-path`. |
| `--data-path URL` | Point served file URLs at an external file server instead; no `/data/` route. |
| `--public-base-url URL` | Public URL of the whole server; default derives from each request. |
| `--site DIR` | Serve your own site at `/` instead of the default one. |
| `--config TOML`, `--records MODULE:CLASS` | As for {doc}`../optimade/deployment`. |
| `--host`, `--port`, `--uds`, `--root-path`, `--workers N`, `--no-access-log` | As for `httk serve optimade serve`. |

`check` opens the store and requests the OPTIMADE endpoints and site pages
in-process without serving.

From Python:

```python
from httk.serve.combined import create_combined_app

app = create_combined_app(store, files_dir="files")  # an ASGI application
```

It takes the same choices as keyword arguments (`site`, `files_dir`,
`data_path`, `public_base_url`, `root_path`); the caller keeps ownership of
`store`.

## Serving files

`--files-dir DIR` makes `DIR` what the stored `data/` prefix names. Stored file
URLs look like `data/<workspace name>/<path>`, so each workspace must be
reachable as `DIR/<workspace name>`:

```bash
mkdir files && ln -s /path/to/workspace files/<name>
```

Only files registered in the store are served (exact URL match); the final path
component may not be a symlink and the on-disk size must match the registered
size. Files download as attachments. Served `files.url` values are absolute:
`<request origin>/data/...`, or `<public base URL>/data/...`.

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
