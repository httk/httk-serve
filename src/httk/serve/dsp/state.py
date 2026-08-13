"""Locked, non-durable state repository for DSP negotiations and transfers."""

import asyncio
from collections.abc import Callable
from dataclasses import replace

from .models import AgreementRecord, DeliveryStatus, NegotiationRecord, TransferRecord


class DspState:
    """Own immutable DSP process snapshots behind one asynchronous lock.

    The repository is deliberately in-memory only. Callback transitions are
    reserved with a unique pending token while holding the lock, sent by the
    provider outside the lock, then committed only when the reservation still
    belongs to that callback.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._negotiations: dict[str, NegotiationRecord] = {}
        self._transfers: dict[str, TransferRecord] = {}
        self._next_token = 0

    async def create_negotiation(self, record: NegotiationRecord) -> NegotiationRecord:
        """Store a new negotiation process.

        :param record: Initial negotiation record to store.
        :return: Stored immutable snapshot.
        :raises ValueError: If its provider process identifier already exists.
        """
        async with self._lock:
            if record.provider_pid in self._negotiations:
                raise ValueError("negotiation providerPid already exists")
            self._negotiations[record.provider_pid] = record
            return record

    async def create_transfer(self, record: TransferRecord) -> TransferRecord:
        """Store a new transfer process.

        :param record: Initial transfer record to store.
        :return: Stored immutable snapshot.
        :raises ValueError: If its provider process identifier already exists.
        """
        async with self._lock:
            if record.provider_pid in self._transfers:
                raise ValueError("transfer providerPid already exists")
            self._transfers[record.provider_pid] = record
            return record

    async def create_or_transfer_for_consumer(self, record: TransferRecord) -> tuple[TransferRecord, bool]:
        """Atomically create a transfer or recover one with the same consumer PID.

        :param record: Candidate initial transfer record.
        :return: ``(record, True)`` when stored, otherwise the existing record
            and ``False``.
        :raises ValueError: If its provider process identifier already exists.
        """
        async with self._lock:
            existing = next(
                (candidate for candidate in self._transfers.values() if candidate.consumer_pid == record.consumer_pid),
                None,
            )
            if existing is not None:
                return existing, False
            if record.provider_pid in self._transfers:
                raise ValueError("transfer providerPid already exists")
            self._transfers[record.provider_pid] = record
            return record, True

    async def get_or_create_transfer_for_consumer(
        self,
        consumer_pid: str,
        create: Callable[[], TransferRecord],
    ) -> tuple[TransferRecord, bool]:
        """Atomically recover or construct one transfer for a consumer process ID.

        ``create`` runs only when no existing consumer process is found and is
        called while the lock is held. It must perform no I/O; this lets callers
        defer ID allocation until an idempotent retry is known to be new.

        :param consumer_pid: Consumer transfer-process identifier to recover.
        :param create: Zero-argument constructor for a new immutable record.
        :return: ``(record, True)`` when stored, otherwise the existing record
            and ``False``.
        :raises ValueError: If the constructed record conflicts or uses a different consumer PID.
        """
        async with self._lock:
            existing = next(
                (candidate for candidate in self._transfers.values() if candidate.consumer_pid == consumer_pid),
                None,
            )
            if existing is not None:
                return existing, False
            record = create()
            if record.consumer_pid != consumer_pid:
                raise ValueError("constructed transfer consumerPid does not match the requested consumerPid")
            if record.provider_pid in self._transfers:
                raise ValueError("transfer providerPid already exists")
            self._transfers[record.provider_pid] = record
            return record, True

    async def negotiation(self, provider_pid: str) -> NegotiationRecord:
        """Return one negotiation snapshot.

        :param provider_pid: Provider process identifier to resolve.
        :return: Immutable negotiation snapshot.
        :raises KeyError: If no such process exists.
        """
        async with self._lock:
            return self._negotiations[provider_pid]

    async def transfer(self, provider_pid: str) -> TransferRecord:
        """Return one transfer snapshot.

        :param provider_pid: Provider transfer-process identifier to resolve.
        :return: Immutable transfer snapshot.
        :raises KeyError: If no such process exists.
        """
        async with self._lock:
            return self._transfers[provider_pid]

    async def transfer_for_consumer(self, consumer_pid: str) -> TransferRecord | None:
        """Return an existing transfer identified by its consumer process ID.

        :param consumer_pid: Consumer transfer-process identifier to find.
        :return: Matching immutable snapshot, or ``None`` when absent.
        """
        async with self._lock:
            return next((record for record in self._transfers.values() if record.consumer_pid == consumer_pid), None)

    async def finalized_agreement(self, agreement_id: str) -> AgreementRecord | None:
        """Return an agreement only if its negotiation has reached ``FINALIZED``.

        :param agreement_id: Agreement identifier to resolve.
        :return: Finalized agreement record, or ``None`` when unavailable.
        """
        async with self._lock:
            for record in self._negotiations.values():
                if record.state == "FINALIZED" and record.agreement is not None and record.agreement.id == agreement_id:
                    return record.agreement
        return None

    async def agreement(self, agreement_id: str) -> tuple[AgreementRecord, str] | None:
        """Return an agreement and its negotiation state regardless of finalization.

        :param agreement_id: Agreement identifier to resolve.
        :return: Agreement record paired with its current negotiation state, or
            ``None`` when no process owns the identifier.
        """
        async with self._lock:
            for record in self._negotiations.values():
                if record.agreement is not None and record.agreement.id == agreement_id:
                    return record.agreement, record.state
        return None

    async def reserve_negotiation(
        self,
        provider_pid: str,
        *,
        expected_states: frozenset[str],
        transition: str,
    ) -> tuple[NegotiationRecord, str]:
        """Reserve an outbound negotiation transition without holding the lock for I/O.

        :param provider_pid: Provider process identifier to transition.
        :param expected_states: States from which the transition is valid.
        :param transition: Descriptive local transition name.
        :return: Pre-transition snapshot and unique reservation token.
        :raises KeyError: If no such process exists.
        :raises RuntimeError: If another callback is pending or the state is invalid.
        """
        async with self._lock:
            record = self._negotiations[provider_pid]
            self._assert_reservable(
                record.state,
                record.pending_transition,
                record.delivery,
                expected_states,
                allow_out_of_sync=transition == "termination",
            )
            token = self._new_token(transition)
            self._negotiations[provider_pid] = replace(record, pending_transition=token)
            return record, token

    async def reserve_transfer(
        self,
        provider_pid: str,
        *,
        expected_states: frozenset[str],
        transition: str,
    ) -> tuple[TransferRecord, str]:
        """Reserve an outbound transfer transition without holding the lock for I/O.

        :param provider_pid: Provider transfer-process identifier to transition.
        :param expected_states: States from which the transition is valid.
        :param transition: Descriptive local transition name.
        :return: Pre-transition snapshot and unique reservation token.
        :raises KeyError: If no such process exists.
        :raises RuntimeError: If another callback is pending or the state is invalid.
        """
        async with self._lock:
            record = self._transfers[provider_pid]
            self._assert_reservable(
                record.state,
                record.pending_transition,
                record.delivery,
                expected_states,
                allow_out_of_sync=transition == "termination",
            )
            token = self._new_token(transition)
            self._transfers[provider_pid] = replace(record, pending_transition=token)
            return record, token

    async def commit_negotiation(
        self,
        provider_pid: str,
        token: str,
        *,
        state: str,
        agreement: AgreementRecord | None,
    ) -> bool:
        """Commit an acknowledged negotiation callback when its token still matches.

        :param provider_pid: Provider process identifier to update.
        :param token: Reservation token returned by :meth:`reserve_negotiation`.
        :param state: Newly acknowledged DSP state.
        :param agreement: Agreement to store, if an agreement was acknowledged.
        :return: Whether this reservation was still current and was committed.
        """
        async with self._lock:
            record = self._negotiations.get(provider_pid)
            if record is None or record.pending_transition != token:
                return False
            self._negotiations[provider_pid] = replace(
                record,
                state=state,
                agreement=agreement if agreement is not None else record.agreement,
                pending_transition=None,
                delivery=DeliveryStatus(),
            )
            return True

    async def commit_transfer(self, provider_pid: str, token: str, *, state: str) -> bool:
        """Commit an acknowledged transfer callback when its token still matches.

        :param provider_pid: Provider transfer-process identifier to update.
        :param token: Reservation token returned by :meth:`reserve_transfer`.
        :param state: Newly acknowledged DSP state.
        :return: Whether this reservation was still current and was committed.
        """
        async with self._lock:
            record = self._transfers.get(provider_pid)
            if record is None or record.pending_transition != token:
                return False
            self._transfers[provider_pid] = replace(
                record, state=state, pending_transition=None, delivery=DeliveryStatus()
            )
            return True

    async def fail_negotiation(self, provider_pid: str, token: str, *, detail: str, retries: int) -> bool:
        """Record an unacknowledged negotiation callback failure.

        :param provider_pid: Provider process identifier to update.
        :param token: Reservation token returned by :meth:`reserve_negotiation`.
        :param detail: Safe callback failure detail.
        :param retries: Delivery attempts used for the failed callback.
        :return: Whether this reservation was still current and was updated.
        """
        async with self._lock:
            record = self._negotiations.get(provider_pid)
            if record is None or record.pending_transition != token:
                return False
            self._negotiations[provider_pid] = replace(
                record,
                pending_transition=None,
                delivery=DeliveryStatus(last_error=detail, retry_count=retries, out_of_sync=True),
            )
            return True

    async def fail_transfer(self, provider_pid: str, token: str, *, detail: str, retries: int) -> bool:
        """Record an unacknowledged transfer callback failure.

        :param provider_pid: Provider transfer-process identifier to update.
        :param token: Reservation token returned by :meth:`reserve_transfer`.
        :param detail: Safe callback failure detail.
        :param retries: Delivery attempts used for the failed callback.
        :return: Whether this reservation was still current and was updated.
        """
        async with self._lock:
            record = self._transfers.get(provider_pid)
            if record is None or record.pending_transition != token:
                return False
            self._transfers[provider_pid] = replace(
                record,
                pending_transition=None,
                delivery=DeliveryStatus(last_error=detail, retry_count=retries, out_of_sync=True),
            )
            return True

    async def receive_negotiation(
        self, provider_pid: str, *, expected_states: frozenset[str], state: str
    ) -> NegotiationRecord:
        """Commit an inbound negotiation transition atomically.

        :param provider_pid: Provider process identifier to update.
        :param expected_states: States from which the inbound transition is valid.
        :param state: Newly received DSP state.
        :return: Updated immutable process snapshot.
        :raises KeyError: If no such process exists.
        :raises RuntimeError: If another callback is pending or the state is invalid.
        """
        async with self._lock:
            record = self._negotiations[provider_pid]
            self._assert_reservable(
                record.state,
                record.pending_transition,
                record.delivery,
                expected_states,
                allow_out_of_sync=state == "TERMINATED",
            )
            updated = replace(
                record, state=state, delivery=DeliveryStatus() if state == "TERMINATED" else record.delivery
            )
            self._negotiations[provider_pid] = updated
            return updated

    async def receive_transfer(
        self, provider_pid: str, *, expected_states: frozenset[str], state: str
    ) -> TransferRecord:
        """Commit an inbound transfer transition atomically.

        :param provider_pid: Provider transfer-process identifier to update.
        :param expected_states: States from which the inbound transition is valid.
        :param state: Newly received DSP state.
        :return: Updated immutable process snapshot.
        :raises KeyError: If no such process exists.
        :raises RuntimeError: If another callback is pending or the state is invalid.
        """
        async with self._lock:
            record = self._transfers[provider_pid]
            self._assert_reservable(
                record.state,
                record.pending_transition,
                record.delivery,
                expected_states,
                allow_out_of_sync=state == "TERMINATED",
            )
            updated = replace(
                record, state=state, delivery=DeliveryStatus() if state == "TERMINATED" else record.delivery
            )
            self._transfers[provider_pid] = updated
            return updated

    def _new_token(self, transition: str) -> str:
        """Return a unique local token while the repository lock is held."""
        self._next_token += 1
        return f"{transition}:{self._next_token}"

    @staticmethod
    def _assert_reservable(
        state: str,
        pending: str | None,
        delivery: DeliveryStatus,
        expected_states: frozenset[str],
        *,
        allow_out_of_sync: bool,
    ) -> None:
        """Raise a stable local failure for pending or illegal transitions."""
        if pending is not None:
            raise RuntimeError("a callback transition is already pending")
        if delivery.out_of_sync and not allow_out_of_sync:
            raise RuntimeError("process is out of sync; termination is required")
        if state not in expected_states:
            raise RuntimeError(f"transition is invalid from state {state}")


__all__ = ["DspState"]
