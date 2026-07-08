# This specific file is a mere re-formatting of information in
# the OPTIMADE specification [https://www.optimade.org/].
#
# Formally, the author makes a Public Domain Dedication
# according to CC0 1.0 Universal (CC0 1.0)
#   https://creativecommons.org/publicdomain/zero/1.0/
#
# (Note, this only applies to this one specific file.)

"""The ``trajectories`` entry type, derived from the ``structures`` properties.

A :entry:`trajectories` entry describes an ordered sequence of structures
(frames). Every structures property (other than ``id`` and ``type``) is reused
with an extra leading ``dim_frames`` dimension so it can vary per frame; that
leading dimension is declared compactable (``constant``) so a server MAY send a
single value when the property is constant across frames (see the specification
sections "Trajectories Entries" and "Compact list representation"). Two
trajectory-specific properties, ``nframes`` and ``reference_frames``, are added.
"""

from typing import Sequence

from . import entries as entry_spec
from .entries import EntryInfo, PropertyInfo


def _inner_dimensions(name: str, info: PropertyInfo) -> tuple[list[str], list[int | None]]:
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


def _frame_wrapped(name: str, info: PropertyInfo) -> PropertyInfo:
    """Wrap a structures property with a leading (compactable) ``dim_frames`` axis."""
    inner_names, inner_sizes = _inner_dimensions(name, info)
    wrapped: PropertyInfo = info.copy()
    wrapped['type'] = 'list'
    wrapped['fulltype'] = 'list of ' + info.get('fulltype', 'string')
    wrapped['dimensions'] = {
        'names': ['dim_frames'] + inner_names,
        'sizes': [None] + inner_sizes,
        'compactable': ['constant'] + ['no'] * len(inner_names),
    }
    wrapped['description'] = 'Frame-dependent (per dim_frames): ' + info.get('description', '')
    return wrapped


def trajectories_entry_info(structure_properties: Sequence[str]) -> EntryInfo:
    """Build the ``trajectories`` entry info from named ``structures`` properties.

    ``id`` and ``type`` are copied unwrapped (with ``type``'s description
    adjusted); every other named structures property is frame-wrapped. The
    shared ``last_modified`` property and the trajectory-specific ``nframes``
    and ``reference_frames`` properties are added.
    """
    structure_props = entry_spec.entry_info['structures']['properties']
    properties: dict[str, PropertyInfo] = {}

    for name in structure_properties:
        info = structure_props[name]
        if name == 'id':
            properties['id'] = info.copy()
        elif name == 'type':
            type_info = info.copy()
            type_info['description'] = "The name of the type of this entry, always 'trajectories'"
            properties['type'] = type_info
        else:
            properties[name] = _frame_wrapped(name, info)

    # A shared property (see "Properties Used by Multiple Entry Types").
    properties['last_modified'] = structure_props['last_modified'].copy()

    properties['nframes'] = {
        'description': (
            "The number of frames in the trajectory. This value indicates the number of frames stored in the "
            "data, and may deviate from the number of steps used to calculate the trajectory. For example, a "
            "10 ps simulation with calculation steps of 1 fs where data is stored once every 50 fs, nframes "
            "will be 200. The integer value MUST be equal to the number of frames in the trajectory (i.e., the "
            "length of the dim_frames dimension) and MUST be a positive non-zero value."
        ),
        'type': 'integer',
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
        'type': 'list',
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
