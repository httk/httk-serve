"""A constrained, offline OpenAPI 3.1 adapter for Starlette."""

from ..apptypes import ResponseHook, ServeApp
from .app import (
    ExceptionHandler,
    OpenAPIContractError,
    OpenAPIOperation,
    OpenAPIParameter,
    OpenAPIRequest,
    OpenAPIRequestError,
    OpenAPIResponse,
    RequestErrorHandler,
    RequestScope,
    create_openapi_app,
    parse_openapi_operations,
)
from .binding import (
    BoundOperation,
    BoundParameter,
    OperationBinding,
    OperationContext,
    bind_operation,
    convert_result,
    normalize_parameter_name,
    operation,
)
from .contract import OpenAPIContract
from .resources import load_packaged_contract, packaged_schema_documents, packaged_schema_registry
from .schemas import OpenAPISchemaError, OpenAPISchemaRegistry

__all__ = [
    "BoundOperation",
    "BoundParameter",
    "ExceptionHandler",
    "OpenAPIContract",
    "OpenAPIContractError",
    "OpenAPIOperation",
    "OpenAPIParameter",
    "OpenAPIRequest",
    "OpenAPIRequestError",
    "OpenAPIResponse",
    "OpenAPISchemaError",
    "OpenAPISchemaRegistry",
    "OperationBinding",
    "OperationContext",
    "RequestErrorHandler",
    "RequestScope",
    "ResponseHook",
    "ServeApp",
    "bind_operation",
    "convert_result",
    "create_openapi_app",
    "load_packaged_contract",
    "normalize_parameter_name",
    "operation",
    "packaged_schema_documents",
    "packaged_schema_registry",
    "parse_openapi_operations",
]
