"""Exercise the generic packaged OpenAPI contract and schema loaders."""

import pytest

from httk.serve.http.openapi import (
    OpenAPISchemaRegistry,
    load_packaged_contract,
    packaged_schema_documents,
    packaged_schema_registry,
)

PACKAGE = "httk.serve.dsp"


def test_packaged_schema_documents_are_stable_schema_dicts() -> None:
    """Every bundled document is a schema mapping and the order is deterministic."""
    documents = packaged_schema_documents(PACKAGE, "schemas")
    assert isinstance(documents, tuple)
    assert documents
    for document in documents:
        assert isinstance(document, dict)
        assert "$schema" in document
    # The bundle is emitted in a directory-first, name-sorted walk order that does not
    # coincide with a plain sort of the $id URLs (the httk.org profiles interleave with
    # the w3id.org tree). Pinning the exact sequence fails if _walk drops its sorted().
    assert [document.get("$id") for document in documents] == [
        "https://w3id.org/dspace/2025/1/catalog/catalog-error-schema.json",
        "https://w3id.org/dspace/2025/1/catalog/catalog-request-message-schema.json",
        "https://w3id.org/dspace/2025/1/catalog/catalog-schema.json",
        "https://w3id.org/dspace/2025/1/catalog/dataset-request-message-schema.json",
        "https://w3id.org/dspace/2025/1/catalog/dataset-schema.json",
        "https://w3id.org/dspace/2025/1/common/context-schema.json",
        "https://w3id.org/dspace/2025/1/common/protocol-version-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-agreement-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-agreement-verification-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-error-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-event-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-negotiation-termination-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-offer-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-request-message-schema.json",
        "https://w3id.org/dspace/2025/1/negotiation/contract-schema.json",
        "https://schemas.httk.org/dsp/2025-1/dcat-ap-catalogue.json",
        "https://schemas.httk.org/dsp/2025-1/http-pull-profile.json",
        "https://w3id.org/dspace/2025/1/transfer/data-address-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-completion-message-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-error-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-process-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-request-message-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-start-message-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-suspension-message-schema.json",
        "https://w3id.org/dspace/2025/1/transfer/transfer-termination-message-schema.json",
    ]


def test_every_packaged_schema_document_declares_an_id() -> None:
    """The bundle satisfies the registry's non-empty ``$id`` requirement."""
    documents = packaged_schema_documents(PACKAGE, "schemas")
    for document in documents:
        identifier = document.get("$id")
        assert isinstance(identifier, str) and identifier


def test_packaged_schema_registry_registers_identifiers() -> None:
    """Composing the loaders yields a usable, non-empty registry."""
    registry = packaged_schema_registry(PACKAGE, "schemas")
    assert isinstance(registry, OpenAPISchemaRegistry)
    assert registry.identifiers


def test_load_packaged_contract_reads_the_yaml_openapi_document() -> None:
    """The YAML branch returns the packaged OpenAPI contract mapping."""
    document = load_packaged_contract(PACKAGE, "schemas", "openapi.yaml")
    assert isinstance(document, dict)
    assert document["openapi"] == "3.1.0"
    assert "paths" in document


def test_load_packaged_contract_reads_a_json_object_resource() -> None:
    """The JSON branch parses a packaged ``.json`` object resource."""
    document = load_packaged_contract(PACKAGE, "schemas", "provenance.json")
    assert isinstance(document, dict)
    assert "artifacts" in document


def test_load_packaged_contract_rejects_an_unknown_suffix() -> None:
    """An unsupported resource suffix fails before any parse attempt."""
    with pytest.raises(ValueError, match="unsupported contract format"):
        load_packaged_contract(PACKAGE, "schemas", "README.md")


def test_load_packaged_contract_rejects_a_missing_resource() -> None:
    """A resource that is not packaged raises the reader's file error."""
    with pytest.raises(FileNotFoundError):
        load_packaged_contract(PACKAGE, "schemas", "does-not-exist.yaml")
