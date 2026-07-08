import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from ..model.config import OptimadeConfig

try:
    _implementation_version = version("httk-optimade")
except PackageNotFoundError:  # pragma: no cover - only hit when running from a raw source tree
    _implementation_version = "0.0.0"


def generate_meta(
    *,
    representation: str,
    api_version: str,
    config: OptimadeConfig,
    data_count: int | None = None,
    more_data_available: bool = False,
    data_available: int | None = None,
    warnings: list[dict[str, Any]] | None = None,
    last_id: str | None = None,
) -> dict[str, Any]:
    implementation = {
        "name": "httk-optimade",
        "version": _implementation_version,
        "homepage": "https://httk.org/",
        "source_url": "https://github.com/httk/httk-optimade",
        "issue_tracker": "https://github.com/httk/httk-optimade/issues",
    }
    implementation.update(config.implementation)

    meta: dict[str, Any] = {
        "query": {
            "representation": representation,
        },
        "api_version": api_version,
        "time_stamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "more_data_available": more_data_available,
        "implementation": implementation,
        "provider": config.provider,
    }
    if config.schema_url is not None:
        meta['schema'] = config.schema_url
    if config.database is not None:
        meta['database'] = config.database
    if config.request_delay is not None:
        meta['request_delay'] = config.request_delay
    if data_count is not None:
        meta['data_returned'] = data_count
    if data_available is not None:
        meta['data_available'] = data_available
    if warnings:
        meta['warnings'] = warnings
    if last_id is not None:
        meta['last_id'] = last_id
    return meta
