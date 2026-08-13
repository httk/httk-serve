"""Run the minimal in-memory DSP provider demonstration."""

from httk.core import Dataset

from httk.serve.dsp import DspProvider, DspProviderConfig, create_dsp_app

config = DspProviderConfig(
    connector_root_url="https://provider.example/dsp",
    service_id="https://provider.example/dsp/services/example",
    participant_id="https://provider.example/participants/provider",
    catalog_id="https://provider.example/catalogs/example",
    catalog_title="Example materials catalogue",
    catalog_description="One example JSON-LD dataset.",
    dataset=Dataset(
        id="https://provider.example/datasets/example",
        title="Example dataset",
        description="A static example dataset.",
        publisher_id="https://provider.example/participants/provider",
        publisher_name="Example Provider",
    ),
    offer_id="https://provider.example/offers/example",
    distribution_id="https://provider.example/distributions/example-jsonld",
    data_service_id="https://provider.example/dsp/services/example",
    data_service_title="Example DSP data service",
    access_url="https://provider.example/data/example",
    data_address={
        "@type": "DataAddress",
        "endpointType": "https://w3id.org/idsa/v4.1/HTTP",
        "endpoint": "https://provider.example/data/example",
    },
)

provider = DspProvider(config)
app = create_dsp_app(provider)
