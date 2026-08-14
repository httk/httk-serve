# DSP minimal and DCAT-AP minimal public catalogues

`httk.serve.dsp` implements a DSP 2025-1 minimal public-catalogue feature set.
Publications can be declared inline or stored under the non-OPTIMADE
`DspPublicationEntry` family. Negotiation and transfer state remains in
memory, while every catalogue request reads a fresh snapshot from the
caller-owned store.

Official DSP discovery uses:

```text
GET  /dsp/.well-known/dspace-version
POST /dsp/2025-1/catalog/request
```

`GET /dsp/2025-1/catalog` is intentionally not implemented: it is not a DSP
2025-1 endpoint. A caller that also publishes a JSON-LD representation can
mount the vocabulary-neutral `httk.serve.http.jsonld_get_app`; discovery
and protocol-specific validation belong to that caller.

## Store-native catalogue

```python
from httk.core import Dataset, DatasetDistribution
from httk.store.db import Database, SqlStore
from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    DspPublicationRecord,
)
from httk.core import Service

publication = DspDatasetPublication(
    dataset=Dataset(
        id="https://provider.example/datasets/one",
        title="Dataset one",
        description="An example dataset.",
        publisher_id="https://provider.example/participants/provider",
        publisher_name="Provider",
        distributions=(
            DatasetDistribution(
                access_url="https://provider.example/files/dataset.csv",
                byte_size=123,
                sha256="…publisher-computed lowercase SHA-256…",
            ),
        ),
    ),
)
store = SqlStore(
    Database.sqlite(),
    entry_records={DspPublicationEntry: DspPublicationRecord},
)
store.save(DspPublicationRecord(dataset=publication))
store.save(DspPublicationRecord(service=Service(
    id="https://catalogue.example/services/dcat-ap",
    title="Public DCAT-AP catalogue",
    endpoint_url="https://catalogue.example/dcat-ap",
    conforms_to=(DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
)))

config = DspProviderConfig(
    public_base_url="https://provider.example",
    dsp_mount="/dsp",
    service_id="https://provider.example/services/dsp",
    service_title="Public DSP service",
    participant_id="https://provider.example/participants/provider",
    catalog_id="https://provider.example/catalogues/main",
    catalog_title="Main catalogue",
    catalog_description="Published datasets.",
    dcat_ap_content_negotiation=True,
)
provider = DspProvider(config, store=store)
```

The store must declare `DspPublicationEntry` mapped solely to
`DspPublicationRecord`; neither application closes the store or database. Later
dataset or service envelope saves become visible without rebuilding the
provider. Duplicate dataset, offer, or distribution IDs and inconsistent
publishers are rejected when a snapshot is built. For fixed declarations, use
`DspProvider(config, publications=(DspPublicationRecord(dataset=publication),))`;
exactly one source is required.

## Replaceable catalogue policy

`DspProvider` keeps storage access, DSP message validation, negotiation and
transfer state, callbacks, and data-address handling fixed. Applications that
are developing a stricter publication profile can replace only the catalogue
requirements and projections through the public `DspCataloguePolicy` contract:

```python
provider = DspProvider(
    config,
    store=store,
    catalogue_policy=ProjectCataloguePolicy(),
)
```

The policy receives the current `DspPublicationRecord` values whenever a live
snapshot is requested. It builds the public `CatalogueProfile`, validates
profile-specific catalogue-request constraints, serializes catalogues,
datasets, and offers, and selects the ordinary or alternate catalogue
representation. `DspCatalogueRepresentation` carries the selected media type,
projection flag, and additional HTTP headers. The media type must correspond
to a response declared by the packaged DSP OpenAPI contract.

`MinimalDspCataloguePolicy` is the default and preserves the feature set
described on this page. A standards prototype should implement the protocol
independently rather than subclassing this default, so its normative decisions
remain visible and cannot change when the built-in minimal profile evolves.
The lower-level policy does not replace DSP routing, schemas, error documents,
or process state machines.

## Representations

Every DSP Distribution uses its full EU File Type IRI as both its DSP transfer
format and its DCAT `dct:format` resource. CSV and JSON therefore have distinct
transfer values. `dcat:mediaType`, direct access and download URLs, explicitly
typed byte size, and SPDX SHA-256 metadata are included in the common payload.

Ordinary catalogue requests use `application/json` and the protected DSP
context. When `dcat_ap_content_negotiation=True`, an explicit
`Accept: application/ld+json` on the same POST returns the same common JSON
value with an owned context and the profiled DCAT-AP media type. A generic
JSON-LD GET application can expose that same live document independently. It
handles GET/HEAD, JSON-LD content acceptance, ETags, caching, CORS, and an
optional profile link, but does not define discovery or claim conformance to a
retrieval protocol.

`DspDatasetPublication` is the DSP offer envelope around one neutral
`Dataset`. The dataset must contain exactly one `DatasetDistribution` with an
absolute HTTPS access URL. It never opens, measures, or hashes a local file.
Size and digest are publisher-supplied distribution metadata. `.csv` and
`.json` infer the authoritative EU File Type and IANA media-type IRIs; other
representations require `format_iri` and `media_type_iri` explicitly on the
distribution.

The package vendors pinned DSP schemas, DCAT-AP 3.0.1 SHACL material, EU
CSV/JSON vocabulary concepts, and the SPDX ontology with provenance and
integrity hashes. The complete runnable example is in
`examples/dsp_store_catalogue/`.
