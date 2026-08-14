"""End-to-end negotiation and representation-specific transfer tests."""

import asyncio
from datetime import UTC, datetime

import pytest
from test_dsp_config import config, publication

from httk.serve.dsp import DSP_CONTEXT, DspProtocolError, DspProvider, DspPublicationRecord


class Sender:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, url: str, document: dict[str, object]) -> int:
        self.calls.append((url, document))
        return 204


def offer(pub) -> dict[str, object]:
    return {
        "@id": pub.offer_id,
        "@type": "Offer",
        "target": pub.dataset.id,
        "permission": [{"action": "use"}],
    }


def message(type_name: str, **values: object) -> dict[str, object]:
    return {"@context": [DSP_CONTEXT], "@type": type_name, **values}


def test_negotiation_selects_current_publication_and_requires_its_transfer_format() -> None:
    async def exercise() -> None:
        first, second = publication("one"), publication("two")
        sender = Sender()
        provider = DspProvider(
            config(automatic_progression=False),
            publications=tuple(DspPublicationRecord(dataset=item) for item in (first, second)),
            callback_sender=sender,
            uuid_factory=iter(["negotiation", "agreement", "transfer"]).__next__,
            utc_clock=lambda: datetime(2026, 8, 14, tzinfo=UTC),
        )
        negotiation = await provider.request_negotiation(
            message(
                "ContractRequestMessage",
                consumerPid="consumer",
                callbackAddress="https://consumer.example/callback",
                offer=offer(second),
            )
        )
        negotiation_pid = str(negotiation["providerPid"])
        await provider.send_agreement(negotiation_pid)
        await provider.verify_agreement(
            negotiation_pid,
            message(
                "ContractAgreementVerificationMessage",
                providerPid=negotiation_pid,
                consumerPid="consumer",
            ),
        )
        await provider.finalize_negotiation(negotiation_pid)

        wrong = message(
            "TransferRequestMessage",
            consumerPid="wrong-transfer",
            callbackAddress="https://consumer.example/callback",
            agreementId="urn:uuid:agreement",
            format="https://schemas.httk.org/dsp/2025-1/transfer/HttpData-PULL",
        )
        with pytest.raises(DspProtocolError, match="format"):
            await provider.request_transfer(wrong)

        transfer = await provider.request_transfer(
            message(
                "TransferRequestMessage",
                consumerPid="transfer-consumer",
                callbackAddress="https://consumer.example/callback",
                agreementId="urn:uuid:agreement",
                format=second.file_format,
            )
        )
        await provider.start_transfer(str(transfer["providerPid"]))
        assert sender.calls[-1][1]["dataAddress"] == {
            "@type": "DataAddress",
            "endpointType": "https://w3id.org/idsa/v4.1/HTTP",
            "endpoint": "https://provider.example/data/two.json",
        }

    asyncio.run(exercise())
