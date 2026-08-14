"""Coverage for the public replaceable DSP catalogue-policy seam."""

from collections.abc import Mapping

import pytest
from starlette.testclient import TestClient
from test_dsp_config import config, publication

from httk.serve.dsp import (
    DSP_CONTEXT,
    CatalogueProfile,
    DatasetProfile,
    DspCatalogueRepresentation,
    DspProvider,
    DspProviderConfig,
    DspPublicationRecord,
    JsonValue,
    MinimalDspCataloguePolicy,
    OfferProfile,
    create_dsp_app,
)


class VisiblePolicy:
    """Small external-style policy proving every public hook is honored."""

    def __init__(self) -> None:
        self.delegate = MinimalDspCataloguePolicy()
        self.profile_builds = 0

    def build_profile(
        self,
        provider_config: DspProviderConfig,
        publications: tuple[DspPublicationRecord, ...],
    ) -> CatalogueProfile:
        self.profile_builds += 1
        return self.delegate.build_profile(provider_config, publications)

    def validate_catalogue_request(
        self,
        provider_config: DspProviderConfig,
        message: Mapping[str, object],
    ) -> None:
        self.delegate.validate_catalogue_request(provider_config, message)

    def select_catalogue_representation(
        self,
        provider_config: DspProviderConfig,
        accept: str | None,
    ) -> DspCatalogueRepresentation:
        del provider_config, accept
        return DspCatalogueRepresentation("application/json", headers=(("X-Catalogue-Policy", "visible"),))

    def serialize_catalogue(
        self,
        profile: CatalogueProfile,
        *,
        alternate: bool,
    ) -> dict[str, JsonValue]:
        document = self.delegate.serialize_catalogue(profile, alternate=alternate)
        document["dct:title"] = "Policy-owned catalogue"
        return document

    def serialize_dataset(self, profile: DatasetProfile) -> dict[str, JsonValue]:
        document = self.delegate.serialize_dataset(profile)
        document["dct:title"] = "Policy-owned dataset"
        return document

    def serialize_offer(self, offer: OfferProfile, *, include_target: bool) -> dict[str, JsonValue]:
        return self.delegate.serialize_offer(offer, include_target=include_target)


def test_default_policy_remains_the_builtin_minimal_profile() -> None:
    provider = DspProvider(
        config(),
        publications=(DspPublicationRecord(dataset=publication()),),
    )
    assert isinstance(provider.catalogue_policy, MinimalDspCataloguePolicy)
    assert (
        provider.dsp_catalogue({"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"})["dct:title"]
        == config().catalog_title
    )


def test_external_policy_owns_live_profiles_documents_and_http_representation() -> None:
    policy = VisiblePolicy()
    provider = DspProvider(
        config(),
        publications=(DspPublicationRecord(dataset=publication()),),
        catalogue_policy=policy,
    )

    with TestClient(create_dsp_app(provider), base_url="https://provider.example") as client:
        response = client.post(
            "/2025-1/catalog/request",
            json={"@context": [DSP_CONTEXT], "@type": "CatalogRequestMessage"},
        )
        dataset = client.get(f"/2025-1/catalog/datasets/{publication().dataset.id}")

    assert response.status_code == 200
    assert response.headers["x-catalogue-policy"] == "visible"
    assert response.json()["dct:title"] == "Policy-owned catalogue"
    assert dataset.json()["dct:title"] == "Policy-owned dataset"
    assert policy.profile_builds == 2


def test_policy_and_representation_contracts_reject_invalid_objects() -> None:
    with pytest.raises(TypeError, match="DspCataloguePolicy"):
        DspProvider(
            config(),
            publications=(DspPublicationRecord(dataset=publication()),),
            catalogue_policy=object(),  # type: ignore[arg-type]
        )
    with pytest.raises(ValueError, match="unique"):
        DspCatalogueRepresentation(
            "application/json",
            headers=(("Vary", "Accept"), ("vary", "Origin")),
        )
    with pytest.raises(ValueError, match="headers"):
        DspCatalogueRepresentation(
            "application/json",
            headers=(("X-Profile", "visible\r\nX-Injected: true"),),
        )
