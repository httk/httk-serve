"""Adapters that turn capability-layer records into DSP dataset publications."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from httk.core import EntryProvider

from .config import DspDatasetPublication, DspDatasetSource

type DspPublicationFactory = Callable[[Mapping[str, Any]], DspDatasetPublication | Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class DspEntryProviderDatasetSource(DspDatasetSource):
    """Adapt one entry type from an :class:`httk.core.EntryProvider`.

    Database-backed ``httk.store.db.StoreEntryProvider`` instances use
    the same neutral contract, so this adapter works for in-memory and stored
    records alike. The factory makes the publication policy and delivery
    metadata explicit instead of guessing protocol settings from domain data.

    :param provider: Entry provider whose records describe the datasets.
    :param entry_type: Entry type to enumerate once at DSP provider creation.
    :param publication_factory: Function adapting each record to a publication.
    """

    provider: EntryProvider
    entry_type: str
    publication_factory: DspPublicationFactory

    def __post_init__(self) -> None:
        """Validate the provider, entry type, and adapter callable."""
        if not isinstance(self.provider, EntryProvider):
            raise TypeError("provider must be an EntryProvider")
        if not isinstance(self.entry_type, str) or not self.entry_type.strip():
            raise ValueError("entry_type must be a non-empty string")
        if not callable(self.publication_factory):
            raise TypeError("publication_factory must be callable")

    def publications(self) -> tuple[DspDatasetPublication, ...]:
        """Read and adapt all records from the configured entry type.

        :return: Validated dataset publications in provider iteration order.
        """
        return tuple(
            DspDatasetPublication.create(self.publication_factory(record))
            for record in self.provider.records(self.entry_type)
        )


__all__ = ["DspEntryProviderDatasetSource", "DspPublicationFactory"]
