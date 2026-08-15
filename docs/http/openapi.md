# Constrained OpenAPI applications

`httk.serve.http.openapi` turns a caller-owned OpenAPI 3.1 path contract into a
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
The adapter derives everything it can from them, and rejects missing or unknown
handlers when the application is constructed rather than when a request
arrives.

## Packaged contracts

A protocol that ships its contract and schemas as package data loads both
through one object. `OpenAPIContract.from_package` parses the OpenAPI document,
builds an offline `OpenAPISchemaRegistry` over every bundled `*.json` schema,
and caches the result, so repeated application construction does not re-parse
or re-validate the package data.

```python
from httk.serve.http.openapi import (
    OpenAPIContract,
    OpenAPIRequest,
    OpenAPIResponse,
    create_openapi_app,
)

CONTRACT = OpenAPIContract.from_package("prototype_protocol")

async def create_thing(request: OpenAPIRequest) -> OpenAPIResponse:
    thing = await service.create(request.body)
    return OpenAPIResponse(body=thing)

app = create_openapi_app(
    CONTRACT,
    {"create_thing": create_thing},
    request_error_handler=prototype_request_error,
)
```

By default the contract is read from `schemas/openapi.yaml` and the schemas
from `schemas/` below the package; both are configurable. `schema_transform`
applies a per-document fix-up to each bundled **JSON Schema** document before
it is registered — for correcting pinned upstream defects — and is never
applied to the OpenAPI document itself.

`contract.document()` returns an independent deep copy of the parsed OpenAPI
document, `contract.operations` the parsed operations, `contract.operation(id)`
one of them by `operationId`, and `contract.schemas` the offline registry.

A caller that assembles its document and schemas some other way can still pass
a plain mapping together with an explicit `schemas=` registry. Supplying both a
contract and `schemas=`, or a mapping without one, is a contract error.

## Responses derived from the contract

Each operation exposes the status and body contracts the document declares:

- `operation.success_status` — the single declared 2xx status, or `None` when
  the operation declares zero or several. A contract declaring more than one
  2xx status is still accepted; only the derivation is withheld.
- `operation.success_contracts` — the `(media type, schema id)` pairs declared
  for that status.
- `operation.response_contracts(status)` — the same for any one status.

A handler therefore does not restate the status the document already declares.
`OpenAPIResponse(body=thing)` responds with the operation's declared success
status, and a bodyless `OpenAPIResponse()` does the same for an operation whose
success response has no body. State a status explicitly when an operation
declares several 2xx statuses, or to select a declared non-success status.

Handlers receive immutable normalized path parameters, query parameters,
lowercase headers, and a JSON body validated through the contract's registry.
They return an `OpenAPIResponse`, including its exact media type when a status
declares more than one; a unique body media type is inferred. Response values
are schema validated before they are serialized.

These accessors also let an implementation be tested against its own contract,
so that facts written in Python — which error document an operation produces,
which media types it can return — cannot silently drift from the document that
declares them. `httk.serve.dsp` does this in `tests/test_dsp_contract_agreement.py`.

## Errors

Use `exception_handlers` only for deliberate protocol exception types, such as
a protocol's declared error object. Unexpected handler exceptions are left to
Starlette; the adapter does not mask programmer errors. `path_converters`
provides a transparent mapping from OpenAPI parameter name to Starlette route
converter, for example `{"id": "path"}`.
