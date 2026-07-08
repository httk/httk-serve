from dataclasses import dataclass, field
from typing import Any


def _default_provider() -> dict[str, Any]:
    return {
        "name": "httk",
        "description": (
            "This is a database hosted with the High-Throughput Toolkit (httk), "
            "for which the hoster has not specifically configured the provider."
        ),
        "prefix": "httk",
    }


@dataclass
class OptimadeConfig:
    """Configuration of a served OPTIMADE database.

    ``data_available`` is filled in by ``process_init`` with the number of
    available entries per entry endpoint.
    """

    provider: dict[str, Any] = field(default_factory=_default_provider)
    links: list[dict[str, Any]] = field(default_factory=list)
    data_available: dict[str, int] = field(default_factory=dict)
