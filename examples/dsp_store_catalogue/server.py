"""Serve two public files and a store-native DSP catalogue.

This example needs ``httk-store[db]`` and assumes the application is deployed
behind HTTPS at ``https://provider.example``.  The file routes are public: DSP
negotiation advertises them but does not gate access to them.
"""

import hashlib
from pathlib import Path

import uvicorn
from httk.core import Dataset, DatasetDistribution, Service
from httk.store import Backend, SqlStore

from httk.serve import ASGIAppMount, compose_asgi_apps
from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    DspPublicationRecord,
    create_dsp_app,
)
from httk.serve.http import create_file_map_app, jsonld_get_app

HERE = Path(__file__).parent
DATASET1 = HERE / "dataset1.csv"
DATASET2 = HERE / "dataset2.json"


def supplied_file_metadata(path: Path) -> tuple[int, str]:
    """Compute publisher-supplied metadata before declaring a publication."""
    content = path.read_bytes()
    return len(content), hashlib.sha256(content).hexdigest()


size1, sha1 = supplied_file_metadata(DATASET1)
size2, sha2 = supplied_file_metadata(DATASET2)
publisher_id = "https://provider.example/participants/example"
publications = (
    DspDatasetPublication(
        Dataset(
            "https://provider.example/datasets/one",
            "Tabular example",
            "A small CSV dataset.",
            publisher_id,
            "Example publisher",
            (
                DatasetDistribution(
                    access_url="/files/dataset1.csv",
                    byte_size=size1,
                    sha256=sha1,
                ),
            ),
        ),
    ),
    DspDatasetPublication(
        Dataset(
            "https://provider.example/datasets/two",
            "JSON example",
            "A small JSON dataset.",
            publisher_id,
            "Example publisher",
            (
                DatasetDistribution(
                    access_url="/files/dataset2.json",
                    byte_size=size2,
                    sha256=sha2,
                ),
            ),
        ),
    ),
)

store = SqlStore(
    Backend.sqlite(),
    entry_records={DspPublicationEntry: DspPublicationRecord},
)
for publication in publications:
    store.save(DspPublicationRecord(dataset=publication))
store.save(
    DspPublicationRecord(
        service=Service(
            id="https://provider.example/services/dcat-ap",
            title="Public DCAT-AP catalogue",
            endpoint_url="https://provider.example/dcat-ap/catalogue/",
            conforms_to=(DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
        )
    )
)

config = DspProviderConfig(
    public_base_url="https://provider.example",
    dsp_mount="/dsp",
    service_id="https://provider.example/services/dsp-download",
    service_title="DSP HTTPS downloads",
    participant_id=publisher_id,
    catalog_id="https://provider.example/catalogues/example",
    catalog_title="Example datasets",
    catalog_description="Two files served by a store-native DSP catalogue.",
    dcat_ap_content_negotiation=True,
)

# DSP minimal uses the representation's EU file-type IRI as each transfer value, so
# CSV and JSON deliberately negotiate different values.
files_app = create_file_map_app({"/dataset1.csv": DATASET1, "/dataset2.json": DATASET2})
provider = DspProvider(config, store=store)
dsp_app = create_dsp_app(provider)
dcat_ap_app = jsonld_get_app(
    provider.dcat_catalogue,
    media_type=f'application/ld+json; profile="{DCAT_AP_3_0_1_PROFILE}"',
    profile=config.dcat_ap_profile,
)
app = compose_asgi_apps(
    [
        ASGIAppMount("/files", files_app),
        ASGIAppMount("/dsp", dsp_app),
        ASGIAppMount("/dcat-ap/catalogue", dcat_ap_app),
    ],
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
