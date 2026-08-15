"""Pin the DSP/OpenAPI/DCAT schema foundation and offline behavior."""

import copy
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft201909Validator
from openapi_spec_validator import validate
from pyshacl import validate as validate_shacl
from rdflib import Graph, Namespace, URIRef
from test_dsp_config import companion, config, publication

from httk.serve.dsp import DspDatasetPublication, DspProvider, DspPublicationRecord
from httk.serve.dsp.validation import dsp_contract
from httk.serve.http.openapi import OpenAPISchemaError

SCHEMAS = Path(__file__).parents[1] / "src" / "httk" / "serve" / "dsp" / "schemas"


def _without_external_schema_refs(value):
    """Replace external schema references in an OpenAPI copy for offline OAS validation."""
    if isinstance(value, dict):
        if set(value) == {"$ref"} and isinstance(value["$ref"], str) and not value["$ref"].startswith("#"):
            return {"type": "object"}
        return {key: _without_external_schema_refs(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_without_external_schema_refs(child) for child in value]
    return value


def test_openapi_is_valid_31_without_network_resolution() -> None:
    """Validate the OpenAPI structure while schema semantics stay in the offline registry."""
    document = dsp_contract().document()
    assert document["openapi"] == "3.1.0"
    validate(_without_external_schema_refs(copy.deepcopy(document)))


def test_every_official_schema_id_is_in_the_offline_registry() -> None:
    """Register every canonical official DSP schema identifier without retrieval hooks."""
    expected = set()
    for path in SCHEMAS.glob("*/*-schema.json"):
        document = json.loads(path.read_text(encoding="utf-8"))
        expected.add(document["$id"])
        Draft201909Validator.check_schema(document)
    registered = set(dsp_contract().schemas)
    assert expected <= registered
    assert all(
        identifier.startswith(("https://w3id.org/dspace/2025/1/", "https://schemas.httk.org/"))
        for identifier in registered
    )


def test_vendored_artifact_hashes_and_required_shacl_pin() -> None:
    """Verify every provenance digest and the normative DCAT-AP SHACL pin."""
    provenance = json.loads((SCHEMAS / "provenance.json").read_text(encoding="utf-8"))
    artifact_paths = {artifact["path"] for artifact in provenance["artifacts"]}
    packaged_paths = {
        path.relative_to(SCHEMAS).as_posix()
        for path in SCHEMAS.rglob("*")
        if path.is_file() and path.name not in {"README.md", "provenance.json"}
    }
    assert artifact_paths == packaged_paths
    for artifact in provenance["artifacts"]:
        path = SCHEMAS / artifact["path"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact["sha256"]
    assert hashlib.sha256((SCHEMAS / "dcat-ap" / "dcat-ap-SHACL.ttl").read_bytes()).hexdigest() == (
        "990d3e42721de6a4be8cc338a7171559f195e62dea89c0b56531356b78cc026f"
    )


def _example_schema(path: Path) -> str:
    """Map each upstream example filename to its official schema ID."""
    name = path.stem.removesuffix("_initial").removesuffix("-full")
    if path.parent.parent.name == "common":
        schema = "protocol-version"
    elif path.parent.parent.name == "catalog":
        schema = "catalog" if name in {"catalog", "nested-catalog"} else name
    elif path.parent.parent.name == "negotiation":
        schema = name
    else:
        schema = name
    area = path.parent.parent.name
    return f"https://w3id.org/dspace/2025/1/{area}/{schema}-schema.json"


def test_every_vendored_official_example_validates_offline(monkeypatch) -> None:
    """Validate pinned upstream examples while making network access impossible."""

    def blocked(*_args, **_kwargs):
        raise AssertionError("schema validation attempted network access")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    examples = sorted(SCHEMAS.glob("*/examples/*.json"))
    assert len(examples) == 25
    for path in examples:
        dsp_contract().schemas.validate(_example_schema(path), json.loads(path.read_text(encoding="utf-8")))


def test_local_profile_examples_validate() -> None:
    """Validate the strict local DCAT and HTTP-pull wire examples."""
    dsp_contract().schemas.validate(
        "https://schemas.httk.org/dsp/2025-1/dcat-ap-catalogue.json",
        json.loads((SCHEMAS / "profile" / "example-catalogue.json").read_text(encoding="utf-8")),
    )
    dsp_contract().schemas.validate(
        "https://schemas.httk.org/dsp/2025-1/http-pull-profile.json",
        json.loads((SCHEMAS / "profile" / "example-http-pull-configuration.json").read_text(encoding="utf-8")),
    )


def test_owned_dcat_context_makes_endpoint_an_iri() -> None:
    """Pin the standards boundary that requires the separate DCAT projection."""
    owned = json.loads((SCHEMAS / "profile" / "example-catalogue.json").read_text(encoding="utf-8"))
    owned_graph = Graph().parse(data=json.dumps(owned), format="json-ld")
    dcat = Namespace("http://www.w3.org/ns/dcat#")
    service = URIRef("https://example.invalid/service")
    endpoint = URIRef("https://example.invalid/dsp/2025-1")
    assert (service, dcat.endpointURL, endpoint) in owned_graph
    optimade = URIRef("https://example.invalid/services/optimade")
    dataset = URIRef("https://example.invalid/dataset")
    standard = URIRef("https://schemas.optimade.org/defs/v1.3/standards/optimade")
    description = URIRef("https://www.optimade.org/specification/latest/")
    assert (optimade, dcat.endpointURL, URIRef("https://example.invalid/optimade/v1")) in owned_graph
    assert (optimade, dcat.servesDataset, dataset) in owned_graph
    assert (optimade, URIRef("http://purl.org/dc/terms/conformsTo"), standard) in owned_graph
    assert (optimade, dcat.endpointDescription, description) in owned_graph

    official_context = json.loads((SCHEMAS / "context" / "dspace.jsonld").read_text(encoding="utf-8"))
    official = {
        "@context": official_context["@context"],
        "@id": str(service),
        "@type": "DataService",
        "endpointURL": str(endpoint),
    }
    official_graph = Graph().parse(data=json.dumps(official), format="json-ld")
    assert (service, dcat.endpointURL, endpoint) not in official_graph


def _usable_dcat_shapes(*filenames: str) -> Graph:
    """Load pinned shapes and discard only upstream dangling property references."""
    graph = Graph()
    for filename in filenames:
        graph += Graph().parse(SCHEMAS / "dcat-ap" / filename, format="turtle")
    shacl = Namespace("http://www.w3.org/ns/shacl#")
    for shape, prop in list(graph.subject_objects(shacl.property)):
        if not list(graph.predicate_objects(prop)):
            graph.remove((shape, shacl.property, prop))
    return graph


def test_dcat_graph_conforms_to_pinned_mandatory_range_and_vocabulary_profiles() -> None:
    """Run an actual generated minimal catalogue through all pinned constraints."""
    csv = publication("csv")
    csv_distribution = replace(csv.distribution, byte_size=7, sha256="0" * 64)
    csv = DspDatasetPublication(
        replace(csv.dataset, distributions=(csv_distribution,)), offer_id=csv.offer_id
    )
    provider = DspProvider(
        config(dcat_ap_content_negotiation=True),
        publications=(
            DspPublicationRecord(dataset=csv),
            DspPublicationRecord(dataset=publication("json")),
            DspPublicationRecord(service=companion()),
        ),
    )
    document = provider.dcat_catalogue()
    data = Graph().parse(data=json.dumps(document), format="json-ld")
    for fixture in ("eu-file-type-csv.rdf", "eu-file-type-json.rdf", "spdx-2.3-ontology.owl.xml"):
        data += Graph().parse(SCHEMAS / "dcat-ap" / fixture, format="xml")
    profiles = (
        ("dcat-ap-SHACL.ttl",),
        ("dcat-ap-SHACL.ttl", "dcat-ap-ranges.ttl"),
        ("dcat-ap-SHACL.ttl", "dcat-ap-controlled-vocabularies.shape.ttl"),
    )
    for filenames in profiles:
        conforms, _report, report_text = validate_shacl(
            data,
            shacl_graph=_usable_dcat_shapes(*filenames),
            inference="rdfs",
            advanced=True,
            allow_warnings=True,
            allow_infos=True,
            do_owl_imports=False,
        )
        assert conforms, report_text


def test_schema_files_are_parseable_data() -> None:
    """Parse every bundled JSON, JSON-LD, YAML, Turtle, and RDF fixture."""
    for path in SCHEMAS.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix in {".json", ".jsonld"}:
            json.loads(path.read_text(encoding="utf-8"))
        elif path.suffix in {".yaml", ".yml"}:
            yaml.safe_load(path.read_text(encoding="utf-8"))
        elif path.suffix == ".ttl":
            Graph().parse(path, format="turtle")
        elif path.suffix == ".rdf" or path.name.endswith(".owl.xml"):
            Graph().parse(path, format="xml")


def test_registry_rejects_unknown_schema_without_retrieval() -> None:
    """Keep unknown identifiers from falling through to network retrieval."""
    with pytest.raises(OpenAPISchemaError, match="not registered"):
        dsp_contract().schemas.lookup("https://schemas.invalid/not-bundled.json")
