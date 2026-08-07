# Walking a live OPTIMADE API in-process, without binding a port

Everything a client sees — the JSON:API envelope, `meta`, `links.next`, the
compound document produced by `include` — is produced by the ASGI application,
not by the query engine underneath it. So the honest way to see what a server
answers is to speak HTTP to it. What this example shows is that you do not need
a port, a background thread, or a `curl` in another terminal to do so:
`create_asgi_app` returns an ordinary ASGI application, and starlette's
`TestClient` drives it directly in this process. Requests are real requests
(routing, query-string validation, error handling, response rendering all run);
they simply never touch a socket.

That makes this the shape to reach for in tests, in notebooks, and while
developing a provider: the whole API surface is available synchronously, and a
failure is a normal Python traceback rather than a log line in a server that is
still running.

The data here comes from the ready-made providers *httk-data* registers for the
standard `files` and `calculations` entry types, so no provider class has to be
written to have something to query — see
[Serving entry providers](../optimade/serving_providers.md) for how to write your own,
and `examples/optimade/provider_server/` for a runnable one. Only `sortable=` is
declared: a property is sortable only if the server says so in
`/v1/info/<entry>`, and `adapter_from_providers` forwards that declaration to
the served schema.

The walk below is the OPTIMADE query surface in order of how you would discover
it:

* `/v1/info` — what this implementation serves at all: API version, endpoints,
  entry types per format.
* `/v1/files` — an entry listing. `response_fields` narrows `attributes` to the
  properties asked for (plus the ones the standard requires a response to
  carry), which is how you keep a response small.
* `?filter=size > 100000` — a numeric comparison, offered because the property
  is described as an integer.
* `?filter=files.id HAS "file-2"` — list membership (`HAS`), here over the ids
  related to each calculation.
* `?sort=-size` — descending sort on a declared-sortable property.
* `?page_limit=2` plus `links.next` — pagination as a client should do it: keep
  following the link the server hands back until it is `null`.
* `?include=files` — a compound document: the related `files` resources are
  returned alongside the calculations, in a top-level `included` array.

Run it with `python examples/optimade/query_in_process.py`.

```{literalinclude} ../../examples/optimade/query_in_process.py
:language: python
:lines: 47-
```
