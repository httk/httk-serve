# Deploying a store as an OPTIMADE service

`httk serve optimade serve` runs an OPTIMADE API for an *httk-store* file as a
production ASGI service with uvicorn. It is designed to be the single
`ExecStart` command of a `systemd` unit behind a front-line web server such as
Apache or Nginx, with no application module of your own:

```console
$ httk serve optimade serve /srv/optimade/materials.sqlite \
      --config /srv/optimade/optimade.toml \
      --baseurl https://example.org/optimade \
      --port 8001
```

`STORE` is an SQLite or DuckDB file written by *httk-store*, for example by
`SqliteStore`, `DuckdbStore`, or `httk workflow collect --into`. The format is
recognized from the file header, and a missing file is refused rather than
created. Every entry family in the store's layout that has a registered
OPTIMADE definition is served, as described in {doc}`serving_stores`.
Before binding, the command requests the info, links, and every entry
endpoint in-process and refuses to start if any of them fails.
`httk serve optimade check STORE` performs the same opening and self-test and
prints the endpoints that would be served.

## Installation

Install *httk-serve* together with the distributions that define the record
types stored in the file, since only registered entry families are discovered:
for example *httk-atomistic* for structures and *httk-workflow* for stores
written by `httk workflow collect --into`. The `httk2` metapackage installs all
of them. A DuckDB store additionally needs `httk-store[duckdb]`.

```console
$ python3 -m venv /opt/optimade/.venv
$ /opt/optimade/.venv/bin/pip install httk2
```

## Application record classes

A store that saved instances of your own record classes, for example an
`@entry_record` class passed as `SqliteStore(..., records=[Result])`, must be
reopened with those classes. Name each one with `--records`:

```console
$ cd /srv/optimade
$ httk serve optimade serve results.sqlite --records result_record:Result
```

The module is imported with the working directory first on the import path,
as uvicorn does for application modules, so in a `systemd` unit set
`WorkingDirectory=` to the directory holding it. Stores whose record classes
come from installed distributions, such as those written by
`httk collect --into`, need no `--records`.

## Options

| Option | Meaning |
| --- | --- |
| `--config FILE` | TOML file with the service metadata described below. |
| `--records MODULE:CLASS` | Record class the store was written with that no installed distribution registers; repeatable. |
| `--data-path PATH` or `NAME=PATH` | Filesystem location of the stored files (`PATH/<workspace name>`, or workspace `NAME` at `PATH`; `NAME=PATH` is repeatable). The service serves the bytes of registered files itself at `/data/...`. Exclusive with `--data-url` (see {doc}`serving_stores`). |
| `--data-url URL` | External file server replacing the leading `data` of `files` entry URLs, e.g. `https://example.org/files` (absolute http(s), or a root-relative `/...`); stored entries are unchanged. |
| `--baseurl URL` | Public base URL of the API. Without it, links are derived from each request's host, scheme, and ASGI root path. |
| `--host HOST`, `--port PORT` | TCP interface and port to bind (default `127.0.0.1:8080`). |
| `--uds PATH` | Bind a Unix domain socket instead of a TCP port. |
| `--root-path PATH` | ASGI root path when the proxy forwards a sub-path without its prefix and no `--baseurl` is given. |
| `--workers N` | Number of worker processes (default 1). |
| `--no-access-log` | Disable uvicorn's per-request access log, e.g. when the proxy already logs requests. |

`httk serve optimade check` takes the same `STORE`, `--config`, `--records`,
`--data-path`, and `--data-url` arguments.

With `--data-path`, served `files` URLs are `<request origin>[<root path>]/data/<name>/<path>`; behind a reverse proxy, `--baseurl` decides them instead (`<baseurl>/data/...`), so route `/data/` to the service.

Setting `--baseurl` is the most robust choice behind a proxy, since generated
links then do not depend on forwarded headers. Request-derived links need the
proxy to preserve the `Host` header and send `X-Forwarded-Proto`. Uvicorn trusts
forwarded headers only from the addresses in its `FORWARDED_ALLOW_IPS`
environment variable (`127.0.0.1,::1` by default in current uvicorn releases).
Connections over a Unix domain socket carry no client address, so with `--uds`
forwarded headers are honoured only when `FORWARDED_ALLOW_IPS=*` is set.

Each worker processes one request at a time, so `--workers` sets the number of
concurrently served requests. Every worker opens its own connection to the
store.

## Service metadata

The `--config` file has the fields of
{py:class}`~httk.serve.optimade.model.config.OptimadeConfig` as top-level keys.
Unknown keys and values of the wrong shape are refused; write dates as strings
rather than TOML date-time values. Without a file, the default `httk` provider is
advertised, which is not appropriate for a public database:

```toml
license = "https://example.org/license"
available_licenses_for_entries = ["CC-BY-4.0"]
page_limit_max = 100
cors_origins = ["https://browser.example.org"]

[provider]
name = "Example materials database"
description = "Materials computed by the example group"
prefix = "exmpl"
homepage = "https://example.org"
```

## Updating the data

An SQLite store is queried on every request, so records saved by another
process become visible while the service runs. Entry families are discovered
at start-up, so a family added to the store later is served only after a
restart. The service never writes to the store; it serves correctly when its
user has only read access to the file and its directory. A read-only service
cannot recover a rollback journal left behind by a crashed writer; reopening
the store once from a writing process does.

A DuckDB store is opened read-only, which lets several workers and other
readers share the file but prevents any process from writing to it while the
service runs. Stop the service, update or replace the file, and start it again.

## Example `systemd` unit

```ini
[Unit]
Description=OPTIMADE API for the example materials database
After=network.target

[Service]
User=optimade
Group=optimade
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/optimade/.venv/bin/httk serve optimade serve /srv/optimade/materials.sqlite --config /srv/optimade/optimade.toml --baseurl https://example.org/optimade --port 8001 --workers 4
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
```

The command exits with status 2 when the store or configuration cannot be
opened or the self-test fails, before any port or socket is bound. With
`--workers`, a worker that later cannot reopen the store stops the whole
service rather than being respawned in a loop; `Restart=always` then lets
`systemd` start it again once the store is available.

## Apache

With `mod_proxy` and `mod_proxy_http` enabled, forward the public sub-path to
the service:

```apache
RedirectMatch    ^/optimade$ /optimade/
ProxyPass        /optimade/ http://127.0.0.1:8001/
ProxyPassReverse /optimade/ http://127.0.0.1:8001/
```

Apache neither preserves the `Host` header nor sends `X-Forwarded-Proto` by
default, so keep `--baseurl` set. To rely on request-derived links instead, add
`ProxyPreserveHost On` and, with `mod_headers`,
`RequestHeader set X-Forwarded-Proto "https"`.

The service then answers, for example,
`https://example.org/optimade/v1/structures`. To serve the API at the root of a
virtual host instead, proxy `/` and pass the corresponding `--baseurl`.

To avoid a TCP port, bind a Unix domain socket in a runtime directory that
`systemd` creates for the service, and point Apache at it:

```ini
[Service]
RuntimeDirectory=httk-optimade
RuntimeDirectoryMode=0755
ExecStart=/opt/optimade/.venv/bin/httk serve optimade serve /srv/optimade/materials.sqlite --config /srv/optimade/optimade.toml --baseurl https://example.org/optimade --uds /run/httk-optimade/optimade.sock
```

```apache
ProxyPass        /optimade/ unix:/run/httk-optimade/optimade.sock|http://localhost/
ProxyPassReverse /optimade/ http://localhost/
```

## Nginx

```nginx
location /optimade/ {
    proxy_pass http://127.0.0.1:8001/;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

For a Unix domain socket, use
`proxy_pass http://unix:/run/httk-optimade/optimade.sock:/;`.

## Larger deployments

The command serves one store with its discovered families. To combine an
OPTIMADE index, several stores, custom routes, or a website in one process,
write an application module with {py:func}`~httk.serve.optimade.create_asgi_app`
and {py:func}`~httk.serve.compose_asgi_apps` and run it with uvicorn directly,
as shown in the Apache and Nginx guides for web sites.
