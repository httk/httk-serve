from collections.abc import Mapping
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
    """Configure a served OPTIMADE database.

    ``implementation`` extends/overrides the fields of the ``meta`` ->
    ``implementation`` dictionary (e.g. ``issue_tracker``, ``source_url``,
    ``maintainer``). ``database``, ``schema_url``, and ``request_delay``
    populate the corresponding optional ``meta`` fields (OPTIMADE v1.2+)
    when set. ``license``, ``available_licenses``, and
    ``available_licenses_for_entries`` populate the corresponding optional
    base-info attributes when set.

    :param provider: Provider metadata for the OPTIMADE response envelope.
    :param links: Provider links exposed by the ``/links`` endpoint.
    :param implementation: Implementation metadata merged into response metadata.
    :param database: Optional database metadata for response metadata.
    :param schema_url: URL of the served schema, when one is available.
    :param request_delay: Optional advertised request delay.
    :param license: License metadata exposed by the base-info endpoint.
    :param available_licenses: Licenses advertised for the service.
    :param available_licenses_for_entries: Licenses advertised for entries.
    :param partial_data_chunk_size: Number of outer items emitted per partial-data page.
    :param cors_origins: Exact browser origins allowed to make cross-origin requests.
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
    partial_data_chunk_size: int = 1000
    cors_origins: tuple[str, ...] = ()


@dataclass
class OptimadeIndexConfig(OptimadeConfig):
    """Configure an OPTIMADE index meta-database.

    The links are the configured databases advertised by the index. Exactly
    one must have ``link_type == "root"``; child links are the databases that
    may be selected as the index's default relationship. The regular
    :class:`OptimadeConfig` remains a non-index service configuration.

    :param default_link_id: Identifier of the default configured child link,
        or ``None`` when the index has no default.
    :raises ValueError: If configured links do not satisfy the links schema or
        the root/default-link constraints.
    """

    default_link_id: str | None = None

    @staticmethod
    def _validate_link_value(link: dict[str, Any], link_id: str, key: str) -> None:
        """Validate one required string or JSON:API link-object value."""
        value = link[key]
        if isinstance(value, str):
            if not value:
                raise ValueError(f"index link {link_id!r} has an empty {key}")
            return
        if not isinstance(value, Mapping):
            raise ValueError(f"index link {link_id!r} has an invalid {key}")
        href = value.get("href")
        if not isinstance(href, str) or not href:
            raise ValueError(f"index link {link_id!r} has an invalid {key}.href")
        if "meta" in value and not isinstance(value["meta"], Mapping):
            raise ValueError(f"index link {link_id!r} has an invalid {key}.meta")

    def __post_init__(self) -> None:
        seen: set[str] = set()
        root_count = 0
        child_ids: set[str] = set()
        required = ("name", "description", "base_url", "homepage", "link_type")
        allowed_link_types = {"root", "child", "external", "providers"}

        for link in self.links:
            if not isinstance(link, dict):
                raise ValueError("index links must be dictionaries")
            link_id = link.get("id")
            if not isinstance(link_id, str) or not link_id:
                raise ValueError("index link ids must be non-empty strings")
            if link_id in seen:
                raise ValueError(f"duplicate index link id: {link_id}")
            seen.add(link_id)
            missing = [key for key in required if key not in link]
            if missing:
                raise ValueError(f"index link {link_id!r} is missing: {', '.join(missing)}")
            for key in ("name", "description"):
                if not isinstance(link[key], str):
                    raise ValueError(f"index link {link_id!r} has a non-string {key}")
            self._validate_link_value(link, link_id, "base_url")
            self._validate_link_value(link, link_id, "homepage")
            link_type = link["link_type"]
            if not isinstance(link_type, str) or link_type not in allowed_link_types:
                raise ValueError(f"unsupported index link_type for {link_id!r}: {link_type!r}")
            if link_type == "root":
                root_count += 1
            elif link_type == "child":
                child_ids.add(link_id)

        if "optimade" in seen:
            raise ValueError("index link id 'optimade' is reserved for the automatic providers link")
        if root_count != 1:
            raise ValueError("an index configuration must contain exactly one root link")
        if self.default_link_id is not None and (
            not isinstance(self.default_link_id, str) or self.default_link_id not in child_ids
        ):
            raise ValueError("default_link_id must reference a configured child link")
