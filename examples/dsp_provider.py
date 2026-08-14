"""Run a small inline DSP provider advertising two public datasets.

For a durable, live catalogue backed by ``SqlStore``, see
``examples/dsp_store_catalogue/server.py``.
"""

from httk.core import Dataset

from httk.serve.dsp import (
    DcatDataService,
    DspDatasetPublication,
    DspProvider,
    DspProviderConfig,
    create_dsp_app,
)

PUBLISHER_ID = "https://provider.example/participants/provider"

PUBLIC_OPTIMADE_SERVICE = DcatDataService(
    id="https://provider.example/services/optimade",
    title="Public materials OPTIMADE API",
    endpoint_url="https://provider.example/optimade/v1",
    conforms_to=("https://schemas.optimade.org/defs/v1.3/standards/optimade",),
    endpoint_description="https://www.optimade.org/specification/latest/",
)


def publication(slug: str, title: str) -> DspDatasetPublication:
    """Build one inline JSON publication."""
    return DspDatasetPublication(
        dataset=Dataset(
            id=f"https://provider.example/datasets/{slug}",
            title=title,
            description=f"The {title.lower()} demonstration dataset.",
            publisher_id=PUBLISHER_ID,
            publisher_name="Example Provider",
        ),
        access_url=f"https://provider.example/data/{slug}.json",
    )


config = DspProviderConfig(
    public_base_url="https://provider.example",
    service_id="https://provider.example/dsp/services/example",
    service_title="Example DSP data service",
    participant_id=PUBLISHER_ID,
    catalog_id="https://provider.example/catalogs/example",
    catalog_title="Example materials catalogue",
    catalog_description="Two example JSON datasets.",
    catalogue_profile="dcat-ap-3.0.1",
    dcat_data_services=(PUBLIC_OPTIMADE_SERVICE,),
)

provider = DspProvider(
    config,
    datasets=(
        publication("structures", "Crystal structures"),
        publication("calculations", "Example calculations"),
    ),
)
app = create_dsp_app(provider)
