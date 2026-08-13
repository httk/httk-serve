"""Validated configuration for a fixed Data Space Protocol provider."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from httk.core import Dataset

from .models import DSP_CONTEXT, FrozenJsonValue, freeze_json, thaw_json
from .validation import DspSchemaError, validate_document

DSP_VERSION = "2025-1"
"""Implemented Data Space Protocol version."""

DSP_TRANSFER_FORMAT = "HttpData-PULL"
"""The sole data-transfer format exposed by this provider."""

DCAT_FILE_FORMAT = "http://publications.europa.eu/resource/authority/file-type/JSON_LD"
"""EU Publications Office IRI for the JSON-LD DCAT distribution format."""

HTTP_ENDPOINT_TYPE = "https://w3id.org/idsa/v4.1/HTTP"
"""Official DSP endpoint type used by the configured pull data address."""


def _nonempty(field_name: str, value: object) -> str:
    """Return a required text setting after checking its basic shape."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _https_url(field_name: str, value: object, *, reject_query: bool = False) -> str:
    """Return an absolute HTTPS URL after rejecting ambiguous URL forms."""
    text = _nonempty(field_name, value)
    if not _plain_iri_characters(text):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError as error:
        raise ValueError(f"{field_name} must be an absolute HTTPS URL") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or (reject_query and parsed.query)
        or parsed.fragment
    ):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    if not _valid_percent_escapes(text):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    return text


def _absolute_iri(field_name: str, value: object) -> str:
    """Return a non-empty absolute IRI suitable for a DSP identity field."""
    text = _nonempty(field_name, value)
    if not _plain_iri_characters(text) or not _valid_percent_escapes(text):
        raise ValueError(f"{field_name} must be an absolute IRI")
    try:
        parsed = urlsplit(text)
    except ValueError as error:
        raise ValueError(f"{field_name} must be an absolute IRI") from error
    if not parsed.scheme:
        raise ValueError(f"{field_name} must be an absolute IRI")
    return text


def _plain_iri_characters(value: str) -> bool:
    """Return whether an IRI has no raw forbidden control or delimiter characters."""
    return not any(
        character.isspace()
        or ord(character) < 32
        or 127 <= ord(character) <= 159
        or 0xD800 <= ord(character) <= 0xDFFF
        or character in '<>"{}|\\^`'
        for character in value
    )


def _valid_percent_escapes(value: str) -> bool:
    """Return whether every percent character begins a valid encoded byte."""
    hexdigits = frozenset("0123456789abcdefABCDEF")
    return all(
        value[index] != "%"
        or index + 2 < len(value)
        and value[index + 1] in hexdigits
        and value[index + 2] in hexdigits
        for index in range(len(value))
    )


@dataclass(frozen=True, slots=True)
class DspProviderConfig:
    """Configure the deliberately small, fixed-catalogue DSP provider.

    The provider accepts exactly one dataset, exposes one unconditional
    ``use`` offer, and provides a single HTTPS pull distribution.  The data
    address is copied into an immutable JSON snapshot after validation, so a
    caller cannot mutate runtime configuration by retaining its source mapping.

    :param connector_root_url: HTTPS root advertised by the connector version document.
    :param service_id: Stable DSP service identifier.
    :param participant_id: Provider participant identifier used as agreement assigner.
    :param catalog_id: Stable catalogue identifier.
    :param catalog_title: Human-readable catalogue title.
    :param catalog_description: Human-readable catalogue description.
    :param dataset: Dataset metadata instance or exact dataset mapping.
    :param offer_id: Stable identifier of the sole advertised offer.
    :param distribution_id: Stable identifier of the sole distribution.
    :param data_service_id: Stable identifier of the sole data service.
    :param data_service_title: Human-readable data-service title.
    :param access_url: HTTPS pull endpoint exposed by the distribution.
    :param data_address: Official-shape HTTPS pull data address sent on transfer start.
    :param automatic_progression: Whether valid inbound requests immediately send required callbacks.
    """

    connector_root_url: str
    service_id: str
    participant_id: str
    catalog_id: str
    catalog_title: str
    catalog_description: str
    dataset: Dataset | Mapping[str, Any]
    offer_id: str
    distribution_id: str
    data_service_id: str
    data_service_title: str
    access_url: str
    data_address: Mapping[str, FrozenJsonValue]
    automatic_progression: bool = True

    def __post_init__(self) -> None:
        """Validate invariant configuration and freeze caller-owned JSON containers."""
        object.__setattr__(
            self,
            "connector_root_url",
            _https_url("connector_root_url", self.connector_root_url, reject_query=True),
        )
        if self.connector_root_url.endswith("/"):
            raise ValueError("connector_root_url must not have a trailing slash")
        for field_name in (
            "catalog_title",
            "catalog_description",
            "data_service_title",
        ):
            object.__setattr__(self, field_name, _nonempty(field_name, getattr(self, field_name)))
        for field_name in (
            "service_id",
            "participant_id",
            "catalog_id",
            "offer_id",
            "distribution_id",
            "data_service_id",
        ):
            object.__setattr__(self, field_name, _absolute_iri(field_name, getattr(self, field_name)))
        object.__setattr__(self, "access_url", _https_url("access_url", self.access_url))
        object.__setattr__(self, "dataset", Dataset.create(self.dataset))
        if not isinstance(self.automatic_progression, bool):
            raise TypeError("automatic_progression must be a bool")
        if not isinstance(self.data_address, Mapping):
            raise TypeError("data_address must be a mapping")
        frozen = freeze_json(self.data_address)
        if not isinstance(frozen, Mapping):
            raise TypeError("data_address must be a JSON object")
        data_address = MappingProxyType(dict(frozen))
        document = thaw_json(data_address)
        try:
            validate_document("https://w3id.org/dspace/2025/1/transfer/data-address-schema.json", document)
        except DspSchemaError as error:
            raise ValueError(f"data_address does not satisfy the official DSP shape: {error}") from error
        if data_address.get("@type") != "DataAddress":
            raise ValueError("data_address must have @type 'DataAddress'")
        if data_address.get("endpointType") != HTTP_ENDPOINT_TYPE:
            raise ValueError("data_address.endpointType must be the official HTTP endpoint type")
        endpoint = data_address.get("endpoint")
        if not isinstance(endpoint, str):
            raise ValueError("data_address.endpoint must be an HTTPS URL")
        _https_url("data_address.endpoint", endpoint)
        if endpoint != self.access_url:
            raise ValueError("data_address.endpoint must equal access_url")
        object.__setattr__(self, "data_address", data_address)

    @property
    def service_endpoint_url(self) -> str:
        """Return the absolute 2025-1 service endpoint derived from the connector root.

        :return: Absolute endpoint root for the implemented DSP protocol version.
        """
        return f"{self.connector_root_url}/{DSP_VERSION}"


__all__ = [
    "DCAT_FILE_FORMAT",
    "DSP_CONTEXT",
    "DSP_TRANSFER_FORMAT",
    "DSP_VERSION",
    "HTTP_ENDPOINT_TYPE",
    "DspProviderConfig",
]
