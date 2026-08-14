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
2025-1 endpoint. The independent DCAT-AP minimal GET application is created
with `httk.serve.dcat_ap.create_dcat_ap_app`.

## Store-native catalogue

```python
from httk.core import Dataset
from httk.store.db import Database, SqlStore
from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DcatDataService,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
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
    service_id="https://provider.example/services/dsp",
    service_title="Public DSP service",
    participant_id="https://provider.example/participants/provider",
    catalog_id="https://provider.example/catalogues/main",
    catalog_title="Main catalogue",
    catalog_description="Published datasets.",
    dcat_ap_service=DcatDataService(
        id="https://provider.example/services/dcat-ap",
        title="Public DCAT-AP catalogue",
        endpoint_url="https://provider.example/dcat-ap/catalogue/",
        conforms_to=(DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
    ),
    dcat_ap_content_negotiation=True,
)
provider = DspProvider(config, store=store)
```

The store must declare `DspPublicationEntry`; neither application closes the
store or database. Later `save()` calls become visible without rebuilding the
provider. Duplicate dataset, offer, or distribution IDs and inconsistent
publishers are rejected when a snapshot is built. For fixed declarations, use
`DspProvider(config, datasets=(publication,))`; exactly one source is required.

## Representations

Every DSP Distribution uses its full EU File Type IRI as both its DSP transfer
format and its DCAT `dct:format` resource. CSV and JSON therefore have distinct
transfer values. `dcat:mediaType`, direct access and download URLs, explicitly
typed byte size, and SPDX SHA-256 metadata are included in the common payload.

Ordinary catalogue requests use `application/json` and the protected DSP
context. When `dcat_ap_content_negotiation=True`, an explicit
`Accept: application/ld+json` on the same POST returns the same common JSON
value with an owned context and the profiled DCAT-AP media type. The companion
GET application returns that same DCAT-AP representation and exposes
`/.well-known/dcat-ap-minimal` discovery by default. Both profile identifiers
and the discovery path are configurable, so an external standard can apply its
own identity without being built into this package.

`DspDatasetPublication` never opens, measures, or hashes a local file. Size
and digest are publisher-supplied metadata. `.csv` and `.json` infer the
authoritative EU File Type and IANA media-type IRIs; other representations
require both IRIs explicitly.

The package vendors pinned DSP schemas, DCAT-AP 3.0.1 SHACL material, EU
CSV/JSON vocabulary concepts, and the SPDX ontology with provenance and
integrity hashes. The complete runnable example is in
`examples/dsp_store_catalogue/`.
