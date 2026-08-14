"""Focused DSP/DCAT projection invariants not covered by store-native tests."""

from dataclasses import replace

import pytest
from test_dsp_config import config, publication

from httk.serve.dsp import DSP_CONTEXT, DcatDataService, DspProvider


def request() -> dict[str, object]:
    return {"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"}


def test_companion_dcat_service_is_not_added_to_dsp_catalogue() -> None:
    companion = DcatDataService(
        id="https://provider.example/services/optimade",
        title="OPTIMADE",
        endpoint_url="https://provider.example/optimade/v1",
        conforms_to=("https://schemas.optimade.org/defs/v1.3/standards/optimade",),
    )
    publications = (publication("one"), publication("two"))
    provider = DspProvider(config(dcat_data_services=(companion,)), datasets=publications)

    assert "dcat:service" not in provider.dsp_catalogue(request())
    services = {item["@id"] for item in provider.dcat_catalogue()["dcat:service"]}
    assert services == {config().service_id, companion.id}


def test_snapshot_rejects_duplicate_ids_and_mixed_publishers() -> None:
    duplicate = publication("one")
    with pytest.raises(ValueError, match="dataset IDs"):
        _ = DspProvider(config(), datasets=(duplicate, replace(duplicate, access_url="/other.csv"))).profile

    other = publication("two")
    other = replace(
        other,
        dataset=replace(other.dataset, publisher_id="https://other.example/publisher", publisher_name="Other"),
    )
    with pytest.raises(ValueError, match="same publisher"):
        _ = DspProvider(config(), datasets=(publication("one"), other)).profile
