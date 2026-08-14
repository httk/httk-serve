"""Focused DSP/DCAT projection invariants not covered by store-native tests."""

from dataclasses import replace

import pytest
from test_dsp_config import config, publication

from httk.serve.dsp import (
    DCAT_AP_3_0_1_PROFILE,
    DCAT_AP_MINIMAL_PROFILE,
    DSP_CONTEXT,
    DcatDataService,
    DspProvider,
)


def request() -> dict[str, object]:
    return {"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"}


def test_companion_dcat_service_is_advertised_in_both_catalogues() -> None:
    companion = DcatDataService(
        id="https://provider.example/services/dcat-ap",
        title="DCAT-AP",
        endpoint_url="https://provider.example/dcat-ap/catalogue/",
        conforms_to=(DCAT_AP_MINIMAL_PROFILE, DCAT_AP_3_0_1_PROFILE),
    )
    publications = (publication("one"), publication("two"))
    provider = DspProvider(config(dcat_ap_service=companion), datasets=publications)

    dsp = provider.dsp_catalogue(request())
    services = {item["@id"] for item in dsp["service"]}
    assert services == {config().service_id, companion.id}
    dcat = provider.dcat_catalogue()
    assert {key: value for key, value in dsp.items() if key != "@context"} == {
        key: value for key, value in dcat.items() if key != "@context"
    }


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
