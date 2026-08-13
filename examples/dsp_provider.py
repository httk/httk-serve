"""Run a Tier 1 DSP provider advertising a public OPTIMADE API.

For database-backed records, wrap an ``httk.store.db.StoreEntryProvider`` with
``DspEntryProviderDatasetSource`` as shown in ``provider_from_store`` below.
The advertised OPTIMADE application can be mounted at ``/optimade`` beside
this DSP application or deployed independently at the configured public URL.
"""

from collections.abc import Mapping
from typing import Any

from httk.core import Dataset, EntryProvider

from httk.serve.dsp import (
    HTTP_ENDPOINT_TYPE,
    DcatDataService,
    DspDatasetPublication,
    DspEntryProviderDatasetSource,
    DspProvider,
    DspProviderConfig,
    create_dsp_app,
)

PUBLIC_OPTIMADE_SERVICE = DcatDataService(
    id="https://provider.example/services/optimade",
    title="Public materials OPTIMADE API",
    endpoint_url="https://provider.example/optimade/v1",
    conforms_to=("https://schemas.optimade.org/defs/v1.3/standards/optimade",),
    endpoint_description="https://www.optimade.org/specification/latest/",
)


def publication(slug: str, title: str) -> DspDatasetPublication:
    """Build one publication from inline metadata."""
    access_url = f"https://provider.example/data/{slug}"
    return DspDatasetPublication(
        dataset=Dataset(
            id=f"https://provider.example/datasets/{slug}",
            title=title,
            description=f"The {title.lower()} demonstration dataset.",
            publisher_id="https://provider.example/participants/provider",
            publisher_name="Example Provider",
        ),
        offer_id=f"https://provider.example/offers/{slug}",
        distribution_id=f"https://provider.example/distributions/{slug}-jsonld",
        data_service_id="https://provider.example/dsp/services/materials",
        data_service_title="Example DSP data service",
        access_url=access_url,
        data_address={
            "@type": "DataAddress",
            "endpointType": HTTP_ENDPOINT_TYPE,
            "endpoint": access_url,
        },
    )


def provider_config(
    *,
    datasets: tuple[DspDatasetPublication, ...] = (),
    dataset_source: DspEntryProviderDatasetSource | None = None,
) -> DspProviderConfig:
    """Build catalogue-level configuration around inline or stored data."""
    return DspProviderConfig(
        connector_root_url="https://provider.example/dsp",
        service_id="https://provider.example/dsp/services/example",
        participant_id="https://provider.example/participants/provider",
        catalog_id="https://provider.example/catalogs/example",
        catalog_title="Example materials catalogue",
        catalog_description="Two example JSON-LD datasets.",
        datasets=datasets,
        dataset_source=dataset_source,
        dcat_data_services=(PUBLIC_OPTIMADE_SERVICE,),
    )


def provider_from_store(store_entries: EntryProvider) -> DspProvider:
    """Create the same server from a store-backed entry provider.

    The example entry type is expected to expose ``id``, ``title``, and
    ``description`` record keys. Real applications can map any stored shape.
    """

    def from_record(record: Mapping[str, Any]) -> DspDatasetPublication:
        slug = str(record["id"])
        access_url = f"https://provider.example/data/{slug}"
        return DspDatasetPublication(
            dataset=Dataset(
                id=f"https://provider.example/datasets/{slug}",
                title=str(record["title"]),
                description=str(record["description"]),
                publisher_id="https://provider.example/participants/provider",
                publisher_name="Example Provider",
            ),
            offer_id=f"https://provider.example/offers/{slug}",
            distribution_id=f"https://provider.example/distributions/{slug}-jsonld",
            data_service_id="https://provider.example/dsp/services/materials",
            data_service_title="Example DSP data service",
            access_url=access_url,
            data_address={
                "@type": "DataAddress",
                "endpointType": HTTP_ENDPOINT_TYPE,
                "endpoint": access_url,
            },
        )

    source = DspEntryProviderDatasetSource(store_entries, "published_datasets", from_record)
    return DspProvider(provider_config(dataset_source=source))


config = provider_config(
    datasets=(
        publication("structures", "Crystal structures"),
        publication("calculations", "Example calculations"),
    )
)

provider = DspProvider(config)
app = create_dsp_app(provider)
