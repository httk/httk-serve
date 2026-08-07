"""The ``trajectories`` entry type, derived from the ``structures`` properties.

A ``trajectories`` entry describes an ordered sequence of structures
(frames). Every structures property (other than ``id`` and ``type``) is reused
with an extra leading ``dim_frames`` dimension so it can vary per frame; that
leading dimension is declared compactable (``constant``) so a server MAY send a
single value when the property is constant across frames (see the specification
sections "Trajectories Entries" and "Compact list representation"). Two
trajectory-specific properties, ``nframes`` and ``reference_frames``, are added.

When ``httk-atomistic`` is present, its ``TrajectoryEntryProvider`` serves the
vendored official OPTIMADE v1.3 definition and is the preferred route. This
helper remains for deriving a definition from a supplied ``structures``
definition.

The source structures properties are taken from a supplied
:class:`~httk.core.EntryTypeDefinition` (its :class:`~httk.core.PropertyDefinition`
objects, reduced to the simplified dialect); this module produces the simplified
``{"description", "properties"}`` dialect, which a caller turns into a full
``trajectories`` :class:`~httk.core.EntryTypeDefinition` via
:func:`~httk.serve.optimade.schema.served.entry_type_definition_from_simple`.
"""

from collections.abc import Sequence
from typing import Any

from httk.core import EntryTypeDefinition

from .served import simplified_property


def _inner_dimensions(name: str, info: dict[str, Any]) -> tuple[list[str], list[int | None]]:
    """The list axes of a structures property, excluding the frame axis.

    Uses the property's declared ``dimensions`` when present; otherwise derives
    one axis per ``list of`` nesting level of its ``fulltype`` with a generated
    name, and no axis at all for a scalar property.
    """
    dimensions = info.get('dimensions')
    if dimensions is not None:
        return list(dimensions['names']), list(dimensions['sizes'])
    fulltype = info.get('fulltype', 'string')
    depth = 0
    while fulltype.startswith('list of '):
        depth += 1
        fulltype = fulltype[len('list of ') :]
    names = [f'dim_{name}_{i + 1}' for i in range(depth)]
    sizes: list[int | None] = [None] * depth
    return names, sizes


def _frame_wrapped(name: str, info: dict[str, Any]) -> dict[str, Any]:
    """Wrap a structures property with a leading (compactable) ``dim_frames`` axis."""
    inner_names, inner_sizes = _inner_dimensions(name, info)
    wrapped: dict[str, Any] = dict(info)
    wrapped['fulltype'] = 'list of ' + info.get('fulltype', 'string')
    wrapped['dimensions'] = {
        'names': ['dim_frames'] + inner_names,
        'sizes': [None] + inner_sizes,
        'compactable': ['constant'] + ['no'] * len(inner_names),
    }
    wrapped['description'] = 'Frame-dependent (per dim_frames): ' + info.get('description', '')
    return wrapped


def trajectories_entry_info(
    structure_definition: EntryTypeDefinition, structure_properties: Sequence[str]
) -> dict[str, Any]:
    """Build ``trajectories`` entry metadata from named ``structures`` properties.

    ``structure_definition`` is the ``structures``
    :class:`~httk.core.EntryTypeDefinition`; ``id`` and ``type`` are copied
    unwrapped (with ``type``'s description adjusted), every other named
    structures property is frame-wrapped, and the shared ``last_modified``
    property plus the trajectory-specific ``nframes`` and ``reference_frames``
    are added. The result is the simplified ``{"description", "properties"}``
    dialect.

    :param structure_definition: Full ``structures`` entry definition.
    :param structure_properties: Structure properties to carry into trajectories.
    :return: Simplified ``trajectories`` entry metadata.
    """
    described = structure_definition.properties

    def simple(name: str) -> dict[str, Any]:
        return simplified_property(described[name])

    properties: dict[str, dict[str, Any]] = {}

    for name in structure_properties:
        info = simple(name)
        if name == 'id':
            properties['id'] = info
        elif name == 'type':
            info['description'] = "The name of the type of this entry, always 'trajectories'"
            properties['type'] = info
        else:
            properties[name] = _frame_wrapped(name, info)

    # A shared property (see "Properties Used by Multiple Entry Types").
    properties['last_modified'] = simple('last_modified')

    properties['nframes'] = {
        'description': (
            "The number of frames in the trajectory. This value indicates the number of frames stored in the "
            "data, and may deviate from the number of steps used to calculate the trajectory. For example, a "
            "10 ps simulation with calculation steps of 1 fs where data is stored once every 50 fs, nframes "
            "will be 200. The integer value MUST be equal to the number of frames in the trajectory (i.e., the "
            "length of the dim_frames dimension) and MUST be a positive non-zero value."
        ),
        'fulltype': 'integer',
        'required_support': True,
        'should_support': True,
        'required_query': True,
        'required_response': False,
        'default_response': True,
    }

    properties['reference_frames'] = {
        'description': (
            "The indices of a set of frames that give a good but very brief overview of the trajectory. The "
            "first reference frame could for example be a starting configuration, the second a transition "
            "state and the third the final state. The values MUST be larger than or equal to 0 and less than "
            "nframes."
        ),
        'fulltype': 'list of integer',
        'required_support': False,
        'should_support': False,
        'required_query': False,
        'required_response': False,
        'default_response': True,
    }

    return {
        'description': 'A trajectories entry.',
        'properties': properties,
    }
