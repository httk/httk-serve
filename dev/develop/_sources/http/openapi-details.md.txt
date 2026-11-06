# OpenAPI adapter details

Reference detail for `httk.serve.http.openapi`. Start with {doc}`openapi` for
the overview and a minimal end-to-end example.

## The supported subset

The adapter implements a small, offline subset of OpenAPI 3.1: `GET` and `POST`
paths, local references for path and operation pieces, required JSON request
bodies whose schemas are external `$ref` values, exact numeric responses, JSON
response media types, and bodyless responses. It is not a general OpenAPI
implementation.

Path, query, and header parameters are supported only as simple strings, with an
optional string `enum`; required parameters are enforced. Path declarations must
exactly match the variables in their path template. Formats, defaults, coercion,
and other parameter constraints are intentionally rejected.

The protocol or prototype owns its OpenAPI document and JSON Schema documents.
The adapter derives everything it can from them — routes, methods, statuses,
media types, request and response validation, and how each handler's arguments
are filled — so an implementation supplies the functions that implement its API
and little else. Whatever cannot be derived is checked when the application is
constructed rather than when a request arrives.

## Packaged contracts

A protocol that ships its contract and schemas as package data loads both
through one object. `OpenAPIContract.from_package` parses the OpenAPI document,
builds an offline `OpenAPISchemaRegistry` over every bundled `*.json` file that
decodes to a mapping carrying a `$schema` key — other `*.json` files below the
schema root are silently skipped — and caches the result, so repeated
application construction does not re-parse or re-validate the package data.

```python
from httk.serve.http.openapi import OpenAPIContract, create_openapi_app

CONTRACT = OpenAPIContract.from_package("prototype_protocol")

async def create_thing(body):
    return await service.create(body)

app = create_openapi_app(
    CONTRACT,
    {"create_thing": create_thing},
    request_error_handler=prototype_request_error,
)
```

By default the contract is read from `schemas/openapi.yaml` and the schemas from
`schemas/` below the package; both are configurable. `schema_transform` applies a
per-document fix-up to each bundled **JSON Schema** document before it is
registered — for correcting pinned upstream defects — and is never applied to
the OpenAPI document itself.

`contract.document()` returns an independent deep copy of the parsed OpenAPI
document, `contract.operations` the parsed operations, `contract.operation(id)`
one of them by `operationId`, and `contract.schemas` the offline registry.

A caller that assembles its document and schemas some other way can still pass a
plain mapping together with an explicit `schemas=` registry. Supplying both a
contract and `schemas=`, or a mapping without one, is a contract error.

## Responses derived from the contract

Each operation exposes the status and body contracts the document declares:

- `operation.success_status` — the single declared 2xx status, or `None` when
  the operation declares zero or several. A contract declaring more than one 2xx
  status is still accepted; only the derivation is withheld.
- `operation.success_contracts` — the `(media type, schema id)` pairs declared
  for that status.
- `operation.response_contracts(status)` — the same for any one status.

A handler therefore does not restate the status the document already declares. It
may simply return a value:

- `None` — the declared bodyless success response;
- a mapping or list — the declared success status with that body;
- an explicit `OpenAPIResponse` — when the handler needs a non-success status, a
  specific media type because the status declares several, or extra headers.

Response values are schema validated before they are serialized.

These accessors also let an implementation be tested against its own contract, so
that facts written in Python — which error document an operation produces, which
media types it can return — cannot silently drift from the document that declares
them. `httk.serve.dsp` does this in `tests/test_dsp_contract_agreement.py`.

## Binding handler parameters

Handler parameters are filled **by name**, never by position, so reordering the
`parameters:` array in the contract can never silently swap two arguments:

| Handler parameter | Bound from |
|---|---|
| the normalized name of a declared path, query or header parameter | that value |
| `body` | the validated request body |
| a parameter annotated `OpenAPIRequest` | the whole request |
| a name the request scope supplies | that extra |

Normalization turns a wire name into a Python identifier — `-` becomes `_`,
camelCase and ACRONYMCase split on case boundaries, and the result is lowercased:
`providerPid` → `provider_pid`, `X-Request-ID` → `x_request_id`.

An optional parameter that the request omits is not passed at all, so the
handler's own default applies. Where a wire name cannot or should not drive the
Python name, `operation()` declares an alias:

```python
{
    "dataset_request": operation(Provider.dataset, aliases={"id": "dataset_id"}),
    "create_thing": operation(Provider.create, aliases={"body": "message"}),
}
```

Everything is checked once, when the application is constructed: every required
declared parameter must reach a handler parameter, every handler parameter
without a default must be satisfied, two declared parameters may not normalize
onto the same handler parameter, and `*args`, `**kwargs` and positional-only
parameters are rejected because they cannot be bound by name.

Passing `implementation=` resolves entries that name unbound methods against that
object with `getattr`, so subclass overrides are honoured.

## Per-request scope

`request_scope` supplies values a handler cannot get from the contract, and
contributes response metadata. It is an async context manager returning an
`OperationContext`, entered around the handler call:

```python
from functools import partial

@asynccontextmanager
async def scope(request):
    session = open_session()
    context = OperationContext(extras={"session": session})
    yield context
    # Defer work until after a clean response is sent. after_response is a
    # zero-argument coroutine callable; bind arguments with functools.partial.
    context.after_response = partial(flush, session)
```

Names a handler wants are declared per operation with `operation(..., extras=(...))`
and listed in `scope_names=`; both are verified at construction, so a renamed
extra fails loudly instead of silently arriving as a default.

The context's `media_type`, `headers` and `after_response` are folded into the
response **only when the handler returns normally** — an adapted error response
never inherits them. Statements after the `yield` are skipped when the handler
raises, so deferred work is not released after a failed request. When both a
handler-returned `OpenAPIResponse` and the scope set `after_response`, the
handler's value wins.

`after_response` replaces framework-specific background-task objects: the adapter
wraps the coroutine callable and runs it once, after the response body has been
sent. A handler may also set `after_response` directly on the `OpenAPIResponse`
it returns.

## Errors

Use `exception_handlers` only for deliberate protocol exception types, such as a
protocol's declared error object. Unexpected handler exceptions are left to the
underlying server; the adapter does not mask programmer errors. `path_converters`
provides a transparent mapping from OpenAPI parameter name to route converter,
for example `{"id": "path"}`.
