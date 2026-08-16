"""Build OPTIMADE response metadata and collected warning entries."""

import datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any

from httk.core.report import active_collections

from ..model.config import OptimadeConfig

try:
    _implementation_version = version("httk-serve")
except PackageNotFoundError:  # pragma: no cover - only hit when running from a raw source tree
    _implementation_version = "0.0.0"


def merge_collected_warnings(json_response: dict[str, Any]) -> None:
    """Merge the innermost active report collection into response metadata.

    :param json_response: JSON:API document whose ``meta.warnings`` is updated.
    """
    collections = active_collections()
    if not collections:
        return

    meta: dict[str, Any] = json_response.setdefault("meta", {})
    warnings = list(meta.get("warnings") or [])
    for record in collections[-1].records:
        warning: dict[str, Any] = {"type": "warning", "detail": record.getMessage()}
        title = getattr(record, "title", None)
        if isinstance(title, str) and title:
            warning["title"] = title
        warnings.append(warning)

    unique_warnings: list[dict[str, Any]] = []
    seen: set[tuple[Any, Any]] = set()
    for warning in warnings:
        key = (warning.get("title") or None, warning.get("detail"))
        if key not in seen:
            seen.add(key)
            unique_warnings.append(warning)
    if unique_warnings:
        meta["warnings"] = unique_warnings
    else:
        meta.pop("warnings", None)


def generate_meta(
    *,
    representation: str,
    api_version: str,
    config: OptimadeConfig,
    data_returned: int | None = None,
    more_data_available: bool = False,
    data_available: int | None = None,
    warnings: list[dict[str, Any]] | None = None,
    last_id: str | None = None,
) -> dict[str, Any]:
    """Build the OPTIMADE response metadata object.

    :param representation: Request representation recorded in metadata.
    :param api_version: OPTIMADE version used for the response.
    :param config: Service metadata configuration.
    :param data_returned: Total data objects for the current filter query, independent of pagination.
    :param more_data_available: Whether another page is available.
    :param data_available: Total data objects available in the database for the endpoint (unfiltered).
    :param warnings: Warnings already associated with the request.
    :param last_id: Identifier of the final returned entry.
    :return: OPTIMADE metadata mapping.
    """
    implementation = {
        "name": "httk-serve",
        "version": _implementation_version,
        "homepage": "https://httk.org/",
        "source_url": "https://github.com/httk/httk-serve",
        "issue_tracker": "https://github.com/httk/httk-serve/issues",
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
    if data_returned is not None:
        meta['data_returned'] = data_returned
    if data_available is not None:
        meta['data_available'] = data_available
    if warnings:
        meta['warnings'] = warnings
    if last_id is not None:
        meta['last_id'] = last_id
    return meta
