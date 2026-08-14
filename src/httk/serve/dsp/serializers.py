"""JSON projections for the DSP/DCAT catalogue and its process records."""

from copy import deepcopy
from typing import Final

from .config import DSP_CONTEXT, DSP_VERSION, SPDX_SHA256
from .models import (
    AgreementRecord,
    CatalogueProfile,
    DataServiceProfile,
    DatasetProfile,
    DcatDataServiceProfile,
    DistributionProfile,
    JsonValue,
    NegotiationRecord,
    OfferProfile,
    TransferRecord,
    thaw_json,
)

_DCAT_CONTEXT: Final[dict[str, JsonValue]] = {
    "@version": 1.1,
    "@protected": True,
    "dcat": "http://www.w3.org/ns/dcat#",
    "dct": "http://purl.org/dc/terms/",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "odrl": "http://www.w3.org/ns/odrl/2/",
    "spdx": "http://spdx.org/rdf/terms#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "Catalog": "dcat:Catalog",
    "Dataset": "dcat:Dataset",
    "Distribution": "dcat:Distribution",
    "DataService": "dcat:DataService",
    "Agent": "foaf:Agent",
    "Offer": "odrl:Offer",
    "title": "dct:title",
    "description": "dct:description",
    "publisher": {"@id": "dct:publisher", "@type": "@id"},
    "accessURL": {"@id": "dcat:accessURL", "@type": "@id"},
    "downloadURL": {"@id": "dcat:downloadURL", "@type": "@id"},
    "mediaType": {"@id": "dcat:mediaType", "@type": "@id"},
    "byteSize": {"@id": "dcat:byteSize", "@type": "xsd:nonNegativeInteger"},
    "checksum": {"@id": "spdx:checksum"},
    "algorithm": {"@id": "spdx:algorithm", "@type": "@id"},
    "checksumValue": {"@id": "spdx:checksumValue", "@type": "xsd:hexBinary"},
    "accessService": {"@id": "dcat:accessService", "@type": "@id"},
    "endpointURL": {"@id": "dcat:endpointURL", "@type": "@id"},
    "endpointDescription": {"@id": "dcat:endpointDescription", "@type": "@id"},
    "servesDataset": {"@id": "dcat:servesDataset", "@type": "@id", "@container": "@set"},
    "conformsTo": {"@id": "dct:conformsTo", "@type": "@id", "@container": "@set"},
    "format": {"@id": "dct:format", "@type": "@id"},
    "permission": {"@id": "odrl:permission", "@container": "@set"},
    "action": {"@id": "odrl:action", "@type": "@vocab"},
    "use": "odrl:use",
}


def _dsp_context() -> list[JsonValue]:
    """Return a fresh official DSP context array for a response document."""
    return [DSP_CONTEXT]


def _dcat_context() -> dict[str, JsonValue]:
    """Return a fresh owned DCAT-AP-compatible JSON-LD context."""
    return deepcopy(_DCAT_CONTEXT)


def offer_policy(offer: OfferProfile, *, include_target: bool) -> dict[str, JsonValue]:
    """Serialize the unconditional ODRL ``use`` offer.

    :param offer: Fixed offer profile to serialize.
    :param include_target: Whether to add the message-only dataset target.
    :return: Fresh JSON policy object.
    """
    policy: dict[str, JsonValue] = {
        "@id": offer.id,
        "@type": "Offer",
        "permission": [{"action": "use"}],
    }
    if include_target:
        policy["target"] = offer.target
    return policy


def serialize_data_service(service: DataServiceProfile) -> dict[str, JsonValue]:
    """Serialize a DSP data service embedded in a distribution.

    :param service: Data-service profile to serialize.
    :return: Fresh DSP data-service object.
    """
    return {
        "@id": service.id,
        "@type": "DataService",
        "dct:title": service.title,
        "endpointURL": service.endpoint_url,
    }


def serialize_distribution(distribution: DistributionProfile) -> dict[str, JsonValue]:
    """Serialize the DSP distribution with its embedded service and access URL.

    :param distribution: Distribution profile to serialize.
    :return: Fresh DSP distribution object.
    """
    return {
        "@id": distribution.id,
        "@type": "Distribution",
        "format": distribution.format,
        "dcat:accessURL": {"@id": distribution.access_url},
        "accessService": serialize_data_service(distribution.data_service),
    }


def serialize_dsp_dataset(profile: DatasetProfile) -> dict[str, JsonValue]:
    """Serialize one dataset in its DSP catalogue projection.

    :param profile: Fixed provider catalogue profile.
    :return: Fresh DSP dataset object.
    """
    dataset = profile.dataset
    return {
        "@id": dataset.id,
        "@type": "Dataset",
        "dct:title": dataset.title,
        "dct:description": dataset.description,
        "dct:publisher": {"@id": dataset.publisher_id, "foaf:name": dataset.publisher_name},
        "hasPolicy": [offer_policy(profile.offer, include_target=False)],
        "distribution": [serialize_distribution(profile.distribution)],
    }


def serialize_dsp_catalogue(profile: CatalogueProfile) -> dict[str, JsonValue]:
    """Serialize the fixed catalogue under the protected official DSP context.

    :param profile: Fixed provider catalogue profile.
    :return: Fresh DSP catalogue document.
    """
    return {
        "@context": _dsp_context(),
        "@id": profile.id,
        "@type": "Catalog",
        "participantId": profile.participant_id,
        "dct:title": profile.title,
        "dct:description": profile.description,
        "dataset": [serialize_dsp_dataset(dataset) for dataset in profile.datasets],
    }


def serialize_dsp_dataset_document(profile: DatasetProfile) -> dict[str, JsonValue]:
    """Serialize one dataset as a DSP root response document.

    :param profile: Fixed provider catalogue profile.
    :return: Fresh DSP dataset document.
    """
    document = serialize_dsp_dataset(profile)
    document["@context"] = _dsp_context()
    return document


def _serialize_dcat_data_service(service: DcatDataServiceProfile) -> dict[str, JsonValue]:
    """Serialize one non-DSP public API in the owned DCAT projection."""
    document: dict[str, JsonValue] = {
        "@id": service.id,
        "@type": "DataService",
        "title": service.title,
        "endpointURL": {"@id": service.endpoint_url},
        "servesDataset": [{"@id": dataset_id} for dataset_id in service.serves_dataset_ids],
        "conformsTo": [{"@id": standard, "@type": "dct:Standard"} for standard in service.conforms_to],
    }
    if service.endpoint_description is not None:
        document["endpointDescription"] = {"@id": service.endpoint_description}
    return document


def serialize_dcat_catalogue(profile: CatalogueProfile) -> dict[str, JsonValue]:
    """Serialize the strict, owned-context DCAT-AP projection.

    The DCAT projection is intentionally distinct from DSP: its distribution
    advertises the EU JSON-LD file-type IRI, and every IRI-valued endpoint,
    service, publisher, access URL, and format is an ``@id`` object.

    :param profile: Fixed provider catalogue profile.
    :return: Fresh DCAT-AP-compatible JSON-LD catalogue document.
    """
    first = profile.datasets[0].dataset
    services = tuple({item.data_service.id: item.data_service for item in profile.datasets}.values())
    dcat_services: list[JsonValue] = [
        {
            "@id": service.id,
            "@type": "DataService",
            "title": service.title,
            "endpointURL": {"@id": service.endpoint_url},
        }
        for service in services
    ]
    dcat_services.extend(_serialize_dcat_data_service(service) for service in profile.dcat_data_services)
    document: dict[str, JsonValue] = {
        "@context": _dcat_context(),
        "@id": profile.id,
        "@type": "Catalog",
        "title": profile.title,
        "description": profile.description,
        "dct:publisher": {"@id": first.publisher_id, "@type": "Agent", "foaf:name": first.publisher_name},
        "dcat:dataset": [
            {
                "@id": item.dataset.id,
                "@type": "Dataset",
                "title": item.dataset.title,
                "description": item.dataset.description,
                "dct:publisher": {"@id": item.dataset.publisher_id},
                "odrl:hasPolicy": [offer_policy(item.offer, include_target=False)],
                "dcat:distribution": [
                    {
                        "@id": item.distribution.id,
                        "@type": "Distribution",
                        "format": {"@id": item.distribution.format, "@type": "dct:MediaTypeOrExtent"},
                        "mediaType": {"@id": item.distribution.media_type, "@type": "dct:MediaType"},
                        "accessURL": {"@id": item.distribution.access_url},
                        "downloadURL": {"@id": item.distribution.access_url},
                        "accessService": {"@id": item.data_service.id},
                        **(
                            {"byteSize": item.distribution.byte_size} if item.distribution.byte_size is not None else {}
                        ),
                        **(
                            {
                                "checksum": {
                                    "@type": "spdx:Checksum",
                                    "algorithm": {"@id": SPDX_SHA256, "@type": "spdx:ChecksumAlgorithm"},
                                    "checksumValue": item.distribution.sha256,
                                }
                            }
                            if item.distribution.sha256 is not None
                            else {}
                        ),
                    }
                ],
            }
            for item in profile.datasets
        ],
        "dcat:service": dcat_services,
    }
    return document


def serialize_agreement(agreement: AgreementRecord) -> dict[str, JsonValue]:
    """Serialize an immutable agreement record as a fresh JSON policy.

    :param agreement: Agreement record to serialize.
    :return: Fresh agreement policy object.
    """
    policy = thaw_json(agreement.policy)
    if not isinstance(policy, dict):
        raise TypeError("agreement policy must be a JSON object")
    return policy


def serialize_negotiation(record: NegotiationRecord) -> dict[str, JsonValue]:
    """Serialize a negotiation process at its acknowledged state.

    :param record: Negotiation snapshot to serialize.
    :return: Fresh DSP contract-negotiation document.
    """
    return {
        "@context": _dsp_context(),
        "@type": "ContractNegotiation",
        "providerPid": record.provider_pid,
        "consumerPid": record.consumer_pid,
        "state": record.state,
    }


def serialize_transfer(record: TransferRecord) -> dict[str, JsonValue]:
    """Serialize a transfer process at its acknowledged state.

    :param record: Transfer snapshot to serialize.
    :return: Fresh DSP transfer-process document.
    """
    return {
        "@context": _dsp_context(),
        "@type": "TransferProcess",
        "providerPid": record.provider_pid,
        "consumerPid": record.consumer_pid,
        "state": record.state,
    }


def version_document(service_id: str) -> dict[str, JsonValue]:
    """Serialize the DSP HTTPS version-discovery response.

    :param service_id: Configured service identifier.
    :return: Fresh protocol version document.
    """
    return {
        "protocolVersions": [
            {
                "version": DSP_VERSION,
                "path": "/2025-1",
                "binding": "HTTPS",
                "serviceId": service_id,
            }
        ]
    }


__all__ = [
    "offer_policy",
    "serialize_agreement",
    "serialize_data_service",
    "serialize_dcat_catalogue",
    "serialize_distribution",
    "serialize_dsp_catalogue",
    "serialize_dsp_dataset",
    "serialize_dsp_dataset_document",
    "serialize_negotiation",
    "serialize_transfer",
    "version_document",
]
