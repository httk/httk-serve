"""Public helpers for creating and running generic OPTIMADE applications."""

import logging
from collections.abc import Mapping

from httk.core.report import configure_reporting
from httk.store import EntryStore
from starlette.applications import Starlette

from .model.config import OptimadeConfig, OptimadeIndexConfig
from .model.results import OptimadeAdapter
from .runtime.asgi import create_app
from .runtime.devserver import run_dev_server
from .schema.served import build_served_schema


def create_asgi_app(
    adapter: OptimadeAdapter | EntryStore,
    config: OptimadeConfig | None = None,
    *,
    baseurl: str | None = None,
    debug: bool = False,
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
) -> Starlette:
    """Create an ASGI application serving an OPTIMADE API for a backend or store.

    An absent ``baseurl`` makes the application derive a mount-aware URL from
    the request. An explicit value is authoritative.

    :param adapter: Backend providing the served schema/query callback, or an
        entry store whose configured OPTIMADE families are discovered lazily.
    :param config: Optional service configuration.
    :param baseurl: Public API base URL, or ``None`` for request-based derivation.
    :param debug: Enable application and backend diagnostics.
    :param report_level: Minimum report level collected per request.
    :param report_context_levels: Context-specific report levels.
    :return: Configured Starlette ASGI application.
    """
    if isinstance(adapter, EntryStore):
        from .backend.stores import adapter_from_store

        adapter = adapter_from_store(adapter)
    if config is None:
        config = OptimadeConfig()
    if isinstance(config, OptimadeIndexConfig):
        raise TypeError("create_asgi_app does not accept OptimadeIndexConfig; use create_index_asgi_app")
    return create_app(
        query_function=adapter.query_function(),
        config=config,
        schema=adapter.schema,
        snapshot_cutoff_ns=getattr(adapter, "snapshot_cutoff_ns", None),
        baseurl=baseurl,
        debug=debug,
        report_level=report_level,
        report_context_levels=report_context_levels,
    )


def create_index_asgi_app(
    config: OptimadeIndexConfig,
    *,
    baseurl: str | None = None,
    debug: bool = False,
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
) -> Starlette:
    """Create an ASGI application for an OPTIMADE index meta-database.

    The index serves only discovery, links, and unversioned version
    negotiation. It has no backend adapter and performs no query calls. The
    supplied configuration is retained as the response metadata source; the
    composed application's caller owns its lifetime and configuration.

    :param config: Validated index metadata and configured database links.
    :param baseurl: Authoritative public index URL, or ``None`` for
        mount-aware derivation from each request.
    :param debug: Enable Starlette diagnostics.
    :param report_level: Minimum report level collected per request.
    :param report_context_levels: Context-specific report levels.
    :return: Configured Starlette ASGI application.
    :raises TypeError: If ``config`` is not an
        :class:`~httk.serve.optimade.model.config.OptimadeIndexConfig`.
    """
    if not isinstance(config, OptimadeIndexConfig):
        raise TypeError("create_index_asgi_app requires an OptimadeIndexConfig")

    def no_query(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("the OPTIMADE index must not execute queries")

    return create_app(
        query_function=no_query,  # type: ignore[arg-type]
        config=config,
        schema=build_served_schema({}),
        baseurl=baseurl,
        debug=debug,
        report_level=report_level,
        report_context_levels=report_context_levels,
    )


def serve(
    adapter: OptimadeAdapter | EntryStore,
    config: OptimadeConfig | None = None,
    *,
    host: str = "127.0.0.1",
    port: int = 8080,
    baseurl: str | None = None,
    debug: bool = False,
    report_level: str | int = "warning",
    report_context_levels: Mapping[str, str | int] | None = None,
) -> None:
    """Serve an OPTIMADE API for a backend or entry store with a development server.

    :param adapter: Backend providing the served schema/query callback, or an
        entry store whose configured OPTIMADE families are discovered lazily.
    :param config: Optional service configuration.
    :param host: Interface or hostname to bind.
    :param port: TCP port to bind.
    :param baseurl: Public API base URL, or ``None`` to derive the local URL.
    :param debug: Enable application and backend diagnostics.
    :param report_level: Minimum report level collected per request.
    :param report_context_levels: Context-specific report levels.
    """
    if baseurl is None:
        baseurl = f"http://{host}:{port}/" if port != 80 else f"http://{host}/"
    # Unlike create_app (embedders own logging), the development server IS the
    # host process: give diagnostics a console unless one is already configured.
    if not logging.getLogger("httk").handlers and not logging.getLogger().handlers:
        configure_reporting()
    if isinstance(config, OptimadeIndexConfig):
        app = create_index_asgi_app(
            config,
            baseurl=baseurl,
            debug=debug,
            report_level=report_level,
            report_context_levels=report_context_levels,
        )
    else:
        app = create_asgi_app(
            adapter,
            config,
            baseurl=baseurl,
            debug=debug,
            report_level=report_level,
            report_context_levels=report_context_levels,
        )
    run_dev_server(app=app, host=host, port=port)
