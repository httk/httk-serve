"""Markers for property values transmitted via the partial data protocol.

A field extractor may return a :class:`PartialValue` in place of a concrete
value when the value is too large to include inline, or when its list axes are
sliceable. The endpoint layer turns it into ``null`` plus
``meta.partial_data_links`` (only the endpoint knows the base URL), slices it
inline when a ``dimension_slices`` query parameter is supplied, or streams it
via the JSON Lines partial data endpoint.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PartialDimension:
    """Describe one list axis of a :class:`PartialValue`.

    ``length`` is the number of items along the axis (``None`` when unknown or
    entry-dependent and not declared). ``sliceable`` indicates whether the
    server can honour a slice request for this axis.

    :param name: Dimension name used in response metadata.
    :param length: Number of items, or ``None`` when unknown.
    :param sliceable: Whether the server accepts slices on this axis.
    """

    name: str
    length: int | None = None
    sliceable: bool = False


@dataclass(frozen=True)
class PartialValue:
    """Describe a property value provided lazily, one slice at a time.

    ``fetch`` takes a tuple of Python slices (one per dimension, with the usual
    *exclusive* stop) and returns the corresponding nested lists.

    :param dimensions: Axes describing the value.
    :param fetch: Slice retrieval operation.
    """

    dimensions: tuple[PartialDimension, ...]
    fetch: Callable[[tuple[slice, ...]], Any]
