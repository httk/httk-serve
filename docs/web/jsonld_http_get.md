# JSON-LD HTTP GET applications

Use `jsonld_http_get_app` to expose one fixed or live JSON-LD document without
coupling the serving layer to its vocabulary or retrieval protocol:

```python
from httk.serve.web import jsonld_http_get_app

app = jsonld_http_get_app(
    catalogue_provider.current_document,
    media_type='application/ld+json; profile="https://example.test/profiles/catalogue"',
    profile="https://example.test/protocols/catalogue-get",
)
```

A zero-argument synchronous or asynchronous factory is invoked for every
request, so records added to a caller-owned store can become visible without
rebuilding the application. A fixed mapping can be supplied when a snapshot is
intended.

The application provides GET and HEAD, JSON-LD `Accept` handling, strong ETags,
conditional requests, cache control, wildcard CORS by default, and an optional
RFC 6906 profile link. Its route defaults to `/`, which is convenient when the
application is mounted with `compose_asgi_apps`.

This is an HTTP representation helper, not a linked-data protocol. It does not
define discovery, prescribe JSON-LD terms, validate a schema or RDF graph, or
make a conformance claim. Those contracts and their OpenAPI or schema assets
belong to the application or protocol repository using the helper.
