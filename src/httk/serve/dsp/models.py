"""Immutable records and protocol errors for the Data Space Protocol provider."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Literal

from httk.core import Dataset

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
type FrozenJsonValue = JsonScalar | tuple["FrozenJsonValue", ...] | Mapping[str, "FrozenJsonValue"]
type ErrorKind = Literal["catalog", "negotiation", "transfer"]

DSP_CONTEXT = "https://w3id.org/dspace/2025/1/context.jsonld"
"""Protected official DSP JSON-LD context required by the 2025-1 schemas."""


def freeze_json(value: object) -> FrozenJsonValue:
    """Freeze a JSON-compatible value without retaining caller-owned containers.

    :param value: JSON-compatible value to copy into an immutable representation.
    :return: An immutable JSON-compatible value.
    :raises TypeError: If ``value`` is not JSON-compatible.
    :raises ValueError: If a floating-point value is non-finite.
    """
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("JSON floats must be finite")
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, FrozenJsonValue] = {}
        for key, child in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            frozen[key] = freeze_json(child)
        return MappingProxyType(frozen)
    if isinstance(value, list | tuple):
        return tuple(freeze_json(child) for child in value)
    raise TypeError(f"Expected a JSON-compatible value, got {type(value).__name__}")


def thaw_json(value: FrozenJsonValue) -> JsonValue:
    """Return an independent ordinary JSON value from an immutable snapshot.

    :param value: Immutable JSON-compatible value to copy.
    :return: Plain JSON-compatible lists and dictionaries.
    """
    if isinstance(value, Mapping):
        return {key: thaw_json(child) for key, child in value.items()}
    if isinstance(value, tuple):
        return [thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class DeliveryStatus:
    """Describe delivery health without claiming an unacknowledged DSP transition.

    :param last_error: Most recent callback failure, if any.
    :param retry_count: Number of delivery attempts made for the last callback.
    :param out_of_sync: Whether the remote peer may not have the acknowledged local state.
    """

    last_error: str | None = None
    retry_count: int = 0
    out_of_sync: bool = False


@dataclass(frozen=True, slots=True)
class OfferProfile:
    """Describe the one static, unconditional offer exposed by the provider.

    :param id: Stable offer identifier.
    :param target: Dataset identifier to which a message offer must refer.
    """

    id: str
    target: str


@dataclass(frozen=True, slots=True)
class DataServiceProfile:
    """Describe the single service through which the dataset is delivered.

    :param id: Stable data-service identifier.
    :param title: Human-readable service title.
    :param endpoint_url: HTTPS endpoint used for data delivery.
    """

    id: str
    title: str
    endpoint_url: str
    conforms_to: tuple[str, ...]
    serves_dataset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DcatDataServiceProfile:
    """Describe a public API included only in the owned DCAT projection.

    :param id: Stable service identifier.
    :param title: Human-readable service title.
    :param endpoint_url: Public HTTPS API endpoint.
    :param conforms_to: Technical standards implemented by the service.
    :param serves_dataset_ids: Catalogue dataset identifiers served by the API.
    :param endpoint_description: Optional IRI describing the API interface.
    """

    id: str
    title: str
    endpoint_url: str
    conforms_to: tuple[str, ...]
    serves_dataset_ids: tuple[str, ...]
    endpoint_description: str | None


@dataclass(frozen=True, slots=True)
class DistributionProfile:
    """Describe the one pull distribution for the provider dataset.

    :param id: Stable distribution identifier.
    :param format: DSP transfer format advertised for the distribution.
    :param access_url: HTTPS URL from which data are pulled.
    :param data_service: Embedded service description for DSP catalogue output.
    """

    id: str
    format: str
    file_format: str
    media_type: str
    access_url: str
    data_service: DataServiceProfile
    byte_size: int | None = None
    sha256: str | None = None


@dataclass(frozen=True, slots=True)
class DatasetProfile:
    """Group one dataset with its DSP offer, distribution, and data address.

    :param dataset: Protocol-neutral dataset metadata.
    :param offer: Unconditional ODRL use offer for this dataset.
    :param distribution: Pull distribution advertised for this dataset.
    :param data_service: Service embedded in the distribution.
    :param data_address: Immutable pull address returned for authorized transfers.
    """

    dataset: Dataset
    offer: OfferProfile
    distribution: DistributionProfile
    data_service: DataServiceProfile
    data_address: Mapping[str, FrozenJsonValue]


@dataclass(frozen=True, slots=True)
class CatalogueProfile:
    """Describe the immutable multi-dataset catalogue served by this provider.

    :param id: Stable catalogue identifier.
    :param title: Human-readable catalogue title.
    :param description: Human-readable catalogue description.
    :param participant_id: Provider participant identifier.
    :param dcat_ap_profile: Configured minimal DCAT-AP profile IRI.
    :param datasets: Dataset publication profiles in stable declaration order.
    :param dcat_data_services: Additional public APIs for the DCAT projection.
    """

    id: str
    title: str
    description: str
    participant_id: str
    dcat_ap_profile: str
    datasets: tuple[DatasetProfile, ...]
    dcat_data_services: tuple[DcatDataServiceProfile, ...]


@dataclass(frozen=True, slots=True)
class AgreementRecord:
    """Record the provider-created agreement associated with a negotiation.

    :param id: Unique agreement identifier in ``urn:uuid:`` form.
    :param policy: Immutable agreement policy JSON.
    :param target: Dataset identifier covered by the agreement.
    :param assigner: Provider participant identifier.
    :param assignee: Consumer participant identifier.
    :param timestamp: UTC XML Schema date-time at which the agreement was created.
    """

    id: str
    policy: Mapping[str, FrozenJsonValue]
    target: str
    assigner: str
    assignee: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class NegotiationRecord:
    """Record an in-memory contract negotiation and its acknowledged state.

    :param provider_pid: Provider process identifier.
    :param consumer_pid: Consumer process identifier.
    :param callback_address: Consumer callback base URL.
    :param state: Last state acknowledged by both protocol processing and callback delivery.
    :param policy: Immutable message offer accepted for the negotiation.
    :param agreement: Created agreement after an agreement callback is acknowledged.
    :param pending_transition: Reserved transition token, if a callback is currently in flight.
    :param delivery: Local delivery health for the latest callback.
    """

    provider_pid: str
    consumer_pid: str
    callback_address: str
    state: str
    policy: Mapping[str, FrozenJsonValue]
    agreement: AgreementRecord | None = None
    pending_transition: str | None = None
    delivery: DeliveryStatus = field(default_factory=DeliveryStatus)


@dataclass(frozen=True, slots=True)
class TransferRecord:
    """Record an in-memory transfer process and its acknowledged state.

    :param provider_pid: Provider transfer-process identifier.
    :param consumer_pid: Consumer transfer-process identifier.
    :param callback_address: Consumer callback base URL.
    :param agreement_id: Finalized agreement authorizing this transfer.
    :param format: Requested transfer format.
    :param state: Last state acknowledged by both protocol processing and callback delivery.
    :param pending_transition: Reserved transition token, if a callback is currently in flight.
    :param delivery: Local delivery health for the latest callback.
    """

    provider_pid: str
    consumer_pid: str
    callback_address: str
    agreement_id: str
    format: str
    state: str
    pending_transition: str | None = None
    delivery: DeliveryStatus = field(default_factory=DeliveryStatus)


class DspProtocolError(Exception):
    """Represent a protocol failure that an HTTP adapter can serialize directly.

    :param kind: DSP area whose official error document must be emitted.
    :param status_code: HTTP status suitable for the adapter response.
    :param detail: Safe human-readable failure detail.
    :param code: Optional machine-readable DSP error code.
    :param provider_pid: Provider process identifier, when one is known.
    :param consumer_pid: Consumer process identifier, when one is known.
    """

    def __init__(
        self,
        kind: ErrorKind,
        status_code: int,
        detail: str,
        *,
        code: str | None = None,
        provider_pid: str | None = None,
        consumer_pid: str | None = None,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.status_code = status_code
        self.detail = detail
        self.code = code
        self.provider_pid = provider_pid
        self.consumer_pid = consumer_pid

    def as_document(self) -> dict[str, JsonValue]:
        """Serialize this failure as the official DSP JSON error document.

        :return: Error document for the exception's DSP area.
        """
        type_name = {
            "catalog": "CatalogError",
            "negotiation": "ContractNegotiationError",
            "transfer": "TransferError",
        }[self.kind]
        document: dict[str, JsonValue] = {
            "@context": [DSP_CONTEXT],
            "@type": type_name,
            "reason": [self.detail],
        }
        if self.code is not None:
            document["code"] = self.code
        if self.provider_pid is not None:
            document["providerPid"] = self.provider_pid
        if self.consumer_pid is not None:
            document["consumerPid"] = self.consumer_pid
        return document


__all__ = [
    "AgreementRecord",
    "CatalogueProfile",
    "DataServiceProfile",
    "DatasetProfile",
    "DcatDataServiceProfile",
    "DeliveryStatus",
    "DistributionProfile",
    "DspProtocolError",
    "ErrorKind",
    "FrozenJsonValue",
    "JsonValue",
    "NegotiationRecord",
    "OfferProfile",
    "TransferRecord",
    "freeze_json",
    "thaw_json",
]
