"""Public catalogue-policy seam and the built-in DSP minimal policy."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .config import (
    DCAT_AP_3_0_1_PROFILE,
    DSP_2025_1_SPECIFICATION,
    HTTP_ENDPOINT_TYPE,
    DspProviderConfig,
    DspPublicationRecord,
    _https_url,
)
from .models import (
    CatalogueProfile,
    DataServiceProfile,
    DatasetProfile,
    DcatDataServiceProfile,
    DistributionProfile,
    DspProtocolError,
    JsonValue,
    OfferProfile,
)
from .serializers import (
    offer_policy,
    serialize_dcat_catalogue,
    serialize_dsp_catalogue,
    serialize_dsp_dataset_document,
)

DCAT_PROFILE = "https://semiceu.github.io/DCAT-AP/releases/3.0.1/"
"""DCAT-AP profile parameter used by the built-in alternate representation."""

DCAT_MEDIA_TYPE = f'application/ld+json; profile="{DCAT_PROFILE}"'
"""Full media type of the built-in alternate catalogue representation."""

_QVALUE = re.compile(r"(?:0(?:\.[0-9]{0,3})?|1(?:\.0{0,3})?)\Z")
_HEADER_NAME = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+\Z")


@dataclass(frozen=True, slots=True)
class DspCatalogueRepresentation:
    """Describe one selected HTTP representation of a catalogue response.

    :param media_type: Exact response media type declared by the DSP OpenAPI contract.
    :param alternate: Whether the policy should render its alternate catalogue projection.
    :param headers: Additional response headers as immutable name-value pairs.
    """

    media_type: str
    alternate: bool = False
    headers: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        if (
            not isinstance(self.media_type, str)
            or "/" not in self.media_type
            or not self.media_type.strip()
            or "\r" in self.media_type
            or "\n" in self.media_type
        ):
            raise ValueError("catalogue representation media_type must be a non-empty media type")
        names: set[str] = set()
        for name, value in self.headers:
            if (
                not isinstance(name, str)
                or _HEADER_NAME.fullmatch(name) is None
                or not isinstance(value, str)
                or not value.strip()
                or "\r" in value
                or "\n" in value
            ):
                raise ValueError("catalogue representation headers must contain non-empty strings")
            normalized = name.lower()
            if normalized in names:
                raise ValueError("catalogue representation header names must be unique")
            names.add(normalized)


@runtime_checkable
class DspCataloguePolicy(Protocol):
    """Define the replaceable publication-profile part of a DSP provider.

    Implementations own catalogue snapshot requirements, catalogue and dataset
    serialization, offer serialization, filter policy, and selection of an
    optional alternate catalogue representation. The provider continues to own
    live publication retrieval and all DSP negotiation and transfer mechanics.
    """

    def build_profile(
        self,
        config: DspProviderConfig,
        publications: tuple[DspPublicationRecord, ...],
    ) -> CatalogueProfile:
        """Build and validate one immutable live catalogue snapshot."""
        ...

    def validate_catalogue_request(
        self,
        config: DspProviderConfig,
        message: Mapping[str, object],
    ) -> None:
        """Validate profile-specific catalogue request constraints."""
        ...

    def select_catalogue_representation(
        self,
        config: DspProviderConfig,
        accept: str | None,
    ) -> DspCatalogueRepresentation:
        """Select the catalogue HTTP representation for an Accept field."""
        ...

    def serialize_catalogue(
        self,
        profile: CatalogueProfile,
        *,
        alternate: bool,
    ) -> dict[str, JsonValue]:
        """Serialize one catalogue snapshot in the selected projection."""
        ...

    def serialize_dataset(self, profile: DatasetProfile) -> dict[str, JsonValue]:
        """Serialize one dataset root response."""
        ...

    def serialize_offer(self, offer: OfferProfile, *, include_target: bool) -> dict[str, JsonValue]:
        """Serialize an offer consistently for catalogues and negotiations."""
        ...


@dataclass(frozen=True, slots=True)
class _AcceptRange:
    major: str
    minor: str
    parameters: tuple[tuple[str, str], ...]
    quality: float


def _split_quoted(value: str, delimiter: str) -> tuple[str, ...] | None:
    parts: list[str] = []
    current: list[str] = []
    quoted = False
    escaped = False
    for character in value:
        if escaped:
            current.append(character)
            escaped = False
        elif quoted and character == "\\":
            current.append(character)
            escaped = True
        elif character == '"':
            current.append(character)
            quoted = not quoted
        elif character == delimiter and not quoted:
            parts.append("".join(current))
            current = []
        else:
            current.append(character)
    if quoted or escaped:
        return None
    parts.append("".join(current))
    return tuple(parts)


def _parameter_value(value: str) -> str | None:
    value = value.strip()
    if not value:
        return None
    if not value.startswith('"'):
        return None if '"' in value else value
    if len(value) < 2 or not value.endswith('"'):
        return None
    decoded: list[str] = []
    escaped = False
    for character in value[1:-1]:
        if escaped:
            decoded.append(character)
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == '"':
            return None
        else:
            decoded.append(character)
    if escaped:
        return None
    return "".join(decoded)


def _parse_accept_ranges(header: str) -> tuple[_AcceptRange, ...]:
    ranges: list[_AcceptRange] = []
    items = _split_quoted(header, ",")
    if items is None:
        return ()
    for item in items:
        split_parts = _split_quoted(item, ";")
        if split_parts is None:
            continue
        parts = [part.strip() for part in split_parts]
        media_type = parts[0].lower()
        if media_type.count("/") != 1:
            continue
        major, minor = media_type.split("/", 1)
        if not major or not minor or (major == "*" and minor != "*") or ("*" in minor and minor != "*"):
            continue
        parameters: list[tuple[str, str]] = []
        quality = 1.0
        seen_names: set[str] = set()
        valid = True
        for parameter in parts[1:]:
            if "=" not in parameter:
                valid = False
                break
            name, raw_value = parameter.split("=", 1)
            name = name.strip().lower()
            value = _parameter_value(raw_value)
            if not name or value is None or name in seen_names:
                valid = False
                break
            seen_names.add(name)
            if name == "q":
                if _QVALUE.fullmatch(value) is None:
                    valid = False
                    break
                quality = float(value)
            else:
                parameters.append((name, value))
        if valid:
            ranges.append(_AcceptRange(major, minor, tuple(parameters), quality))
    return tuple(ranges)


def _range_quality(ranges: tuple[_AcceptRange, ...], major: str, minor: str) -> float | None:
    best: tuple[float, tuple[int, int]] | None = None
    for item in ranges:
        if item.major not in {"*", major} or item.minor not in {"*", minor} or item.parameters:
            continue
        specificity = (2 if item.major == major and item.minor == minor else 1 if item.major == major else 0, 0)
        if best is None or specificity > best[1]:
            best = (item.quality, specificity)
    return None if best is None else best[0]


@dataclass(frozen=True, slots=True)
class MinimalDspCataloguePolicy:
    """Implement the built-in stable DSP minimal catalogue profile."""

    def build_profile(
        self,
        config: DspProviderConfig,
        publications: tuple[DspPublicationRecord, ...],
    ) -> CatalogueProfile:
        """Build and cross-validate the built-in immutable snapshot."""
        declarations = tuple(item.dataset for item in publications if item.dataset is not None)
        services = tuple(item.service for item in publications if item.service is not None)
        if not declarations:
            raise ValueError("the DSP publication source contains no dataset publications")
        first_publisher = (declarations[0].dataset.publisher_id, declarations[0].dataset.publisher_name)
        if any(
            (publication.dataset.publisher_id, publication.dataset.publisher_name) != first_publisher
            for publication in declarations[1:]
        ):
            raise ValueError("all datasets in one catalogue must have the same publisher identifier and name")
        for attribute in ("id", "offer_id", "distribution_id"):
            values = [
                publication.dataset.id if attribute == "id" else getattr(publication, attribute)
                for publication in declarations
            ]
            if len(values) != len(set(values)):
                label = "dataset IDs" if attribute == "id" else attribute.replace("_", " ") + "s"
                raise ValueError(f"{label} must be unique within the catalogue")
        dataset_ids = tuple(publication.dataset.id for publication in declarations)
        service_conformance = [config.dsp_profile, DSP_2025_1_SPECIFICATION]
        if config.dcat_ap_content_negotiation:
            service_conformance.extend((config.dcat_ap_content_negotiation_profile, DCAT_AP_3_0_1_PROFILE))
        data_service = DataServiceProfile(
            config.service_id,
            config.service_title,
            config.service_endpoint_url,
            tuple(service_conformance),
            dataset_ids,
        )
        datasets: list[DatasetProfile] = []
        for publication in declarations:
            assert publication.file_format is not None
            assert publication.media_type is not None
            assert publication.offer_id is not None
            assert publication.distribution_id is not None
            access_url = config.resolve_access_url(publication.access_url)
            distribution = DistributionProfile(
                publication.distribution_id,
                publication.file_format,
                publication.file_format,
                publication.media_type,
                access_url,
                data_service,
                publication.byte_size,
                publication.sha256,
            )
            datasets.append(
                DatasetProfile(
                    publication.dataset,
                    OfferProfile(publication.offer_id, publication.dataset.id),
                    distribution,
                    data_service,
                    {"@type": "DataAddress", "endpointType": HTTP_ENDPOINT_TYPE, "endpoint": access_url},
                )
            )
        dsp_service_ids = {item.data_service.id for item in datasets}
        dcat_services: list[DcatDataServiceProfile] = []
        service_ids = [service.id for service in services]
        if len(service_ids) != len(set(service_ids)):
            raise ValueError("catalogue service IDs must be unique")
        if config.service_id in service_ids:
            raise ValueError("a catalogue service must have an ID distinct from the global DSP access service")
        dataset_id_set = set(dataset_ids)
        qualifying_service = False
        for service in services:
            try:
                endpoint_url = _https_url("service endpoint_url", service.endpoint_url, reject_query=True)
            except ValueError as error:
                raise ValueError("catalogue service endpoint_url must be an absolute HTTPS URL") from error
            served_ids = dataset_ids if service.serves_dataset_ids is None else service.serves_dataset_ids
            unknown = sorted(set(served_ids).difference(dataset_id_set))
            if unknown:
                raise ValueError(f"catalogue service references unknown dataset IDs: {', '.join(unknown)}")
            if (
                config.dcat_ap_profile in service.conforms_to
                and DCAT_AP_3_0_1_PROFILE in service.conforms_to
                and set(served_ids) == dataset_id_set
            ):
                qualifying_service = True
            if service.id in dsp_service_ids:
                raise ValueError("a catalogue service must have an ID distinct from every DSP access service")
            dcat_services.append(
                DcatDataServiceProfile(
                    service.id,
                    service.title,
                    endpoint_url,
                    service.conforms_to,
                    served_ids,
                    service.endpoint_description,
                )
            )
        if config.dcat_ap_content_negotiation and not qualifying_service:
            raise ValueError("dcat_ap_content_negotiation requires a qualifying published catalogue service")
        return CatalogueProfile(
            config.catalog_id,
            config.catalog_title,
            config.catalog_description,
            config.participant_id,
            config.dcat_ap_profile,
            tuple(datasets),
            tuple(dcat_services),
        )

    def validate_catalogue_request(
        self,
        config: DspProviderConfig,
        message: Mapping[str, object],
    ) -> None:
        """Reject catalogue filtering in the built-in minimal profile."""
        del config
        if message.get("filter") not in (None, [], ""):
            raise DspProtocolError("catalog", 400, "catalog filters are not supported", code="unsupported-filter")

    def select_catalogue_representation(
        self,
        config: DspProviderConfig,
        accept: str | None,
    ) -> DspCatalogueRepresentation:
        """Apply the built-in exact alternate-representation negotiation rule."""
        vary = (("Vary", "Accept"),) if config.dcat_ap_content_negotiation else ()
        if accept is None or not accept.strip():
            return DspCatalogueRepresentation("application/json", headers=vary)
        split_ranges = _split_quoted(accept, ",")
        raw_ranges = () if split_ranges is None else tuple(item for item in split_ranges if item.strip())
        ranges = _parse_accept_ranges(accept)
        if len(raw_ranges) == 1 and len(ranges) == 1:
            explicit = ranges[0]
            if (
                explicit.major == "application"
                and explicit.minor == "ld+json"
                and explicit.quality == 1.0
                and dict(explicit.parameters) in ({}, {"profile": DCAT_PROFILE})
            ):
                if config.dcat_ap_content_negotiation:
                    return DspCatalogueRepresentation(
                        DCAT_MEDIA_TYPE,
                        alternate=True,
                        headers=(("Vary", "Accept"), ("Link", f'<{config.dcat_ap_profile}>; rel="profile"')),
                    )
                raise DspProtocolError(
                    "catalog",
                    406,
                    "the DCAT-AP catalogue representation is not available",
                    code="not-acceptable",
                )
        ordinary_quality = _range_quality(ranges, "application", "json")
        if ordinary_quality is not None and ordinary_quality > 0:
            return DspCatalogueRepresentation("application/json", headers=vary)
        raise DspProtocolError(
            "catalog",
            406,
            "no acceptable catalogue representation is available",
            code="not-acceptable",
        )

    def serialize_catalogue(
        self,
        profile: CatalogueProfile,
        *,
        alternate: bool,
    ) -> dict[str, JsonValue]:
        """Serialize the built-in DSP or context-substituted catalogue."""
        return serialize_dcat_catalogue(profile) if alternate else serialize_dsp_catalogue(profile)

    def serialize_dataset(self, profile: DatasetProfile) -> dict[str, JsonValue]:
        """Serialize one built-in DSP dataset document."""
        return serialize_dsp_dataset_document(profile)

    def serialize_offer(self, offer: OfferProfile, *, include_target: bool) -> dict[str, JsonValue]:
        """Serialize one built-in unconditional-use offer."""
        return offer_policy(offer, include_target=include_target)


__all__ = [
    "DCAT_MEDIA_TYPE",
    "DCAT_PROFILE",
    "DspCataloguePolicy",
    "DspCatalogueRepresentation",
    "MinimalDspCataloguePolicy",
]
