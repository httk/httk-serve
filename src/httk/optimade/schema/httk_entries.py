"""The subset of the OPTIMADE entry/property definitions served by httk.

This exposes the tables of :func:`httk.optimade.schema.served.default_served_schema`
(the entry types and properties the httk backend implements) as module-level
names, for consumers that do not carry an explicit
:class:`~httk.optimade.schema.served.ServedSchema`.
"""

from .entries import EntryInfo
from .served import default_served_schema

_schema = default_served_schema()

httk_recognized_prefixes: tuple[str, ...] = _schema.recognized_prefixes

httk_all_entries: list[str] = list(_schema.all_entries)

httk_entry_info: dict[str, EntryInfo] = _schema.entry_info

httk_valid_endpoints: list[str] = list(_schema.valid_endpoints)

httk_properties_by_entry: dict[str, list[str]] = {
    entry: list(fields) for entry, fields in _schema.properties_by_entry.items()
}

httk_valid_response_fields = httk_properties_by_entry

default_response_fields: dict[str, list[str]] = {
    entry: list(fields) for entry, fields in _schema.default_response_fields.items()
}

required_response_fields: dict[str, list[str]] = {
    entry: list(fields) for entry, fields in _schema.required_response_fields.items()
}

# Properties defined by the specification for an entry type, but not
# implemented by the httk backend.
httk_unknown_response_fields: dict[str, list[str]] = {
    entry: list(fields) for entry, fields in _schema.unknown_response_fields.items()
}
