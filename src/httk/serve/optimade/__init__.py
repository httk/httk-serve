"""Public generic OPTIMADE serving, client, and query APIs."""

from httk.core.optimade import ParserError, ParserSyntaxError, parse_optimade_filter

from .api import create_asgi_app, serve
from .backend import (
    BackendAdapter,
    EntrySource,
    InMemoryStore,
    StoredBackendAdapter,
    adapter_from_providers,
    adapter_from_stores,
    providers_from_registry,
)
from .client import (
    ALL_ADVERTISED,
    OptimadeClientError,
    OptimadeDiscoveryError,
    OptimadeErrorDocumentError,
    OptimadeHTTPError,
    OptimadeStore,
    OptimadeTransportError,
    OptimadeVersionNegotiationError,
    RemoteEntryType,
)
from .engine.processing import process, process_init
from .model import (
    EndpointResponse,
    OptimadeConfig,
    OptimadeError,
    RawRequest,
    TranslatorError,
    ValidatedParameters,
    ValidatedRequest,
)
from .remote_query import (
    CountUnavailableError,
    OptimadePaginationError,
    OptimadeResponseError,
    RemoteResultColumn,
    RemoteResultSet,
    RemoteSearcher,
)

__all__ = [
    "ALL_ADVERTISED",
    "BackendAdapter",
    "CountUnavailableError",
    "EndpointResponse",
    "EntrySource",
    "InMemoryStore",
    "OptimadeClientError",
    "OptimadeConfig",
    "OptimadeDiscoveryError",
    "OptimadeError",
    "OptimadeErrorDocumentError",
    "OptimadeHTTPError",
    "OptimadePaginationError",
    "OptimadeResponseError",
    "OptimadeStore",
    "OptimadeTransportError",
    "OptimadeVersionNegotiationError",
    "ParserError",
    "ParserSyntaxError",
    "RawRequest",
    "RemoteEntryType",
    "RemoteResultColumn",
    "RemoteResultSet",
    "RemoteSearcher",
    "StoredBackendAdapter",
    "TranslatorError",
    "ValidatedParameters",
    "ValidatedRequest",
    "adapter_from_providers",
    "adapter_from_stores",
    "create_asgi_app",
    "parse_optimade_filter",
    "process",
    "process_init",
    "providers_from_registry",
    "serve",
]
