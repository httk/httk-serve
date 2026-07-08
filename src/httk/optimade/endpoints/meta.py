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
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "query": {
            "representation": representation,
        },
        "api_version": api_version,
        "time_stamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "more_data_available": more_data_available,
        "implementation": {
            "name": "httk",
            "version": _implementation_version,
            "homepage": "https://httk.org/",
        },
        "provider": config.provider,
    }
    if data_count is not None:
        meta['data_returned'] = data_count
    if data_available is not None:
        meta['data_available'] = data_available
    return meta
