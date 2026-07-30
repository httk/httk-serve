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

    ``implementation`` extends/overrides the fields of the ``meta`` ->
    ``implementation`` dictionary (e.g. ``issue_tracker``, ``source_url``,
    ``maintainer``). ``database``, ``schema_url``, and ``request_delay``
    populate the corresponding optional ``meta`` fields (OPTIMADE v1.2+)
    when set. ``license``, ``available_licenses``, and
    ``available_licenses_for_entries`` populate the corresponding optional
    base-info attributes when set. ``data_available`` is filled in by
    ``process_init`` with the number of available entries per entry endpoint.
    """

    provider: dict[str, Any] = field(default_factory=_default_provider)
    links: list[dict[str, Any]] = field(default_factory=list)
    implementation: dict[str, Any] = field(default_factory=dict)
    database: dict[str, Any] | None = None
    schema_url: str | None = None
    request_delay: float | None = None
    license: dict[str, Any] | str | None = None
    available_licenses: list[str] | None = None
    available_licenses_for_entries: list[str] | None = None
    data_available: dict[str, int] = field(default_factory=dict)
    partial_data_chunk_size: int = 1000
    cors_origins: tuple[str, ...] = ()
