"""Validated configuration and dataset sources for the DSP provider."""

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Self
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
class DspDatasetPublication:
    """Describe how one dataset is advertised and delivered over DSP.

    :param dataset: Protocol-neutral dataset metadata.
    :param offer_id: Stable identifier of the dataset's unconditional offer.
    :param distribution_id: Stable identifier of the dataset's pull distribution.
    :param data_service_id: Stable identifier of the dataset's DSP data service.
    :param data_service_title: Human-readable data-service title.
    :param access_url: HTTPS pull endpoint exposed by the distribution.
    :param data_address: DSP data address returned when this dataset's transfer starts.
    """

    dataset: Dataset
    offer_id: str
    distribution_id: str
    data_service_id: str
    data_service_title: str
    access_url: str
    data_address: Mapping[str, FrozenJsonValue]

    def __post_init__(self) -> None:
        """Validate identifiers and freeze caller-owned JSON containers."""
        object.__setattr__(self, "dataset", Dataset.create(self.dataset))
        for field_name in ("offer_id", "distribution_id", "data_service_id"):
            object.__setattr__(self, field_name, _absolute_iri(field_name, getattr(self, field_name)))
        object.__setattr__(self, "data_service_title", _nonempty("data_service_title", self.data_service_title))
        object.__setattr__(self, "access_url", _https_url("access_url", self.access_url))
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

    @classmethod
    def create(cls, obj: Self | Mapping[str, Any]) -> Self:
        """Return a validated publication from an instance or plain mapping.

        :param obj: Existing publication or mapping with exactly its declared fields.
        :return: Validated publication value.
        :raises TypeError: If ``obj`` is neither a publication nor a mapping.
        :raises ValueError: If a mapping has missing or unknown fields.
        """
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise TypeError(f"expected {cls.__name__} or a mapping")
        if any(not isinstance(name, str) for name in obj):
            raise ValueError("publication mapping keys must be strings")
        names = {field.name for field in fields(cls)}
        missing = names.difference(obj)
        unknown = set(obj).difference(names)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {', '.join(sorted(missing))}")
            if unknown:
                details.append(f"unknown fields: {', '.join(sorted(unknown))}")
            raise ValueError("; ".join(details))
        return cls(**{name: obj[name] for name in names})


class DspDatasetSource(ABC):
    """Supply dataset publications when a :class:`DspProvider` is created.

    Sources are read once and snapshotted into the provider's in-memory
    catalogue. A source can therefore query a store without making catalogue
    requests depend on a live database connection.
    """

    @abstractmethod
    def publications(self) -> Iterable[DspDatasetPublication | Mapping[str, Any]]:
        """Return publication declarations to snapshot into a provider.

        :return: Dataset publication instances or exact publication mappings.
        """
        raise NotImplementedError


@dataclass(frozen=True, slots=True)
class InlineDspDatasetSource(DspDatasetSource):
    """Supply a fixed sequence of inline dataset publications.

    :param datasets: Dataset publication instances or exact mappings.
    """

    datasets: tuple[DspDatasetPublication, ...]

    def __init__(self, datasets: Iterable[DspDatasetPublication | Mapping[str, Any]]) -> None:
        object.__setattr__(self, "datasets", tuple(DspDatasetPublication.create(value) for value in datasets))
        if not self.datasets:
            raise ValueError("datasets must contain at least one publication")

    def publications(self) -> tuple[DspDatasetPublication, ...]:
        """Return the immutable inline publication sequence.

        :return: Configured publications in declaration order.
        """
        return self.datasets


@dataclass(frozen=True, slots=True)
class DcatDataService:
    """Describe an additional public API serving catalogue datasets.

    This is distinct from the DSP access service embedded in each
    distribution. It is emitted only by the owned DCAT-AP projection and does
    not participate in DSP negotiation or transfer processing.

    :param id: Stable identifier of the DCAT data service.
    :param title: Human-readable service title.
    :param endpoint_url: Public HTTPS API endpoint.
    :param conforms_to: Technical-standard IRIs implemented by the service.
    :param serves_dataset_ids: Dataset IDs served, or ``None`` for every dataset
        in the provider's catalogue snapshot.
    :param endpoint_description: Optional IRI describing API operations and parameters.
    """

    id: str
    title: str
    endpoint_url: str
    conforms_to: tuple[str, ...]
    serves_dataset_ids: tuple[str, ...] | None = None
    endpoint_description: str | None = None

    def __post_init__(self) -> None:
        """Validate service identifiers and normalize caller-owned sequences."""
        object.__setattr__(self, "id", _absolute_iri("id", self.id))
        object.__setattr__(self, "title", _nonempty("title", self.title))
        object.__setattr__(self, "endpoint_url", _https_url("endpoint_url", self.endpoint_url))
        conforms_to = _iri_sequence("conforms_to", self.conforms_to)
        if not conforms_to:
            raise ValueError("conforms_to must contain at least one technical-standard IRI")
        object.__setattr__(self, "conforms_to", conforms_to)
        if self.serves_dataset_ids is not None:
            served = _iri_sequence("serves_dataset_ids", self.serves_dataset_ids)
            if not served:
                raise ValueError("serves_dataset_ids must contain at least one dataset IRI or be None")
            object.__setattr__(self, "serves_dataset_ids", served)
        if self.endpoint_description is not None:
            object.__setattr__(
                self,
                "endpoint_description",
                _absolute_iri("endpoint_description", self.endpoint_description),
            )

    @classmethod
    def create(cls, obj: Self | Mapping[str, Any]) -> Self:
        """Return a validated service from an instance or exact mapping.

        :param obj: Existing service or mapping of constructor fields.
        :return: Validated service value.
        :raises TypeError: If ``obj`` is neither a service nor a mapping.
        :raises ValueError: If a mapping has missing or unknown fields.
        """
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise TypeError(f"expected {cls.__name__} or a mapping")
        if any(not isinstance(name, str) for name in obj):
            raise ValueError("data-service mapping keys must be strings")
        required = {"id", "title", "endpoint_url", "conforms_to"}
        optional = {"serves_dataset_ids", "endpoint_description"}
        missing = required.difference(obj)
        unknown = set(obj).difference(required | optional)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append(f"missing fields: {', '.join(sorted(missing))}")
            if unknown:
                details.append(f"unknown fields: {', '.join(sorted(unknown))}")
            raise ValueError("; ".join(details))
        return cls(**{name: obj[name] for name in required | optional if name in obj})


def _iri_sequence(field_name: str, value: object) -> tuple[str, ...]:
    """Normalize a non-string iterable of unique absolute IRIs."""
    if isinstance(value, str):
        raise TypeError(f"{field_name} must be an iterable of IRIs, not one string")
    if not isinstance(value, Iterable):
        raise TypeError(f"{field_name} must be an iterable of IRIs")
    values: tuple[object, ...] = tuple(value)
    normalized = tuple(_absolute_iri(field_name, item) for item in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} must not contain duplicate IRIs")
    return normalized


@dataclass(frozen=True, slots=True)
class DspProviderConfig:
    """Configure the in-memory DSP provider and its dataset catalogue.

    New code normally supplies ``datasets`` or ``dataset_source``. The singular
    publication fields remain supported as a backwards-compatible convenience
    for one-dataset scripts. Exactly one of these three modes must be used.

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
    :param datasets: Inline publications for the preferred multi-dataset form.
    :param dataset_source: Source to snapshot instead of declaring publications inline.
    :param dcat_data_services: Additional public APIs emitted only in the DCAT projection.
    """

    connector_root_url: str
    service_id: str
    participant_id: str
    catalog_id: str
    catalog_title: str
    catalog_description: str
    dataset: Dataset | Mapping[str, Any] | None = None
    offer_id: str | None = None
    distribution_id: str | None = None
    data_service_id: str | None = None
    data_service_title: str | None = None
    access_url: str | None = None
    data_address: Mapping[str, FrozenJsonValue] | None = None
    automatic_progression: bool = True
    datasets: tuple[DspDatasetPublication, ...] = ()
    dataset_source: DspDatasetSource | None = None
    dcat_data_services: tuple[DcatDataService, ...] = ()

    def __post_init__(self) -> None:
        """Validate invariant configuration and freeze caller-owned JSON containers."""
        object.__setattr__(
            self,
            "connector_root_url",
            _https_url("connector_root_url", self.connector_root_url, reject_query=True),
        )
        if self.connector_root_url.endswith("/"):
            raise ValueError("connector_root_url must not have a trailing slash")
        for field_name in ("catalog_title", "catalog_description"):
            object.__setattr__(self, field_name, _nonempty(field_name, getattr(self, field_name)))
        for field_name in ("service_id", "participant_id", "catalog_id"):
            object.__setattr__(self, field_name, _absolute_iri(field_name, getattr(self, field_name)))
        if not isinstance(self.automatic_progression, bool):
            raise TypeError("automatic_progression must be a bool")
        legacy_values = (
            self.dataset,
            self.offer_id,
            self.distribution_id,
            self.data_service_id,
            self.data_service_title,
            self.access_url,
            self.data_address,
        )
        has_legacy = any(value is not None for value in legacy_values)
        if has_legacy and not all(value is not None for value in legacy_values):
            raise ValueError("the singular dataset publication fields must be supplied together")
        if not isinstance(self.datasets, tuple):
            try:
                object.__setattr__(self, "datasets", tuple(self.datasets))
            except TypeError as error:
                raise TypeError("datasets must be an iterable of dataset publications") from error
        inline = tuple(DspDatasetPublication.create(value) for value in self.datasets)
        object.__setattr__(self, "datasets", inline)
        modes = int(has_legacy) + int(bool(inline)) + int(self.dataset_source is not None)
        if modes != 1:
            raise ValueError("configure exactly one of singular dataset fields, datasets, or dataset_source")
        if self.dataset_source is not None and not isinstance(self.dataset_source, DspDatasetSource):
            raise TypeError("dataset_source must be a DspDatasetSource")
        if not isinstance(self.dcat_data_services, tuple):
            try:
                object.__setattr__(self, "dcat_data_services", tuple(self.dcat_data_services))
            except TypeError as error:
                raise TypeError("dcat_data_services must be an iterable of DCAT data services") from error
        dcat_services = tuple(DcatDataService.create(value) for value in self.dcat_data_services)
        if len({service.id for service in dcat_services}) != len(dcat_services):
            raise ValueError("dcat_data_services must have unique identifiers")
        object.__setattr__(self, "dcat_data_services", dcat_services)
        if has_legacy:
            assert self.dataset is not None
            assert self.offer_id is not None
            assert self.distribution_id is not None
            assert self.data_service_id is not None
            assert self.data_service_title is not None
            assert self.access_url is not None
            assert self.data_address is not None
            publication = DspDatasetPublication(
                dataset=Dataset.create(self.dataset),
                offer_id=self.offer_id,
                distribution_id=self.distribution_id,
                data_service_id=self.data_service_id,
                data_service_title=self.data_service_title,
                access_url=self.access_url,
                data_address=self.data_address,
            )
            object.__setattr__(self, "dataset", publication.dataset)
            object.__setattr__(self, "offer_id", publication.offer_id)
            object.__setattr__(self, "distribution_id", publication.distribution_id)
            object.__setattr__(self, "data_service_id", publication.data_service_id)
            object.__setattr__(self, "data_service_title", publication.data_service_title)
            object.__setattr__(self, "access_url", publication.access_url)
            object.__setattr__(self, "data_address", publication.data_address)

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
    "DcatDataService",
    "DspDatasetPublication",
    "DspDatasetSource",
    "DspProviderConfig",
    "InlineDspDatasetSource",
]
