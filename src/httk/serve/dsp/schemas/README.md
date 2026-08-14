# Vendored DSP and DCAT-AP schema assets

This directory contains raw upstream bytes for the pinned Eclipse Dataspace Protocol commit `0a30bdd30c36ffb329e1c29fd819faf9f33e0f22`, DCAT-AP release tag `3.0.1`, the EU CSV/JSON file-type concepts, and the SPDX 2.3 ontology used by checksum validation. The machine-readable inventory, source URLs, SHA-256 digests, and licence attribution are in [`provenance.json`](provenance.json).

DSP assets are Apache-2.0 licensed; the complete upstream notice is [`LICENSE-APACHE-2.0.txt`](LICENSE-APACHE-2.0.txt). DCAT-AP 3.0.1 repository material is CC-BY-4.0; the complete authoritative legal text is vendored as [`LICENSE-CC-BY-4.0.txt`](LICENSE-CC-BY-4.0.txt). SPDX 2.3 is CC-BY-3.0 and its license is [`LICENSE-CC-BY-3.0.txt`](LICENSE-CC-BY-3.0.txt). The EU Publications Office RDF fixtures retain their source-specific attribution in `provenance.json`.

The owned profile assets are in [`profile/`](profile/): an inline-capable JSON-LD context, catalogue projection schema (including file metadata and additional public DCAT data services), HTTPS-pull `DataAddress` schema, and small valid JSON fixtures. They are marked local and AGPL-3.0-or-later in `provenance.json`, with local SHA-256 values rather than upstream provenance.

The requested all-in-one DCAT-AP SHACL digest is pinned and verified as `990d3e42721de6a4be8cc338a7171559f195e62dea89c0b56531356b78cc026f`.

The upstream DSP transfer error, suspension, and termination schemas contain
the fragment spelling `.json#definitions/...` rather than the JSON Pointer
`.json#/definitions/...`. Their bytes and hashes remain authoritative here;
the offline validator corrects only those three fragments in memory. The
DCAT-AP all-in-one and split range graphs likewise contain `sh:property`
references whose property-shape subjects have no triples. Conformance tests
retain the pinned graphs and remove only those dangling references in memory
before invoking pySHACL.

Official DSP JSON examples from the pinned commit are vendored under `common/examples/`, `catalog/examples/`, `negotiation/examples/`, and `transfer/examples/`. Both official common DID-service examples are intentionally excluded because DID service support is outside this packet's scope; both exclusions are recorded in `provenance.json`.
