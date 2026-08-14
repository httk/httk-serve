"""Serve two public files and a store-native DSP catalogue.

This example needs ``httk-store[db]`` and assumes the application is deployed
behind HTTPS at ``https://provider.example``.  The file routes are public: DSP
negotiation advertises them but does not gate access to them.
"""

import hashlib
from pathlib import Path

import uvicorn
from httk.core import Dataset
from httk.store.db import Database, SqlStore
from starlette.applications import Starlette
from starlette.routing import Mount

from httk.serve.dsp import (
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    DspPublicationEntry,
    create_dsp_app,
)
from httk.serve.web import create_file_map_app

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
        ),
        "/files/dataset1.csv",
        byte_size=size1,
        sha256=sha1,
    ),
    DspDatasetPublication(
        Dataset(
            "https://provider.example/datasets/two",
            "JSON example",
            "A small JSON dataset.",
            publisher_id,
            "Example publisher",
        ),
        "/files/dataset2.json",
        byte_size=size2,
        sha256=sha2,
    ),
)

store = SqlStore(
    Database.sqlite(),
    entry_records={DspPublicationEntry: DspDatasetPublication},
)
for publication in publications:
    store.save(publication)

config = DspProviderConfig(
    public_base_url="https://provider.example",
    dsp_mount="/dsp",
    service_id="https://provider.example/services/dsp-download",
    service_title="DSP HTTPS downloads",
    participant_id=publisher_id,
    catalog_id="https://provider.example/catalogues/example",
    catalog_title="Example datasets",
    catalog_description="Two files served by a store-native DSP catalogue.",
    catalogue_profile="dcat-ap-3.0.1",
)

# Strict DSP + DCAT-AP uses the representation's EU file-type IRI as each
# transfer value, so CSV and JSON negotiate different values.  Selecting
# catalogue_profile="dcat" instead gives every distribution the shared httk
# HttpData-PULL profile, at the cost of making no DCAT-AP conformance claim.
files_app = create_file_map_app({"/dataset1.csv": DATASET1, "/dataset2.json": DATASET2})
dsp_app = create_dsp_app(DspProvider(config, store=store))
app = Starlette(routes=[Mount("/files", app=files_app), Mount("/dsp", app=dsp_app)])

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8080)
