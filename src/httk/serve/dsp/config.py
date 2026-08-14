"""Validated configuration and durable publication records for DSP serving."""

import re
from collections.abc import Mapping
from dataclasses import MISSING, dataclass, fields
from typing import Any, ClassVar, Self
from urllib.parse import urlsplit

from httk.core import Dataset, Service, StorageInfo

from .models import DSP_CONTEXT

DSP_VERSION = "2025-1"
"""Implemented Data Space Protocol version."""

HTTP_ENDPOINT_TYPE = "https://w3id.org/idsa/v4.1/HTTP"
"""Official DSP endpoint type used by HTTPS-pull data addresses."""

DSP_2025_1_SPECIFICATION = "https://eclipse-dataspace-protocol-base.github.io/DataspaceProtocol/2025-1-err1/"
DCAT_AP_3_0_1_PROFILE = "https://semiceu.github.io/DCAT-AP/releases/3.0.1/"
DSP_MINIMAL_PROFILE = "https://schemas.httk.org/profiles/dsp/2025-1/minimal"
DCAT_AP_MINIMAL_PROFILE = "https://schemas.httk.org/profiles/dcat-ap/3.0.1/minimal"
DCAT_AP_MINIMAL_CONTENT_NEGOTIATION = f"{DSP_MINIMAL_PROFILE}#dcat-ap-content-negotiation"

EU_FILE_TYPE_CSV = "http://publications.europa.eu/resource/authority/file-type/CSV"
EU_FILE_TYPE_JSON = "http://publications.europa.eu/resource/authority/file-type/JSON"
IANA_MEDIA_TYPE_CSV = "https://www.iana.org/assignments/media-types/text/csv"
IANA_MEDIA_TYPE_JSON = "https://www.iana.org/assignments/media-types/application/json"
SPDX_SHA256 = "https://spdx.org/rdf/terms#checksumAlgorithm_sha256"

_SHA256 = re.compile(r"[0-9a-f]{64}")


def _nonempty(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")
    return value


def _plain_iri_characters(value: str) -> bool:
    return not any(
        character.isspace()
        or ord(character) < 32
        or 127 <= ord(character) <= 159
        or 0xD800 <= ord(character) <= 0xDFFF
        or character in '<>"{}|\\^`'
        for character in value
    )


def _valid_percent_escapes(value: str) -> bool:
    hexdigits = frozenset("0123456789abcdefABCDEF")
    return all(
        value[index] != "%"
        or index + 2 < len(value)
        and value[index + 1] in hexdigits
        and value[index + 2] in hexdigits
        for index in range(len(value))
    )


def _absolute_iri(field_name: str, value: object) -> str:
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


def _https_url(field_name: str, value: object, *, reject_query: bool = False) -> str:
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
        or (port is not None and not 1 <= port <= 65_535)
        or not _valid_percent_escapes(text)
    ):
        raise ValueError(f"{field_name} must be an absolute HTTPS URL")
    return text


def _https_origin(field_name: str, value: object) -> str:
    text = _https_url(field_name, value, reject_query=True)
    parsed = urlsplit(text)
    if parsed.path not in {"", "/"}:
        raise ValueError(f"{field_name} must be an absolute HTTPS origin")
    return text.rstrip("/")


def _mount_path(value: object) -> str:
    text = _nonempty("dsp_mount", value)
    if (
        not text.startswith("/")
        or text == "/"
        or text.endswith("/")
        or "//" in text
        or "?" in text
        or "#" in text
        or "%" in text
        or any(part in {"", ".", ".."} for part in text[1:].split("/"))
    ):
        raise ValueError("dsp_mount must be a canonical root-relative mount path")
    return text


def _publication_url(value: object) -> str:
    text = _nonempty("access_url", value)
    if text.startswith("/"):
        parsed = urlsplit(text)
        if (
            parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or text.startswith("//")
            or not _valid_percent_escapes(text)
        ):
            raise ValueError("access_url must be root-relative or an absolute HTTPS URL")
        return text
    return _https_url("access_url", text)


@dataclass(frozen=True)
class DspDatasetPublication:
    """Store one dataset's transport-independent DSP publication declaration.

    ``file_format`` and ``media_type`` are inferred for ``.csv`` and ``.json``
    access paths.  Other representations must declare both as absolute IRIs.
    No file is opened and no metadata is computed by this record.
    """

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="serve_dsp_publication_v1",
        identity_name="serve_dsp_publication_v1",
    )

    dataset: Dataset
    access_url: str
    file_format: str | None = None
    media_type: str | None = None
    byte_size: int | None = None
    sha256: str | None = None
    offer_id: str | None = None
    distribution_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset", Dataset.create(self.dataset))
        object.__setattr__(self, "access_url", _publication_url(self.access_url))
        suffix = urlsplit(self.access_url).path.lower()
        inferred = (
            (EU_FILE_TYPE_CSV, IANA_MEDIA_TYPE_CSV)
            if suffix.endswith(".csv")
            else (EU_FILE_TYPE_JSON, IANA_MEDIA_TYPE_JSON)
            if suffix.endswith(".json")
            else None
        )
        if inferred is None and (self.file_format is None or self.media_type is None):
            raise ValueError("non-CSV/JSON publications require explicit file_format and media_type IRIs")
        file_format = inferred[0] if self.file_format is None and inferred is not None else self.file_format
        media_type = inferred[1] if self.media_type is None and inferred is not None else self.media_type
        object.__setattr__(self, "file_format", _absolute_iri("file_format", file_format))
        object.__setattr__(self, "media_type", _absolute_iri("media_type", media_type))
        if self.byte_size is not None and (
            isinstance(self.byte_size, bool) or not isinstance(self.byte_size, int) or self.byte_size < 0
        ):
            raise ValueError("byte_size must be a non-negative integer or None")
        if self.sha256 is not None and (not isinstance(self.sha256, str) or _SHA256.fullmatch(self.sha256) is None):
            raise ValueError("sha256 must be a lowercase 64-character hexadecimal digest or None")
        object.__setattr__(
            self,
            "offer_id",
            _absolute_iri("offer_id", self.offer_id or f"{self.dataset.id}#offer"),
        )
        object.__setattr__(
            self,
            "distribution_id",
            _absolute_iri("distribution_id", self.distribution_id or f"{self.dataset.id}#distribution"),
        )

    @classmethod
    def create(cls, obj: Self | Mapping[str, Any]) -> Self:
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise TypeError(f"expected {cls.__name__} or a mapping")
        if any(not isinstance(name, str) for name in obj):
            raise ValueError("publication mapping keys must be strings")
        names = {field.name for field in fields(cls)}
        required = {
            field.name for field in fields(cls) if field.default is MISSING and field.default_factory is MISSING
        }
        missing = required.difference(obj)
        unknown = set(obj).difference(names)
        if missing or unknown:
            details = []
            if missing:
                details.append(f"missing fields: {', '.join(sorted(missing))}")
            if unknown:
                details.append(f"unknown fields: {', '.join(sorted(unknown))}")
            raise ValueError("; ".join(details))
        return cls(**{name: obj[name] for name in names if name in obj})


@dataclass(frozen=True)
class DspPublicationRecord:
    """Store exactly one dataset publication or catalogue service envelope."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="serve_dsp_publication_envelope_v1",
        identity_name="serve_dsp_publication_envelope_v1",
    )

    dataset: DspDatasetPublication | None = None
    service: Service | None = None

    def __post_init__(self) -> None:
        if (self.dataset is None) == (self.service is None):
            raise ValueError("DspPublicationRecord must contain exactly one of dataset or service")
        if self.dataset is not None:
            object.__setattr__(self, "dataset", DspDatasetPublication.create(self.dataset))
        if self.service is not None:
            object.__setattr__(self, "service", Service.create(self.service))

    @classmethod
    def create(cls, obj: Self | Mapping[str, Any]) -> Self:
        if isinstance(obj, cls):
            return obj
        if not isinstance(obj, Mapping):
            raise TypeError(f"expected {cls.__name__} or a mapping")
        if any(not isinstance(name, str) for name in obj):
            raise ValueError("publication envelope mapping keys must be strings")
        names = {field.name for field in fields(cls)}
        unknown = set(obj).difference(names)
        if unknown:
            raise ValueError(f"unknown fields: {', '.join(sorted(unknown))}")
        return cls(**{name: obj[name] for name in names if name in obj})


class DspPublicationEntry:
    """Non-OPTIMADE logical family for durable DSP publication records."""

    type = "dsp-publications"
    definition_id = None

    def __new__(cls, *args: Any, **kwargs: Any) -> Self:
        raise TypeError("DspPublicationEntry is a logical entry family; store DspPublicationRecord directly")


@dataclass(frozen=True, slots=True)
class DspProviderConfig:
    """Configure global DSP minimal service and catalogue metadata."""

    public_base_url: str
    service_id: str
    service_title: str
    participant_id: str
    catalog_id: str
    catalog_title: str
    catalog_description: str
    dsp_mount: str = "/dsp"
    automatic_progression: bool = True
    dcat_ap_content_negotiation: bool = False
    dsp_profile: str = DSP_MINIMAL_PROFILE
    dcat_ap_profile: str = DCAT_AP_MINIMAL_PROFILE
    dcat_ap_content_negotiation_profile: str = DCAT_AP_MINIMAL_CONTENT_NEGOTIATION

    def __post_init__(self) -> None:
        object.__setattr__(self, "public_base_url", _https_origin("public_base_url", self.public_base_url))
        object.__setattr__(self, "dsp_mount", _mount_path(self.dsp_mount))
        for name in ("service_id", "participant_id", "catalog_id"):
            object.__setattr__(self, name, _absolute_iri(name, getattr(self, name)))
        for name in ("service_title", "catalog_title", "catalog_description"):
            object.__setattr__(self, name, _nonempty(name, getattr(self, name)))
        for name in ("dsp_profile", "dcat_ap_profile", "dcat_ap_content_negotiation_profile"):
            object.__setattr__(self, name, _absolute_iri(name, getattr(self, name)))
        if not isinstance(self.automatic_progression, bool):
            raise TypeError("automatic_progression must be a bool")
        if not isinstance(self.dcat_ap_content_negotiation, bool):
            raise TypeError("dcat_ap_content_negotiation must be a bool")

    @property
    def connector_root_url(self) -> str:
        """Return the externally visible DSP connector root."""
        return f"{self.public_base_url}{self.dsp_mount}"

    @property
    def service_endpoint_url(self) -> str:
        """Return the externally visible versioned DSP endpoint."""
        return f"{self.connector_root_url}/{DSP_VERSION}"

    def resolve_access_url(self, access_url: str) -> str:
        """Resolve one validated publication URL against the public origin."""
        value = _publication_url(access_url)
        return f"{self.public_base_url}{value}" if value.startswith("/") else value


__all__ = [
    "DCAT_AP_3_0_1_PROFILE",
    "DCAT_AP_MINIMAL_CONTENT_NEGOTIATION",
    "DCAT_AP_MINIMAL_PROFILE",
    "DSP_2025_1_SPECIFICATION",
    "DSP_CONTEXT",
    "DSP_MINIMAL_PROFILE",
    "DSP_VERSION",
    "EU_FILE_TYPE_CSV",
    "EU_FILE_TYPE_JSON",
    "HTTP_ENDPOINT_TYPE",
    "IANA_MEDIA_TYPE_CSV",
    "IANA_MEDIA_TYPE_JSON",
    "SPDX_SHA256",
    "DspDatasetPublication",
    "DspProviderConfig",
    "DspPublicationEntry",
    "DspPublicationRecord",
]
