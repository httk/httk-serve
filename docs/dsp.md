# Data Space Protocol catalogues

`httk.serve.dsp` serves the provider side of DSP 2025-1. Publications can be
declared inline or stored under the non-OPTIMADE `DspPublicationEntry` family.
Negotiation and transfer process state remains in memory; publication records
are queried live from the caller-owned store.

The official DSP catalogue discovery operation is:

```text
POST /dsp/2025-1/catalog/request
```

`GET /dsp/2025-1/catalog` is an alternate DCAT JSON-LD representation, not DSP
catalogue discovery.

## Store-native catalogue

```python
from httk.core import Dataset
from httk.store.db import Database, SqlStore
from httk.serve.dsp import (
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    create_dsp_app,
)

publication = DspDatasetPublication(
    dataset=Dataset(
        id="https://provider.example/datasets/one",
        title="Dataset one",
        description="An example dataset.",
        publisher_id="https://provider.example/participants/provider",
        publisher_name="Provider",
    ),
    access_url="/files/dataset.csv",
    byte_size=123,
    sha256="…publisher-computed lowercase SHA-256…",
)

store = SqlStore(
    Database.sqlite(),
    entry_records={DspPublicationEntry: DspDatasetPublication},
)
store.save(publication)

config = DspProviderConfig(
    public_base_url="https://provider.example",
    dsp_mount="/dsp",
    service_id="https://provider.example/services/download",
    service_title="Dataset downloads",
    participant_id="https://provider.example/participants/provider",
    catalog_id="https://provider.example/catalogues/main",
    catalog_title="Main catalogue",
    catalog_description="Published datasets.",
    catalogue_profile="dcat-ap-3.0.1",
)
app = create_dsp_app(DspProvider(config, store=store))
```

The store must declare `DspPublicationEntry`. The provider does not close the
store or its database. Each catalogue and dataset request takes a new snapshot,
so later `save()` calls become visible. Duplicate dataset, offer, or
distribution IDs and inconsistent publishers are rejected when that snapshot
is built.

For a fixed declaration, use `DspProvider(config, datasets=(publication,))`.
Exactly one of `store` and `datasets` is required.

## Publications and public files

`DspDatasetPublication` never opens, measures, or hashes a file. `byte_size`
and `sha256` are publisher-supplied metadata and can become stale when a file
is replaced. `.csv` and `.json` access paths infer the corresponding full EU
File Type and IANA Media Type IRIs. Other suffixes require explicit
`file_format=` and `media_type=` IRIs.

An access URL beginning with `/` is resolved against `public_base_url`; an
absolute URL must use HTTPS. DSP generates the HTTPS-pull `DataAddress` from
that resolved URL. File routing is deliberately independent: see
{doc}`web/static_files`. These files are public and are not protected by DSP
negotiation.

## Catalogue profiles

`catalogue_profile="dcat-ap-3.0.1"` is the strict profile. Each DSP
distribution and `TransferRequestMessage` uses its exact EU file-type IRI as
the transfer value. The alternate graph includes `dct:format`,
`dcat:mediaType`, `dcat:accessURL`, `dcat:downloadURL`, and optional
`dcat:byteSize` and SPDX SHA-256 checksum data. Its response media type carries
the DCAT-AP 3.0.1 profile parameter.

`catalogue_profile="dcat"` uses the common full httk transfer profile IRI
ending in `HttpData-PULL` for every DSP distribution. The representation is
identified separately by `dcat:mediaType`. The alternate graph is served as
plain `application/ld+json` and makes no DCAT-AP media-profile claim.

Both modes keep `DataAddress.endpointType` fixed to HTTPS pull. DSP separates
control-plane transfer processing from the data plane as specified by
[DSP 2025-1](https://eclipse-dataspace-protocol-base.github.io/DataspaceProtocol/2025-1/).
File metadata follows
[DCAT-AP 3.0.1](https://semiceu.github.io/DCAT-AP/releases/3.0.1/).

## Offline validation assets

The package vendors the pinned DSP schemas, DCAT-AP 3.0.1 context and SHACL
graphs, authoritative EU CSV/JSON concepts, and the SPDX 2.3 ontology. Their
sources, licenses, and SHA-256 hashes are recorded in
`httk.serve.dsp.schemas/provenance.json`; schema and RDF validation needs no
network access.

The complete runnable two-file example is in `examples/dsp_store_catalogue/`.
