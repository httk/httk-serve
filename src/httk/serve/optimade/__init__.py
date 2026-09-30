"""Public generic OPTIMADE serving and query APIs."""

from httk.core.optimade import ParserError, ParserSyntaxError, parse_optimade_filter

from .api import create_asgi_app, create_index_asgi_app, serve
from .backend import (
    BackendAdapter,
    EntrySource,
    InMemoryStore,
    MappedSource,
    StoredBackendAdapter,
    adapter_from_providers,
    adapter_from_sources,
    adapter_from_store,
    adapter_from_stores,
    execute_query,
    providers_from_registry,
)
from .engine.processing import process
from .model import (
    EndpointResponse,
    OptimadeConfig,
    OptimadeError,
    OptimadeIndexConfig,
    RawRequest,
    TranslatorError,
    ValidatedParameters,
    ValidatedRequest,
)
from .schema.served import ServedSchema, build_served_schema

__all__ = [
    "BackendAdapter",
    "EndpointResponse",
    "EntrySource",
    "InMemoryStore",
    "MappedSource",
    "OptimadeConfig",
    "OptimadeError",
    "OptimadeIndexConfig",
    "ParserError",
    "ParserSyntaxError",
    "RawRequest",
    "ServedSchema",
    "StoredBackendAdapter",
    "TranslatorError",
    "ValidatedParameters",
    "ValidatedRequest",
    "adapter_from_providers",
    "adapter_from_sources",
    "adapter_from_store",
    "adapter_from_stores",
    "build_served_schema",
    "create_asgi_app",
    "create_index_asgi_app",
    "execute_query",
    "parse_optimade_filter",
    "process",
    "providers_from_registry",
    "serve",
]
