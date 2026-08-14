# JSON and JSON-LD GET applications

Use `json_get_app` to expose a fixed or live JSON document, and
`jsonld_get_app` for its JSON-LD specialization, without
coupling the serving layer to its vocabulary or retrieval protocol:

```python
from httk.serve.http import json_get_app, jsonld_get_app

discovery_app = json_get_app(
    discovery_provider.current_document,
    path="/.well-known/example",
)

catalogue_app = jsonld_get_app(
    catalogue_provider.current_document,
    media_type='application/ld+json; profile="https://example.test/profiles/catalogue"',
    profile="https://example.test/protocols/catalogue-get",
)
```

A zero-argument synchronous or asynchronous factory is invoked for every
request, so records added to a caller-owned store can become visible without
rebuilding the application. A fixed mapping can be supplied when a snapshot is
intended.

Both applications provide GET and HEAD, JSON `Accept` handling, strong ETags,
conditional requests, cache control, wildcard CORS by default, and an optional
RFC 6906 profile link. Their routes default to `/`, which is convenient when an
application is mounted with `compose_asgi_apps`.

These are HTTP representation helpers, not linked-data protocols. They do not
define discovery, prescribe JSON-LD terms, validate a schema or RDF graph, or
make a conformance claim. Those contracts and their OpenAPI or schema assets
belong to the application or protocol repository using the helper.
