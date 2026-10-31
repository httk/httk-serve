# Serving an OpenAPI contract

`httk.serve.http.openapi` turns a caller-owned **OpenAPI 3.1** document into a
running HTTP application. You own the contract — the OpenAPI document and its
JSON Schemas — and supply one function per operation; the adapter derives the
routes, methods, status codes, media types, and request/response validation
from the contract. Whatever cannot be derived is checked when the application is
**constructed**, not when a request arrives, so a handler that does not match
its contract fails at startup rather than in production.

It implements a deliberately small, offline subset of OpenAPI — `GET`/`POST`,
JSON request/response bodies, and string path/query/header parameters. It is not
a general OpenAPI implementation; the exact boundaries are in
{doc}`openapi-details`.

## From schema to server

Four steps take an OpenAPI contract to a live application:

1. **Ship the contract** — the OpenAPI document and its `*.json` schemas as
   package data (conventionally `schemas/openapi.yaml` alongside `schemas/`).
2. **Load it** into an `OpenAPIContract`.
3. **Write handlers** — one callable per `operationId`, returning plain JSON
   values. Parameters are filled **by name** from the contract.
4. **Build the app** with `create_openapi_app`, then mount or run it.

```python
from httk.serve.http.openapi import OpenAPIContract, create_openapi_app, operation

# 1-2. Load the packaged OpenAPI document + JSON Schemas (parsed and validated
#      once, then cached).
CONTRACT = OpenAPIContract.from_package("prototype_protocol")

# 3. One handler per operationId. Each parameter is filled by name from a
#    declared path/query/header parameter or the request body; the return value
#    is validated against the operation's declared response before it is sent.
async def create_thing(body):
    return await service.create(body)            # -> the declared 2xx body

async def get_thing(thing_id):
    return await service.get(thing_id)

# 4. Build the application. It returns a ServeApp (a mountable ASGI
#    application) — no Starlette import required.
app = create_openapi_app(
    CONTRACT,
    {
        "create_thing": create_thing,
        # `operation()` adjusts how a handler binds to its contract, e.g. when a
        # wire parameter name should map to a different Python parameter.
        "get_thing": operation(get_thing, aliases={"id": "thing_id"}),
    },
    request_error_handler=prototype_request_error,
)
```

Serve `app` with any ASGI server:

```python
import uvicorn

uvicorn.run(app, host="127.0.0.1", port=8080)
```

The only required argument beyond the contract and operations is
`request_error_handler`, which turns a malformed request into your protocol's
own error response. Everything else — a per-request scope, deliberate protocol
exception handlers, route converters — is optional and covered in the details.

## A worked, real-world example

`httk.serve.dsp` is a complete implementation of this pattern: it ships a DSP
OpenAPI contract as package data, binds each operation to a provider method, and
builds the app with `create_openapi_app` (see `httk/serve/dsp/api.py`). Because
the contract accessors let an implementation be tested against its own document,
`httk.serve.dsp` checks that the errors and media types written in Python cannot
drift from the contract (`tests/test_dsp_contract_agreement.py`).

## Details

```{toctree}
:maxdepth: 1

openapi-details
```

The details page covers packaged contracts and the plain-mapping alternative,
the response contracts derived from each operation, how handler parameters are
bound by name, the per-request scope (and post-response callbacks), and error
handling.
