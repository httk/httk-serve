"""Tests for locked DSP process snapshots and reservation commits."""

import asyncio

import pytest

from httk.serve.dsp import NegotiationRecord, TransferRecord
from httk.serve.dsp.models import freeze_json
from httk.serve.dsp.state import DspState


def negotiation() -> NegotiationRecord:
    """Build a minimal immutable negotiation snapshot."""
    policy = freeze_json({"@id": "offer", "@type": "Offer", "target": "dataset", "permission": [{"action": "use"}]})
    assert isinstance(policy, dict) is False
    return NegotiationRecord("provider", "consumer", "https://consumer.example/callback", "REQUESTED", policy)


def transfer() -> TransferRecord:
    """Build a minimal immutable transfer snapshot."""
    return TransferRecord(
        "transfer", "consumer-transfer", "https://consumer.example/callback", "agreement", "HttpData-PULL", "REQUESTED"
    )


def test_reservation_commits_only_its_current_token() -> None:
    """A stale callback token cannot overwrite the current process snapshot."""

    async def exercise() -> None:
        state = DspState()
        await state.create_negotiation(negotiation())
        record, token = await state.reserve_negotiation(
            "provider", expected_states=frozenset({"REQUESTED"}), transition="offer"
        )
        assert record.pending_transition is None
        assert not await state.commit_negotiation("provider", "other", state="OFFERED", agreement=None)
        assert (await state.negotiation("provider")).state == "REQUESTED"
        assert await state.commit_negotiation("provider", token, state="OFFERED", agreement=None)
        assert (await state.negotiation("provider")).state == "OFFERED"

    asyncio.run(exercise())


def test_transfer_consumer_id_creation_is_atomic() -> None:
    """Concurrent same-consumer creations recover one existing transfer only."""

    async def exercise() -> None:
        state = DspState()
        first = transfer()
        second = TransferRecord(
            "other", first.consumer_pid, first.callback_address, first.agreement_id, first.format, first.state
        )
        received = await asyncio.gather(
            state.create_or_transfer_for_consumer(first), state.create_or_transfer_for_consumer(second)
        )
        stored = [record for record, created in received if created]
        recovered = [record for record, created in received if not created]
        assert len(stored) == len(recovered) == 1
        assert stored[0].provider_pid == recovered[0].provider_pid

    asyncio.run(exercise())


def test_out_of_sync_transfer_allows_only_termination_recovery() -> None:
    """An unacknowledged callback blocks normal transfer actions until termination is acknowledged."""

    async def exercise() -> None:
        state = DspState()
        await state.create_transfer(transfer())
        _record, token = await state.reserve_transfer(
            "transfer", expected_states=frozenset({"REQUESTED"}), transition="start"
        )
        assert await state.fail_transfer("transfer", token, detail="acknowledgement lost", retries=2)
        with pytest.raises(RuntimeError, match="out of sync"):
            await state.reserve_transfer("transfer", expected_states=frozenset({"REQUESTED"}), transition="start")
        _record, termination = await state.reserve_transfer(
            "transfer", expected_states=frozenset({"REQUESTED"}), transition="termination"
        )
        assert await state.commit_transfer("transfer", termination, state="TERMINATED")
        assert (await state.transfer("transfer")).delivery.out_of_sync is False

    asyncio.run(exercise())
