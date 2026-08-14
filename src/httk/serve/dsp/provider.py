"""In-memory business implementation of the constrained DSP provider."""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from urllib.parse import quote
from uuid import UUID, uuid4

from httk.store import EntryStore

from .callbacks import CallbackSender, CallbackTransportError, DefaultCallbackSender, callback_url
from .config import (
    DSP_CONTEXT,
    HTTP_ENDPOINT_TYPE,
    HTTP_PULL_PROFILE,
    DspDatasetPublication,
    DspProviderConfig,
    DspPublicationEntry,
    _https_url,
)
from .models import (
    AgreementRecord,
    CatalogueProfile,
    DataServiceProfile,
    DatasetProfile,
    DcatDataServiceProfile,
    DistributionProfile,
    DspProtocolError,
    ErrorKind,
    FrozenJsonValue,
    JsonValue,
    NegotiationRecord,
    OfferProfile,
    TransferRecord,
    freeze_json,
    thaw_json,
)
from .serializers import (
    offer_policy,
    serialize_agreement,
    serialize_dcat_catalogue,
    serialize_dsp_catalogue,
    serialize_dsp_dataset_document,
    serialize_negotiation,
    serialize_transfer,
    version_document,
)
from .state import DspState

type UuidFactory = Callable[[], UUID | str]
type UtcClock = Callable[[], datetime]
type _AutomaticCallback = Callable[[], Awaitable[None]]

_NEGOTIATION_TERMINAL = frozenset({"FINALIZED", "TERMINATED"})
_TRANSFER_TERMINAL = frozenset({"COMPLETED", "TERMINATED"})

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class _AutomaticBatch:
    """Hold callback actions reserved for one response's post-send hook."""

    actions: list[_AutomaticCallback] = field(default_factory=list)


def _profile_from_declarations(
    config: DspProviderConfig,
    declarations: tuple[DspDatasetPublication, ...],
) -> CatalogueProfile:
    """Build and cross-validate the immutable catalogue snapshot."""
    if not declarations:
        raise ValueError("the DSP publication source contains no dataset publications")
    first_publisher = (declarations[0].dataset.publisher_id, declarations[0].dataset.publisher_name)
    if any(
        (publication.dataset.publisher_id, publication.dataset.publisher_name) != first_publisher
        for publication in declarations[1:]
    ):
        raise ValueError("all datasets in one catalogue must have the same publisher identifier and name")
    for attribute in ("id", "offer_id", "distribution_id"):
        values = [
            publication.dataset.id if attribute == "id" else getattr(publication, attribute)
            for publication in declarations
        ]
        if len(values) != len(set(values)):
            label = "dataset IDs" if attribute == "id" else attribute.replace("_", " ") + "s"
            raise ValueError(f"{label} must be unique within the catalogue")
    datasets: list[DatasetProfile] = []
    for publication in declarations:
        assert publication.file_format is not None
        assert publication.media_type is not None
        assert publication.offer_id is not None
        assert publication.distribution_id is not None
        data_service = DataServiceProfile(
            config.service_id,
            config.service_title,
            config.service_endpoint_url,
        )
        access_url = config.resolve_access_url(publication.access_url)
        transfer_format = publication.file_format if config.catalogue_profile == "dcat-ap-3.0.1" else HTTP_PULL_PROFILE
        distribution = DistributionProfile(
            publication.distribution_id,
            transfer_format,
            publication.file_format,
            publication.media_type,
            access_url,
            data_service,
            publication.byte_size,
            publication.sha256,
        )
        datasets.append(
            DatasetProfile(
                publication.dataset,
                OfferProfile(publication.offer_id, publication.dataset.id),
                distribution,
                data_service,
                {
                    "@type": "DataAddress",
                    "endpointType": HTTP_ENDPOINT_TYPE,
                    "endpoint": access_url,
                },
            )
        )
    dataset_ids = tuple(item.dataset.id for item in datasets)
    dsp_service_ids = {item.data_service.id for item in datasets}
    dcat_services: list[DcatDataServiceProfile] = []
    for service in config.dcat_data_services:
        if service.id in dsp_service_ids:
            raise ValueError("a DCAT companion service must have an ID distinct from every DSP access service")
        served_ids = dataset_ids if service.serves_dataset_ids is None else service.serves_dataset_ids
        unknown = sorted(set(served_ids).difference(dataset_ids))
        if unknown:
            raise ValueError(f"DCAT data service references unknown dataset IDs: {', '.join(unknown)}")
        dcat_services.append(
            DcatDataServiceProfile(
                service.id,
                service.title,
                service.endpoint_url,
                service.conforms_to,
                served_ids,
                service.endpoint_description,
            )
        )
    return CatalogueProfile(
        config.catalog_id,
        config.catalog_title,
        config.catalog_description,
        config.participant_id,
        config.catalogue_profile,
        tuple(datasets),
        tuple(dcat_services),
    )


def _message_object(message: object, kind: ErrorKind) -> dict[str, object]:
    """Copy a plain protocol message or raise a classified bad-request error."""
    if not isinstance(message, dict):
        raise DspProtocolError(kind, 400, "message must be a JSON object", code="invalid-message")
    if any(not isinstance(key, str) for key in message):
        raise DspProtocolError(kind, 400, "message object keys must be strings", code="invalid-message")
    return dict(message)


def _required_string(message: Mapping[str, object], name: str, kind: ErrorKind) -> str:
    """Read one non-empty string message field or raise a classified error."""
    value = message.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    ):
        raise DspProtocolError(kind, 400, f"{name} must be a non-empty string", code="invalid-message")
    return value


def _validate_common_message(message: Mapping[str, object], type_name: str, kind: ErrorKind) -> None:
    """Validate the official protected context and concrete DSP message type."""
    context = message.get("@context")
    if not isinstance(context, list) or DSP_CONTEXT not in context:
        raise DspProtocolError(kind, 400, "@context must include the official DSP context", code="invalid-context")
    if message.get("@type") != type_name:
        raise DspProtocolError(kind, 400, f"@type must be {type_name}", code="invalid-message")


def _https_callback(value: object, kind: ErrorKind) -> str:
    """Validate the HTTPS callback base URL supplied by a consumer."""
    callback = _required_string({"callbackAddress": value}, "callbackAddress", kind)
    try:
        return _https_url("callbackAddress", callback, reject_query=True)
    except ValueError as error:
        raise DspProtocolError(kind, 400, "callbackAddress must be an HTTPS URL", code="invalid-callback") from error


class DspProvider:
    """Serve a live dataset catalogue and manage non-durable DSP processes.

    Business methods accept and return only ordinary JSON dictionaries; a thin
    HTTP adapter is responsible for route and response-code presentation. All
    process state is in memory and is lost on restart. Callback transitions are
    never committed until a peer acknowledges a 2xx response.

    :param config: Validated fixed provider configuration.
    :param store: Caller-owned entry store containing the DSP publication
        family. Exactly one of ``store`` and ``datasets`` is required.
    :param datasets: Inline publication declarations. Exactly one of
        ``datasets`` and ``store`` is required.
    :param callback_sender: Optional asynchronous callback transport. Supplying
        one bypasses default network policy and is useful for deterministic tests.
    :param uuid_factory: Optional source for provider and agreement identifiers.
    :param utc_clock: Optional UTC clock used for agreement timestamps.
    """

    def __init__(
        self,
        config: DspProviderConfig,
        *,
        store: EntryStore | None = None,
        datasets: tuple[DspDatasetPublication, ...] | None = None,
        callback_sender: CallbackSender | None = None,
        uuid_factory: UuidFactory = uuid4,
        utc_clock: UtcClock | None = None,
    ) -> None:
        if not isinstance(config, DspProviderConfig):
            raise TypeError("config must be a DspProviderConfig")
        if not callable(uuid_factory):
            raise TypeError("uuid_factory must be callable")
        if utc_clock is not None and not callable(utc_clock):
            raise TypeError("utc_clock must be callable")
        if (store is None) == (datasets is None):
            raise ValueError("configure exactly one publication source: store or datasets")
        if store is not None:
            if not isinstance(store, EntryStore):
                raise TypeError("store must implement EntryStore")
            layout = next((item for item in store.entry_layout if item.family is DspPublicationEntry), None)
            if layout is None:
                raise ValueError("store is not configured with DspPublicationEntry")
            if DspDatasetPublication not in layout.records:
                raise ValueError("DspPublicationEntry is not mapped to DspDatasetPublication in this store")
        if datasets is not None:
            try:
                inline = tuple(DspDatasetPublication.create(item) for item in datasets)
            except TypeError as error:
                raise TypeError("datasets must be an iterable of DspDatasetPublication values") from error
            if not inline:
                raise ValueError("datasets must contain at least one publication")
        else:
            inline = None
        self.config = config
        self._store = store
        self._inline_datasets = inline
        self._state = DspState()
        self._sender = callback_sender if callback_sender is not None else DefaultCallbackSender()
        self._uuid_factory = uuid_factory
        self._utc_clock = utc_clock if utc_clock is not None else lambda: datetime.now(UTC)
        self._automatic_tasks: set[asyncio.Task[None]] = set()

    def _publications(self) -> tuple[DspDatasetPublication, ...]:
        """Read the current publication source without taking ownership of it."""
        if self._inline_datasets is not None:
            return self._inline_datasets
        assert self._store is not None
        searcher = self._store.searcher()
        publication = searcher.variable(DspDatasetPublication)
        searcher.output(publication, "publication")
        return tuple(DspDatasetPublication.create(row.values[0]) for row in searcher)

    @property
    def profile(self) -> CatalogueProfile:
        """Return a freshly validated catalogue snapshot."""
        return _profile_from_declarations(self.config, self._publications())

    def _dataset_maps(self) -> tuple[CatalogueProfile, dict[str, DatasetProfile], dict[str, DatasetProfile]]:
        profile = self.profile
        return (
            profile,
            {item.dataset.id: item for item in profile.datasets},
            {item.offer.id: item for item in profile.datasets},
        )

    def automatic_batch(self) -> _AutomaticBatch:
        """Create a response-local holder for automatic callback actions.

        The HTTP adapter uses the returned private holder to release callbacks
        only from that response's background hook.  Ordinary callers do not
        need this seam: their automatic callbacks are managed immediately after
        the business method returns.

        :return: An empty response-local automatic callback holder.
        """
        return _AutomaticBatch()

    def has_automatic_actions(self, batch: _AutomaticBatch) -> bool:
        """Report whether a response-local holder has callbacks to release.

        :param batch: Holder returned by :meth:`automatic_batch`.
        :return: Whether the holder contains at least one action.
        """
        return bool(batch.actions)

    async def release_automatic(self, batch: _AutomaticBatch) -> None:
        """Start one response's automatic callbacks after its body was sent.

        :param batch: Holder returned by :meth:`automatic_batch`.
        """
        actions = tuple(batch.actions)
        batch.actions.clear()
        for action in actions:
            self._start_automatic(action)

    async def drain_automatic(self) -> None:
        """Wait until all provider-managed automatic callback tasks settle."""
        while self._automatic_tasks:
            await asyncio.gather(*tuple(self._automatic_tasks), return_exceptions=True)

    async def cancel_automatic(self) -> None:
        """Cancel and drain provider-managed automatic callbacks at shutdown."""
        tasks = tuple(self._automatic_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def version_document(self) -> dict[str, JsonValue]:
        """Return the DSP 2025-1 HTTPS version-discovery document.

        :return: Plain DSP protocol-version document.
        """
        return version_document(self.config.service_id)

    def dsp_catalogue(self, request: dict[str, object]) -> dict[str, JsonValue]:
        """Return the DSP catalogue snapshot for an empty catalogue filter.

        :param request: Catalog request message JSON.
        :return: Plain DSP catalogue document.
        :raises httk.serve.dsp.models.DspProtocolError: If the request is malformed or filters are unsupported.
        """
        message = _message_object(request, "catalog")
        _validate_common_message(message, "CatalogRequestMessage", "catalog")
        filter_value = message.get("filter")
        if filter_value not in (None, [], ""):
            raise DspProtocolError("catalog", 400, "catalog filters are not supported", code="unsupported-filter")
        return serialize_dsp_catalogue(self.profile)

    def dsp_dataset(self, dataset_id: str) -> dict[str, JsonValue]:
        """Return one DSP dataset only when its ID exactly matches.

        :param dataset_id: Requested dataset identifier.
        :return: Plain DSP dataset document.
        :raises httk.serve.dsp.models.DspProtocolError: If the identifier is absent or unknown.
        """
        _profile, datasets_by_id, _datasets_by_offer = self._dataset_maps()
        if not isinstance(dataset_id, str) or dataset_id not in datasets_by_id:
            raise DspProtocolError("catalog", 404, "dataset was not found", code="not-found")
        return serialize_dsp_dataset_document(datasets_by_id[dataset_id])

    def dcat_catalogue(self) -> dict[str, JsonValue]:
        """Return the separate strict owned-context DCAT-AP projection.

        :return: Plain DCAT-AP-compatible JSON-LD catalogue document.
        """
        return serialize_dcat_catalogue(self.profile)

    async def get_negotiation(self, provider_pid: str) -> dict[str, JsonValue]:
        """Return one acknowledged negotiation process.

        :param provider_pid: Provider negotiation process identifier.
        :return: Plain DSP negotiation document.
        :raises httk.serve.dsp.models.DspProtocolError: If the process is unknown.
        """
        return serialize_negotiation(await self._negotiation(provider_pid))

    async def request_negotiation(
        self,
        message: dict[str, object],
        *,
        _automatic_batch: _AutomaticBatch | None = None,
    ) -> dict[str, JsonValue]:
        """Accept an initial consumer contract request.

        The initial request must omit ``providerPid``, identify the configured
        offer and dataset exactly, and provide an HTTPS callback. With automatic
        progression enabled, the agreement callback is scheduled only after
        the returned process snapshot has been acknowledged to the caller.

        :param message: Contract request message JSON.
        :param _automatic_batch: Optional response-local holder used by the HTTP adapter.
        :return: Newly created DSP negotiation document.
        :raises httk.serve.dsp.models.DspProtocolError: If message validation fails.
        """
        request = _message_object(message, "negotiation")
        _validate_common_message(request, "ContractRequestMessage", "negotiation")
        if "providerPid" in request:
            raise DspProtocolError("negotiation", 400, "initial requests must omit providerPid", code="invalid-pid")
        consumer_pid = _required_string(request, "consumerPid", "negotiation")
        callback = _https_callback(request.get("callbackAddress"), "negotiation")
        policy = self._validate_offer(request.get("offer"), "negotiation")
        record = NegotiationRecord(
            provider_pid=self._new_process_id(),
            consumer_pid=consumer_pid,
            callback_address=callback,
            state="REQUESTED",
            policy=policy,
        )
        try:
            await self._state.create_negotiation(record)
        except ValueError as error:
            raise DspProtocolError("negotiation", 400, str(error), code="duplicate-process") from error
        if self.config.automatic_progression:
            self._queue_automatic(_automatic_batch, lambda: self.send_agreement(record.provider_pid))
        return serialize_negotiation(record)

    async def counter_request(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive a consumer counter-request after a provider offer.

        :param provider_pid: Provider negotiation process identifier from the route.
        :param message: Consumer contract request message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs, policy, or the transition are invalid.
        """
        request = _message_object(message, "negotiation")
        _validate_common_message(request, "ContractRequestMessage", "negotiation")
        record = await self._negotiation(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, request, "negotiation")
        policy = self._validate_offer(request.get("offer"), "negotiation")
        if policy != record.policy:
            raise DspProtocolError(
                "negotiation",
                400,
                "counter-request offer must match the negotiation's selected offer",
                code="invalid-offer",
            )
        await self._receive_negotiation(provider_pid, frozenset({"OFFERED"}), "REQUESTED")

    async def negotiation_event(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive the only permitted consumer negotiation event, ``ACCEPTED``.

        :param provider_pid: Provider negotiation process identifier from the route.
        :param message: Contract-negotiation event message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs, event, or transition are invalid.
        """
        event = _message_object(message, "negotiation")
        _validate_common_message(event, "ContractNegotiationEventMessage", "negotiation")
        record = await self._negotiation(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, event, "negotiation")
        if event.get("eventType") != "ACCEPTED":
            raise DspProtocolError(
                "negotiation", 400, "only consumer ACCEPTED events are accepted", code="invalid-event"
            )
        await self._receive_negotiation(provider_pid, frozenset({"OFFERED"}), "ACCEPTED")

    async def verify_agreement(
        self,
        provider_pid: str,
        message: dict[str, object],
        *,
        _automatic_batch: _AutomaticBatch | None = None,
    ) -> None:
        """Receive consumer verification of an acknowledged agreement.

        :param provider_pid: Provider negotiation process identifier from the route.
        :param message: Agreement-verification message JSON.
        :param _automatic_batch: Optional response-local holder used by the HTTP adapter.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs, state, or finalization delivery are invalid.
        """
        verification = _message_object(message, "negotiation")
        _validate_common_message(verification, "ContractAgreementVerificationMessage", "negotiation")
        record = await self._negotiation(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, verification, "negotiation")
        await self._receive_negotiation(provider_pid, frozenset({"AGREED"}), "VERIFIED")
        if self.config.automatic_progression:
            self._queue_automatic(_automatic_batch, lambda: self.finalize_negotiation(provider_pid))

    async def receive_negotiation_termination(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive consumer termination of a nonterminal negotiation.

        :param provider_pid: Provider negotiation process identifier from the route.
        :param message: Negotiation-termination message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs or the transition are invalid.
        """
        termination = _message_object(message, "negotiation")
        _validate_common_message(termination, "ContractNegotiationTerminationMessage", "negotiation")
        record = await self._negotiation(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, termination, "negotiation")
        await self._receive_negotiation(
            provider_pid,
            frozenset({"REQUESTED", "OFFERED", "ACCEPTED", "AGREED", "VERIFIED"}),
            "TERMINATED",
        )

    async def get_transfer(self, provider_pid: str) -> dict[str, JsonValue]:
        """Return one acknowledged transfer process.

        :param provider_pid: Provider transfer-process identifier.
        :return: Plain DSP transfer-process document.
        :raises httk.serve.dsp.models.DspProtocolError: If the process is unknown.
        """
        return serialize_transfer(await self._transfer(provider_pid))

    async def request_transfer(
        self,
        message: dict[str, object],
        *,
        _automatic_batch: _AutomaticBatch | None = None,
    ) -> dict[str, JsonValue]:
        """Accept a consumer pull transfer request under a finalized agreement.

        Identical repeated consumer process IDs return the original transfer.
        A reuse with different agreement, callback, or format is rejected.

        :param message: Transfer request message JSON.
        :param _automatic_batch: Optional response-local holder used by the HTTP adapter.
        :return: Newly created or idempotently recovered transfer-process document.
        :raises httk.serve.dsp.models.DspProtocolError: If request validation or callback delivery fails.
        """
        request = _message_object(message, "transfer")
        _validate_common_message(request, "TransferRequestMessage", "transfer")
        consumer_pid = _required_string(request, "consumerPid", "transfer")
        callback = _https_callback(request.get("callbackAddress"), "transfer")
        agreement_id = _required_string(request, "agreementId", "transfer")
        if "dataAddress" in request:
            raise DspProtocolError(
                "transfer", 400, "consumer dataAddress is not accepted for HttpData-PULL", code="invalid-data-address"
            )
        agreement_with_state = await self._state.agreement(agreement_id)
        if agreement_with_state is None:
            raise DspProtocolError("transfer", 404, "agreement was not found", code="not-found")
        agreement, agreement_state = agreement_with_state
        _profile, datasets_by_id, _datasets_by_offer = self._dataset_maps()
        publication = datasets_by_id.get(agreement.target)
        if agreement_state != "FINALIZED" or publication is None:
            raise DspProtocolError(
                "transfer",
                400,
                "a finalized agreement for a catalogue dataset is required",
                code="invalid-agreement",
            )
        requested_format = request.get("format")
        if requested_format != publication.distribution.format:
            raise DspProtocolError("transfer", 400, "transfer format is not supported", code="unsupported-format")
        try:
            stored, created = await self._state.get_or_create_transfer_for_consumer(
                consumer_pid,
                lambda: TransferRecord(
                    provider_pid=self._new_process_id(),
                    consumer_pid=consumer_pid,
                    callback_address=callback,
                    agreement_id=agreement_id,
                    format=publication.distribution.format,
                    state="REQUESTED",
                ),
            )
        except ValueError as error:
            raise DspProtocolError("transfer", 400, str(error), code="duplicate-process") from error
        if not created:
            if (
                stored.callback_address != callback
                or stored.agreement_id != agreement_id
                or stored.format != publication.distribution.format
            ):
                raise DspProtocolError(
                    "transfer", 400, "consumerPid is already used by a conflicting transfer", code="duplicate-process"
                )
            if self.config.automatic_progression and stored.state == "REQUESTED" and not stored.delivery.out_of_sync:
                self._queue_automatic(_automatic_batch, lambda: self.start_transfer(stored.provider_pid))
            return serialize_transfer(stored)
        if self.config.automatic_progression:
            self._queue_automatic(_automatic_batch, lambda: self.start_transfer(stored.provider_pid))
        return serialize_transfer(stored)

    async def resume_transfer(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive a consumer start message that resumes a suspended transfer.

        :param provider_pid: Provider transfer-process identifier from the route.
        :param message: Transfer-start message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs or the transition are invalid.
        """
        start = _message_object(message, "transfer")
        _validate_common_message(start, "TransferStartMessage", "transfer")
        record = await self._transfer(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, start, "transfer")
        if "dataAddress" in start:
            raise DspProtocolError(
                "transfer", 400, "consumer dataAddress is not accepted for HttpData-PULL", code="invalid-data-address"
            )
        await self._receive_transfer(provider_pid, frozenset({"SUSPENDED"}), "STARTED")

    async def receive_transfer_suspension(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive consumer suspension of a started transfer.

        :param provider_pid: Provider transfer-process identifier from the route.
        :param message: Transfer-suspension message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs or the transition are invalid.
        """
        suspension = _message_object(message, "transfer")
        _validate_common_message(suspension, "TransferSuspensionMessage", "transfer")
        record = await self._transfer(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, suspension, "transfer")
        await self._receive_transfer(provider_pid, frozenset({"STARTED"}), "SUSPENDED")

    async def receive_transfer_completion(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive consumer completion of a started transfer.

        :param provider_pid: Provider transfer-process identifier from the route.
        :param message: Transfer-completion message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs or the transition are invalid.
        """
        completion = _message_object(message, "transfer")
        _validate_common_message(completion, "TransferCompletionMessage", "transfer")
        record = await self._transfer(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, completion, "transfer")
        await self._receive_transfer(provider_pid, frozenset({"STARTED"}), "COMPLETED")

    async def receive_transfer_termination(self, provider_pid: str, message: dict[str, object]) -> None:
        """Receive consumer termination of a nonterminal transfer.

        :param provider_pid: Provider transfer-process identifier from the route.
        :param message: Transfer-termination message JSON.
        :raises httk.serve.dsp.models.DspProtocolError: If PIDs or the transition are invalid.
        """
        termination = _message_object(message, "transfer")
        _validate_common_message(termination, "TransferTerminationMessage", "transfer")
        record = await self._transfer(provider_pid)
        self._assert_process_pids(record.provider_pid, record.consumer_pid, provider_pid, termination, "transfer")
        await self._receive_transfer(provider_pid, frozenset({"REQUESTED", "STARTED", "SUSPENDED"}), "TERMINATED")

    async def send_offer(self, provider_pid: str) -> None:
        """Send a provider contract offer and acknowledge ``REQUESTED`` to ``OFFERED``.

        :param provider_pid: Provider negotiation process identifier.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_negotiation(
            provider_pid,
            expected_states=frozenset({"REQUESTED"}),
            target_state="OFFERED",
            path_part="offers",
            transition="offer",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "ContractOfferMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "offer": thaw_json(record.policy),
            },
        )

    async def send_agreement(self, provider_pid: str) -> None:
        """Send a provider agreement from ``REQUESTED`` or ``ACCEPTED``.

        :param provider_pid: Provider negotiation process identifier.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        record = await self._negotiation(provider_pid)
        agreement = self._new_agreement(record)
        await self._send_negotiation(
            provider_pid,
            expected_states=frozenset({"REQUESTED", "ACCEPTED"}),
            target_state="AGREED",
            path_part="agreement",
            transition="agreement",
            agreement=agreement,
            build=lambda snapshot: {
                "@context": [DSP_CONTEXT],
                "@type": "ContractAgreementMessage",
                "providerPid": snapshot.provider_pid,
                "consumerPid": snapshot.consumer_pid,
                "agreement": serialize_agreement(agreement),
            },
        )

    async def finalize_negotiation(self, provider_pid: str) -> None:
        """Send provider finalization after consumer agreement verification.

        :param provider_pid: Provider negotiation process identifier.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_negotiation(
            provider_pid,
            expected_states=frozenset({"VERIFIED"}),
            target_state="FINALIZED",
            path_part="events",
            transition="finalized",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "ContractNegotiationEventMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "eventType": "FINALIZED",
            },
        )

    async def terminate_negotiation(
        self,
        provider_pid: str,
        *,
        code: str = "terminated",
        reason: str = "negotiation terminated by provider",
    ) -> None:
        """Send provider termination for any nonterminal negotiation state.

        :param provider_pid: Provider negotiation process identifier.
        :param code: Machine-readable termination code.
        :param reason: Human-readable termination reason.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_negotiation(
            provider_pid,
            expected_states=frozenset({"REQUESTED", "OFFERED", "ACCEPTED", "AGREED", "VERIFIED"}),
            target_state="TERMINATED",
            path_part="termination",
            transition="termination",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "ContractNegotiationTerminationMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "code": code,
                "reason": [reason],
            },
            termination=True,
        )

    async def start_transfer(self, provider_pid: str) -> None:
        """Send provider transfer start with the configured pull data address.

        :param provider_pid: Provider transfer-process identifier.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        transfer = await self._transfer(provider_pid)
        agreement_with_state = await self._state.agreement(transfer.agreement_id)
        if agreement_with_state is None:
            raise DspProtocolError("transfer", 404, "agreement was not found", code="not-found")
        agreement, agreement_state = agreement_with_state
        _profile, datasets_by_id, _datasets_by_offer = self._dataset_maps()
        publication = datasets_by_id.get(agreement.target)
        if agreement_state != "FINALIZED" or publication is None:
            raise DspProtocolError(
                "transfer", 400, "a finalized agreement for a catalogue dataset is required", code="invalid-agreement"
            )
        data_address = thaw_json(publication.data_address)
        if not isinstance(data_address, dict):
            raise TypeError("configured data_address must be a JSON object")
        await self._send_transfer(
            provider_pid,
            expected_states=frozenset({"REQUESTED", "SUSPENDED"}),
            target_state="STARTED",
            path_part="start",
            transition="start",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "TransferStartMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "dataAddress": data_address,
            },
        )

    async def suspend_transfer(
        self,
        provider_pid: str,
        *,
        code: str = "suspended",
        reason: str = "transfer suspended by provider",
    ) -> None:
        """Send provider suspension for a started transfer.

        :param provider_pid: Provider transfer-process identifier.
        :param code: Machine-readable suspension code.
        :param reason: Human-readable suspension reason.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_transfer(
            provider_pid,
            expected_states=frozenset({"STARTED"}),
            target_state="SUSPENDED",
            path_part="suspension",
            transition="suspension",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "TransferSuspensionMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "code": code,
                "reason": [reason],
            },
        )

    async def complete_transfer(self, provider_pid: str) -> None:
        """Send provider completion for a started transfer.

        :param provider_pid: Provider transfer-process identifier.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_transfer(
            provider_pid,
            expected_states=frozenset({"STARTED"}),
            target_state="COMPLETED",
            path_part="completion",
            transition="completion",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "TransferCompletionMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
            },
        )

    async def terminate_transfer(
        self,
        provider_pid: str,
        *,
        code: str = "terminated",
        reason: str = "transfer terminated by provider",
    ) -> None:
        """Send provider termination for any nonterminal transfer state.

        :param provider_pid: Provider transfer-process identifier.
        :param code: Machine-readable termination code.
        :param reason: Human-readable termination reason.
        :raises httk.serve.dsp.models.DspProtocolError: If state or callback delivery is invalid.
        """
        await self._send_transfer(
            provider_pid,
            expected_states=frozenset({"REQUESTED", "STARTED", "SUSPENDED"}),
            target_state="TERMINATED",
            path_part="termination",
            transition="termination",
            build=lambda record: {
                "@context": [DSP_CONTEXT],
                "@type": "TransferTerminationMessage",
                "providerPid": record.provider_pid,
                "consumerPid": record.consumer_pid,
                "code": code,
                "reason": [reason],
            },
            termination=True,
        )

    async def _send_negotiation(
        self,
        provider_pid: str,
        *,
        expected_states: frozenset[str],
        target_state: str,
        path_part: str,
        transition: str,
        build: Callable[[NegotiationRecord], dict[str, JsonValue]],
        agreement: AgreementRecord | None = None,
        termination: bool = False,
    ) -> None:
        """Reserve, deliver, and conditionally commit one negotiation callback."""
        try:
            record, token = await self._state.reserve_negotiation(
                provider_pid,
                expected_states=expected_states,
                transition=transition,
            )
        except KeyError as error:
            raise DspProtocolError("negotiation", 404, "negotiation was not found", code="not-found") from error
        except RuntimeError as error:
            raise self._transition_error("negotiation", provider_pid, error) from error
        try:
            await self._deliver(
                callback_url(
                    record.callback_address, f"/negotiations/{quote(record.consumer_pid, safe='')}/{path_part}"
                ),
                build(record),
            )
        except CallbackTransportError as error:
            await self._state.fail_negotiation(provider_pid, token, detail=error.detail, retries=2)
            if not termination:
                try:
                    await self.terminate_negotiation(provider_pid, code="callback-failed", reason=error.detail)
                except DspProtocolError:
                    pass
            raise DspProtocolError(
                "negotiation",
                502,
                f"callback delivery failed: {error.detail}",
                code="callback-failed",
                provider_pid=record.provider_pid,
                consumer_pid=record.consumer_pid,
            ) from error
        if not await self._state.commit_negotiation(provider_pid, token, state=target_state, agreement=agreement):
            raise DspProtocolError("negotiation", 409, "callback transition was superseded", code="transition-conflict")

    async def _send_transfer(
        self,
        provider_pid: str,
        *,
        expected_states: frozenset[str],
        target_state: str,
        path_part: str,
        transition: str,
        build: Callable[[TransferRecord], dict[str, JsonValue]],
        termination: bool = False,
    ) -> None:
        """Reserve, deliver, and conditionally commit one transfer callback."""
        try:
            record, token = await self._state.reserve_transfer(
                provider_pid,
                expected_states=expected_states,
                transition=transition,
            )
        except KeyError as error:
            raise DspProtocolError("transfer", 404, "transfer was not found", code="not-found") from error
        except RuntimeError as error:
            raise self._transition_error("transfer", provider_pid, error) from error
        try:
            await self._deliver(
                callback_url(record.callback_address, f"/transfers/{quote(record.consumer_pid, safe='')}/{path_part}"),
                build(record),
            )
        except CallbackTransportError as error:
            await self._state.fail_transfer(provider_pid, token, detail=error.detail, retries=2)
            if not termination:
                try:
                    await self.terminate_transfer(provider_pid, code="callback-failed", reason=error.detail)
                except DspProtocolError:
                    pass
            raise DspProtocolError(
                "transfer",
                502,
                f"callback delivery failed: {error.detail}",
                code="callback-failed",
                provider_pid=record.provider_pid,
                consumer_pid=record.consumer_pid,
            ) from error
        if not await self._state.commit_transfer(provider_pid, token, state=target_state):
            raise DspProtocolError("transfer", 409, "callback transition was superseded", code="transition-conflict")

    def _queue_automatic(self, batch: _AutomaticBatch | None, action: _AutomaticCallback) -> None:
        """Queue an automatic callback for one response or manage it directly."""
        if batch is None:
            self._start_automatic(action)
        else:
            batch.actions.append(action)

    def _start_automatic(self, action: _AutomaticCallback) -> None:
        """Create one tracked callback task after its initiating handler returns."""
        task = asyncio.create_task(self._run_automatic(action()))
        self._automatic_tasks.add(task)
        task.add_done_callback(self._automatic_tasks.discard)

    async def _run_automatic(self, callback: Awaitable[None]) -> None:
        """Consume expected callback failures after delivery state has been recorded."""
        try:
            await callback
        except asyncio.CancelledError:
            raise
        except DspProtocolError as error:
            _LOGGER.warning(
                "automatic DSP callback failed: kind=%s code=%s providerPid=%s consumerPid=%s",
                error.kind,
                error.code,
                error.provider_pid,
                error.consumer_pid,
            )
            return
        except Exception:
            _LOGGER.exception("unexpected automatic DSP callback failure")

    async def _deliver(self, url: str, document: dict[str, JsonValue]) -> None:
        """Deliver a callback at most twice and require a 2xx acknowledgement."""
        last_detail = "callback delivery was not attempted"
        for _attempt in range(2):
            try:
                status = await self._sender(url, document)
            except CallbackTransportError as error:
                last_detail = error.detail
                continue
            except Exception as error:
                last_detail = f"callback transport failed: {error.__class__.__name__}"
                continue
            if isinstance(status, bool) or not isinstance(status, int):
                last_detail = "callback sender did not return an HTTP status code"
                continue
            if 200 <= status < 300:
                return
            last_detail = f"callback returned HTTP {status}"
        raise CallbackTransportError(last_detail)

    async def _negotiation(self, provider_pid: str) -> NegotiationRecord:
        """Resolve one negotiation or raise the adapter-facing missing error."""
        try:
            return await self._state.negotiation(provider_pid)
        except KeyError as error:
            raise DspProtocolError("negotiation", 404, "negotiation was not found", code="not-found") from error

    async def _transfer(self, provider_pid: str) -> TransferRecord:
        """Resolve one transfer or raise the adapter-facing missing error."""
        try:
            return await self._state.transfer(provider_pid)
        except KeyError as error:
            raise DspProtocolError("transfer", 404, "transfer was not found", code="not-found") from error

    async def _receive_negotiation(self, provider_pid: str, expected_states: frozenset[str], state: str) -> None:
        """Atomically accept an inbound negotiation transition."""
        try:
            await self._state.receive_negotiation(provider_pid, expected_states=expected_states, state=state)
        except KeyError as error:
            raise DspProtocolError("negotiation", 404, "negotiation was not found", code="not-found") from error
        except RuntimeError as error:
            raise self._transition_error("negotiation", provider_pid, error) from error

    async def _receive_transfer(self, provider_pid: str, expected_states: frozenset[str], state: str) -> None:
        """Atomically accept an inbound transfer transition."""
        try:
            await self._state.receive_transfer(provider_pid, expected_states=expected_states, state=state)
        except KeyError as error:
            raise DspProtocolError("transfer", 404, "transfer was not found", code="not-found") from error
        except RuntimeError as error:
            raise self._transition_error("transfer", provider_pid, error) from error

    def _validate_offer(self, value: object, kind: ErrorKind) -> Mapping[str, FrozenJsonValue]:
        """Require the exact advertised policy with only its mandated top target."""
        if not isinstance(value, Mapping):
            raise DspProtocolError(kind, 400, "offer must be a JSON object", code="invalid-offer")
        try:
            frozen = freeze_json(value)
        except (TypeError, ValueError) as error:
            raise DspProtocolError(kind, 400, str(error), code="invalid-offer") from error
        if not isinstance(frozen, Mapping):
            raise DspProtocolError(kind, 400, "offer must be a JSON object", code="invalid-offer")
        policy = thaw_json(frozen)
        if not isinstance(policy, dict):
            raise DspProtocolError(kind, 400, "offer must be a JSON object", code="invalid-offer")
        offer_id = policy.get("@id")
        _catalogue, _datasets_by_id, datasets_by_offer = self._dataset_maps()
        profile = datasets_by_offer.get(offer_id) if isinstance(offer_id, str) else None
        if profile is None:
            raise DspProtocolError(kind, 400, "offer is not advertised by this catalogue", code="invalid-offer")
        if policy.get("target") != profile.dataset.id:
            raise DspProtocolError(kind, 400, "offer target must match its catalogue dataset", code="invalid-target")
        self._reject_contained_targets(policy)
        expected = offer_policy(profile.offer, include_target=True)
        if policy != expected:
            raise DspProtocolError(
                kind, 400, "offer does not exactly match the advertised policy", code="invalid-offer"
            )
        return frozen

    @staticmethod
    def _reject_contained_targets(policy: Mapping[str, object]) -> None:
        """Reject target properties below the message offer's top-level policy."""

        def visit(value: object) -> bool:
            if isinstance(value, Mapping):
                return "target" in value or any(visit(child) for child in value.values())
            if isinstance(value, list | tuple):
                return any(visit(child) for child in value)
            return False

        for name, value in policy.items():
            if name != "target" and visit(value):
                raise DspProtocolError(
                    "negotiation", 400, "contained policy rules must not have targets", code="invalid-target"
                )

    def _new_agreement(self, record: NegotiationRecord) -> AgreementRecord:
        """Create a unique UTC agreement by copying the accepted static policy."""
        policy = thaw_json(record.policy)
        if not isinstance(policy, dict):
            raise TypeError("negotiation policy must be a JSON object")
        policy["@id"] = self._new_agreement_id()
        policy["@type"] = "Agreement"
        target = policy.get("target")
        _profile, datasets_by_id, _datasets_by_offer = self._dataset_maps()
        if not isinstance(target, str) or target not in datasets_by_id:
            raise TypeError("negotiation policy must target a catalogue dataset")
        policy["target"] = target
        policy["assigner"] = self.config.participant_id
        policy["assignee"] = record.consumer_pid
        policy["timestamp"] = self._utc_timestamp()
        agreement_id = policy["@id"]
        timestamp = policy["timestamp"]
        if not isinstance(agreement_id, str) or not isinstance(timestamp, str):
            raise TypeError("agreement identifiers and timestamps must be strings")
        frozen = freeze_json(policy)
        if not isinstance(frozen, Mapping):
            raise TypeError("agreement policy must be a JSON object")
        return AgreementRecord(
            id=agreement_id,
            policy=frozen,
            target=target,
            assigner=self.config.participant_id,
            assignee=record.consumer_pid,
            timestamp=timestamp,
        )

    def _new_process_id(self) -> str:
        """Create a provider process identifier in the required ``urn:uuid:`` form."""
        value = str(self._uuid_factory())
        if not value.strip() or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise RuntimeError("uuid_factory returned an invalid identifier")
        return value if value.startswith("urn:uuid:") else f"urn:uuid:{value}"

    def _new_agreement_id(self) -> str:
        """Create a unique agreement identifier in the required ``urn:uuid:`` form."""
        value = str(self._uuid_factory())
        if not value.strip() or any(0xD800 <= ord(character) <= 0xDFFF for character in value):
            raise RuntimeError("uuid_factory returned an invalid identifier")
        return value if value.startswith("urn:uuid:") else f"urn:uuid:{value}"

    def _utc_timestamp(self) -> str:
        """Render the injected clock value as a UTC XML Schema date-time."""
        value = self._utc_clock()
        if not isinstance(value, datetime):
            raise TypeError("utc_clock must return a datetime")
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("utc_clock must return a timezone-aware UTC datetime")
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _assert_process_pids(
        known_provider_pid: str,
        known_consumer_pid: str,
        path_provider_pid: str,
        message: Mapping[str, object],
        kind: ErrorKind,
    ) -> None:
        """Correlate route and message PIDs against the stored process snapshot."""
        if path_provider_pid != known_provider_pid or message.get("providerPid") != known_provider_pid:
            raise DspProtocolError(kind, 400, "providerPid does not match the process", code="invalid-pid")
        if message.get("consumerPid") != known_consumer_pid:
            raise DspProtocolError(kind, 400, "consumerPid does not match the process", code="invalid-pid")

    @staticmethod
    def _transition_error(kind: ErrorKind, provider_pid: str, error: RuntimeError) -> DspProtocolError:
        """Map locked-state rejection to the documented invalid-transition response."""
        return DspProtocolError(kind, 400, str(error), code="invalid-transition", provider_pid=provider_pid)


__all__ = ["DspProvider", "UtcClock", "UuidFactory"]
