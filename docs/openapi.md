# Constrained OpenAPI applications

`httk.serve.openapi` turns a caller-owned OpenAPI 3.1 path contract into a
Starlette application. It deliberately implements a small, offline subset:
`GET` and `POST` paths, local references for path and operation pieces,
required JSON request bodies whose schemas are external `$ref` values, exact
numeric responses, JSON response media types, and bodyless responses. It is
not a general OpenAPI implementation.

Path, query, and header parameters are supported only as simple strings, with
an optional string `enum`; required parameters are enforced. Path declarations
must exactly match the variables in their path template. Formats, defaults,
coercion, and other parameter constraints are intentionally rejected.

The protocol or prototype owns its OpenAPI document and JSON Schema documents.
Build an `OpenAPISchemaRegistry` from those documents, then map every
`operationId` to a handler. The adapter rejects missing or unknown handlers
when the application is constructed.

```python
from httk.serve.openapi import (
    OpenAPIRequest,
    OpenAPIResponse,
    OpenAPISchemaRegistry,
    create_openapi_app,
)

schemas = OpenAPISchemaRegistry(prototype_schema_documents)

async def create_thing(request: OpenAPIRequest) -> OpenAPIResponse:
    thing = await service.create(request.body)
    return OpenAPIResponse(201, thing, media_type="application/json")

app = create_openapi_app(
    prototype_openapi_document,
    {"create_thing": create_thing},
    schemas=schemas,
    request_error_handler=prototype_request_error,
)
```

Handlers receive immutable normalized path parameters, query parameters,
lowercase headers, and a JSON body validated through the supplied registry.
They return an `OpenAPIResponse`, including the exact declared status and,
when a status has multiple body media types, its exact media type. A unique
body media type is inferred. Response values are schema validated before they
are serialized.

Use `exception_handlers` only for deliberate protocol exception types, such as
a protocol's declared error object. Unexpected handler exceptions are left to
Starlette; the adapter does not mask programmer errors. `path_converters`
provides a transparent mapping from OpenAPI parameter name to Starlette route
converter, for example `{"id": "path"}`.
