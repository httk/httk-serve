"""Public generic OPTIMADE serving and query APIs."""

from httk.core.optimade import ParserError, ParserSyntaxError, parse_optimade_filter

from .api import create_asgi_app, create_index_asgi_app, serve
from .backend import (
    BackendAdapter,
    EntrySource,
    InMemoryStore,
    StoredBackendAdapter,
    adapter_from_providers,
    adapter_from_store,
    adapter_from_stores,
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

__all__ = [
    "BackendAdapter",
    "EndpointResponse",
    "EntrySource",
    "InMemoryStore",
    "OptimadeConfig",
    "OptimadeError",
    "OptimadeIndexConfig",
    "ParserError",
    "ParserSyntaxError",
    "RawRequest",
    "StoredBackendAdapter",
    "TranslatorError",
    "ValidatedParameters",
    "ValidatedRequest",
    "adapter_from_providers",
    "adapter_from_store",
    "adapter_from_stores",
    "create_asgi_app",
    "create_index_asgi_app",
    "parse_optimade_filter",
    "process",
    "providers_from_registry",
    "serve",
]
