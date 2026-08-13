"""Tests for DSP business handlers and both process state machines."""

import asyncio
from datetime import UTC, datetime

import pytest
from test_dsp_config import config, multi_config, publication

from httk.serve.dsp import DSP_CONTEXT, DSP_TRANSFER_FORMAT, DspProtocolError, DspProvider


def offer(*, target: str = "https://provider.example/datasets/one") -> dict[str, object]:
    """Build the exact advertised message offer with its required top target."""
    return {
        "@id": "https://provider.example/offers/one",
        "@type": "Offer",
        "target": target,
        "permission": [{"action": "use"}],
    }


def negotiation_request(consumer_pid: str = "consumer") -> dict[str, object]:
    """Build one valid initial negotiation request."""
    return {
        "@context": [DSP_CONTEXT],
        "@type": "ContractRequestMessage",
        "consumerPid": consumer_pid,
        "callbackAddress": "https://consumer.example/callback",
        "offer": offer(),
    }


def process_message(type_name: str, provider_pid: str, consumer_pid: str, **fields: object) -> dict[str, object]:
    """Build one correlated inbound DSP process message."""
    return {
        "@context": [DSP_CONTEXT],
        "@type": type_name,
        "providerPid": provider_pid,
        "consumerPid": consumer_pid,
        **fields,
    }


def transfer_request(
    consumer_pid: str = "transfer-consumer",
    callback_address: str = "https://consumer.example/callback",
    agreement_id: str = "urn:uuid:a1",
) -> dict[str, object]:
    """Build one valid pull transfer request."""
    return {
        "@context": [DSP_CONTEXT],
        "@type": "TransferRequestMessage",
        "consumerPid": consumer_pid,
        "callbackAddress": callback_address,
        "agreementId": agreement_id,
        "format": DSP_TRANSFER_FORMAT,
    }


class Sender:
    """Record successful callback messages for assertions."""

    def __init__(self) -> None:
        """Create an empty callback recorder."""
        self.calls: list[tuple[str, dict[str, object]]] = []

    async def __call__(self, url: str, document: dict[str, object]) -> int:
        """Record one callback and acknowledge it.

        :param url: Callback URL selected by the provider.
        :param document: Callback DSP message.
        :return: A successful HTTP status.
        """
        self.calls.append((url, document))
        return 204


def provider(*, automatic: bool = False, identifiers: list[str] | None = None) -> tuple[DspProvider, Sender]:
    """Build a deterministic provider and callback recorder."""
    sender = Sender()
    source = iter(identifiers or ["negotiation", "agreement", "transfer", "spare"])
    value = DspProvider(
        config(automatic_progression=automatic),
        callback_sender=sender,
        uuid_factory=source.__next__,
        utc_clock=lambda: datetime(2026, 8, 13, 12, 34, 56, tzinfo=UTC),
    )
    return value, sender


def test_negotiation_allowed_transitions_and_agreement_fields() -> None:
    """Negotiations follow the request, offer, counter, accept, agreement, verify, finalize state machine."""

    async def exercise() -> None:
        service, sender = provider(identifiers=["n1", "a1"])
        created = await service.request_negotiation(negotiation_request())
        pid = str(created["providerPid"])
        assert created["state"] == "REQUESTED"

        await service.send_offer(pid)
        await service.counter_request(
            pid,
            process_message("ContractRequestMessage", pid, "consumer", offer=offer()),
        )
        await service.send_offer(pid)
        await service.negotiation_event(
            pid,
            process_message("ContractNegotiationEventMessage", pid, "consumer", eventType="ACCEPTED"),
        )
        await service.send_agreement(pid)
        agreed = await service.get_negotiation(pid)
        assert agreed["state"] == "AGREED"
        agreement = sender.calls[-1][1]["agreement"]
        assert agreement == {
            "@id": "urn:uuid:a1",
            "@type": "Agreement",
            "target": "https://provider.example/datasets/one",
            "permission": [{"action": "use"}],
            "assigner": "https://provider.example/participant",
            "assignee": "consumer",
            "timestamp": "2026-08-13T12:34:56Z",
        }

        await service.verify_agreement(
            pid,
            process_message("ContractAgreementVerificationMessage", pid, "consumer"),
        )
        await service.finalize_negotiation(pid)
        assert (await service.get_negotiation(pid))["state"] == "FINALIZED"
        with pytest.raises(DspProtocolError) as raised:
            await service.terminate_negotiation(pid)
        assert raised.value.status_code == 400

    asyncio.run(exercise())


def test_invalid_policy_pid_event_and_terminal_transitions_are_bad_requests() -> None:
    """Negotiations reject altered policies, PID correlation failures, invalid events, and terminal state changes."""

    async def exercise() -> None:
        service, _sender = provider(identifiers=["n1", "a1"])
        altered = negotiation_request()
        altered["offer"] = offer(target="https://wrong.example/dataset")
        with pytest.raises(DspProtocolError, match="target"):
            await service.request_negotiation(altered)
        nonfinite = negotiation_request()
        nonfinite["offer"] = {"@id": "https://provider.example/offers/one", "value": float("nan")}
        with pytest.raises(DspProtocolError, match="finite"):
            await service.request_negotiation(nonfinite)
        created = await service.request_negotiation(negotiation_request())
        pid = str(created["providerPid"])
        await service.send_offer(pid)
        with pytest.raises(DspProtocolError, match="consumerPid"):
            await service.negotiation_event(
                pid,
                process_message("ContractNegotiationEventMessage", pid, "wrong", eventType="ACCEPTED"),
            )
        with pytest.raises(DspProtocolError, match="only consumer ACCEPTED"):
            await service.negotiation_event(
                pid,
                process_message("ContractNegotiationEventMessage", pid, "consumer", eventType="FINALIZED"),
            )
        await service.negotiation_event(
            pid,
            process_message("ContractNegotiationEventMessage", pid, "consumer", eventType="ACCEPTED"),
        )
        await service.send_agreement(pid)
        await service.verify_agreement(pid, process_message("ContractAgreementVerificationMessage", pid, "consumer"))
        await service.finalize_negotiation(pid)
        with pytest.raises(DspProtocolError) as raised:
            await service.send_offer(pid)
        assert raised.value.status_code == 400

    asyncio.run(exercise())


def test_automatic_progression_finalizes_and_authorizes_pull_transfer() -> None:
    """Automatic agreement and finalization callbacks satisfy the finalized-agreement transfer prerequisite."""

    async def exercise() -> None:
        service, sender = provider(automatic=True, identifiers=["n1", "a1", "t1"])
        created = await service.request_negotiation(negotiation_request())
        pid = str(created["providerPid"])
        assert created["state"] == "REQUESTED"
        await service.drain_automatic()
        assert (await service.get_negotiation(pid))["state"] == "AGREED"
        await service.verify_agreement(pid, process_message("ContractAgreementVerificationMessage", pid, "consumer"))
        assert (await service.get_negotiation(pid))["state"] == "VERIFIED"
        await service.drain_automatic()
        assert (await service.get_negotiation(pid))["state"] == "FINALIZED"

        request = transfer_request(callback_address="https://consumer.example/callback/")
        transfer = await service.request_transfer(request)
        assert transfer == {
            "@context": [DSP_CONTEXT],
            "@type": "TransferProcess",
            "providerPid": "urn:uuid:t1",
            "consumerPid": "transfer-consumer",
            "state": "REQUESTED",
        }
        await service.drain_automatic()
        started = await service.get_transfer(str(transfer["providerPid"]))
        assert started["state"] == "STARTED"
        start = sender.calls[-1][1]
        assert start["@type"] == "TransferStartMessage"
        assert start["dataAddress"] == {
            "@type": "DataAddress",
            "endpointType": "https://w3id.org/idsa/v4.1/HTTP",
            "endpoint": "https://provider.example/data/one",
        }

        repeat = await service.request_transfer(request)
        assert repeat == started
        conflicting = dict(request)
        conflicting["callbackAddress"] = "https://other.example/callback"
        with pytest.raises(DspProtocolError, match="conflicting"):
            await service.request_transfer(conflicting)

    asyncio.run(exercise())


def test_negotiation_and_transfer_select_the_requested_dataset_publication() -> None:
    """Agreement targets and pull addresses remain correlated in a multi-dataset catalogue."""

    async def exercise() -> None:
        sender = Sender()
        service = DspProvider(
            multi_config(publication("one"), publication("two"), automatic_progression=False),
            callback_sender=sender,
            uuid_factory=iter(["n2", "a2", "t2"]).__next__,
            utc_clock=lambda: datetime(2026, 8, 13, tzinfo=UTC),
        )
        request = negotiation_request("consumer-two")
        request["offer"] = {
            "@id": "https://provider.example/offers/two",
            "@type": "Offer",
            "target": "https://provider.example/datasets/two",
            "permission": [{"action": "use"}],
        }
        negotiation = await service.request_negotiation(request)
        negotiation_pid = str(negotiation["providerPid"])
        await service.send_agreement(negotiation_pid)
        agreement = sender.calls[-1][1]["agreement"]
        assert agreement["target"] == "https://provider.example/datasets/two"
        await service.verify_agreement(
            negotiation_pid,
            process_message(
                "ContractAgreementVerificationMessage",
                negotiation_pid,
                "consumer-two",
            ),
        )
        await service.finalize_negotiation(negotiation_pid)
        transfer = await service.request_transfer(transfer_request(agreement_id="urn:uuid:a2"))
        await service.start_transfer(str(transfer["providerPid"]))

        assert sender.calls[-1][1]["dataAddress"]["endpoint"] == "https://provider.example/data/two"

    asyncio.run(exercise())


def test_transfer_requires_finalized_agreement_and_allows_its_transitions() -> None:
    """Transfers reject pre-finalization then allow start, suspend, resume, completion, and terminal rejection."""

    async def exercise() -> None:
        service, _sender = provider(identifiers=["n1", "a1", "t1"])
        created = await service.request_negotiation(negotiation_request())
        negotiation_pid = str(created["providerPid"])
        await service.send_agreement(negotiation_pid)
        request = transfer_request()
        missing = transfer_request(agreement_id="urn:uuid:missing")
        with pytest.raises(DspProtocolError, match="not found") as raised:
            await service.request_transfer(missing)
        assert raised.value.status_code == 404
        with pytest.raises(DspProtocolError, match="finalized"):
            await service.request_transfer(request)
        await service.verify_agreement(
            negotiation_pid,
            process_message("ContractAgreementVerificationMessage", negotiation_pid, "consumer"),
        )
        await service.finalize_negotiation(negotiation_pid)
        with pytest.raises(DspProtocolError, match="consumerPid"):
            await service.request_transfer(transfer_request(consumer_pid="\ud800"))
        transfer = await service.request_transfer(request)
        transfer_pid = str(transfer["providerPid"])
        assert transfer["state"] == "REQUESTED"
        await service.start_transfer(transfer_pid)
        await service.suspend_transfer(transfer_pid)
        await service.resume_transfer(
            transfer_pid,
            process_message("TransferStartMessage", transfer_pid, "transfer-consumer"),
        )
        await service.complete_transfer(transfer_pid)
        assert (await service.get_transfer(transfer_pid))["state"] == "COMPLETED"
        with pytest.raises(DspProtocolError) as raised:
            await service.terminate_transfer(transfer_pid)
        assert raised.value.status_code == 400

    asyncio.run(exercise())


def test_concurrent_transfer_idempotency_creates_one_process_and_rejects_conflicts() -> None:
    """Concurrent reuse has one provider PID, while conflicting reuse is rejected after atomic recovery."""

    async def finalized_service() -> DspProvider:
        service, _sender = provider(identifiers=["n1", "a1", "t1", "t2"])
        negotiation = await service.request_negotiation(negotiation_request())
        negotiation_pid = str(negotiation["providerPid"])
        await service.send_agreement(negotiation_pid)
        await service.verify_agreement(
            negotiation_pid,
            process_message("ContractAgreementVerificationMessage", negotiation_pid, "consumer"),
        )
        await service.finalize_negotiation(negotiation_pid)
        return service

    async def exercise() -> None:
        service = await finalized_service()
        identical = transfer_request()
        first, second = await asyncio.gather(
            service.request_transfer(identical), service.request_transfer(dict(identical))
        )
        assert first["providerPid"] == second["providerPid"] == "urn:uuid:t1"

        service = await finalized_service()
        normal = transfer_request()
        conflict = transfer_request(callback_address="https://other.example/callback")
        outcomes = await asyncio.gather(
            service.request_transfer(normal),
            service.request_transfer(conflict),
            return_exceptions=True,
        )
        successes = [outcome for outcome in outcomes if isinstance(outcome, dict)]
        failures = [outcome for outcome in outcomes if isinstance(outcome, DspProtocolError)]
        assert len(successes) == len(failures) == 1
        assert successes[0]["providerPid"] == "urn:uuid:t1"
        assert failures[0].status_code == 400
        assert failures[0].code == "duplicate-process"

    asyncio.run(exercise())


def test_concurrent_callback_commits_leave_one_acknowledged_transition() -> None:
    """A pending callback prevents a competing transition from committing over it."""

    async def exercise() -> None:
        started = asyncio.Event()
        release = asyncio.Event()

        async def sender(_url: str, _document: dict[str, object]) -> int:
            started.set()
            await release.wait()
            return 204

        service = DspProvider(
            config(automatic_progression=False),
            callback_sender=sender,
            uuid_factory=iter(["n1"]).__next__,
        )
        created = await service.request_negotiation(negotiation_request())
        pid = str(created["providerPid"])
        first = asyncio.create_task(service.send_offer(pid))
        await started.wait()
        with pytest.raises(DspProtocolError, match="pending"):
            await service.send_offer(pid)
        release.set()
        await first
        assert (await service.get_negotiation(pid))["state"] == "OFFERED"

    asyncio.run(exercise())


def test_callback_failure_requires_termination_before_any_further_transition() -> None:
    """A lost callback acknowledgement blocks new state changes until termination succeeds."""

    async def exercise() -> None:
        responses = iter([500, 500, 500, 500, 204])

        async def sender(_url: str, _document: dict[str, object]) -> int:
            return next(responses)

        service = DspProvider(
            config(automatic_progression=False),
            callback_sender=sender,
            uuid_factory=iter(["n1"]).__next__,
        )
        created = await service.request_negotiation(negotiation_request())
        pid = str(created["providerPid"])
        with pytest.raises(DspProtocolError, match="callback delivery failed"):
            await service.send_offer(pid)
        with pytest.raises(DspProtocolError, match="out of sync"):
            await service.send_offer(pid)
        await service.terminate_negotiation(pid)
        assert (await service.get_negotiation(pid))["state"] == "TERMINATED"

    asyncio.run(exercise())


def test_callback_paths_encode_consumer_pids_as_one_segment() -> None:
    """A consumer PID cannot inject path, query, fragment, or percent semantics into callbacks."""

    async def exercise() -> None:
        service, sender = provider(identifiers=["n1"])
        consumer_pid = "consumer/a?b#c%d"
        created = await service.request_negotiation(negotiation_request(consumer_pid))
        await service.send_offer(str(created["providerPid"]))
        assert sender.calls[0][0].endswith("/negotiations/consumer%2Fa%3Fb%23c%25d/offers")

    asyncio.run(exercise())
