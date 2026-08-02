"""Build an OPTIMADE adapter over lazy SQL-backed entry federation.

The durable layout, SQL translation, collision policy, and bounded global
pagination live in :mod:`httk.data.db`.  This module owns only the serving
boundary: the advertised OPTIMADE schema, request-error translation, public
response-field projection, and :class:`~httk.serve.optimade.model.ResultRow`
objects consumed by the endpoint envelope code.

Imports from ``httk.data.db`` deliberately remain inside call sites.  Importing
``httk.serve.optimade`` therefore does not initialize a database backend or
load optional SQL dialects.
"""

from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, NoReturn, cast

from httk.core import EntryTypeDefinition, FilterAst, stored_property_projections
from httk.data.optimade_query import FilterTranslationError

from ..model.errors import OptimadeError, TranslatorError, translator_error_from
from ..model.results import QueryFunction, QueryResults, ResultRow
from ..schema.served import ServedSchema, build_served_schema

if TYPE_CHECKING:
    from httk.data.db import StoredEntrySource


@dataclass(frozen=True, slots=True)
class _StoredQueryResults:
    """One already-bounded data-owned page exposed through QueryResults."""

    rows: tuple[ResultRow, ...]
    more_data_available: bool
    total_count: int

    def count(self) -> int:
        return self.total_count

    def __iter__(self) -> Iterator[ResultRow]:
        return iter(self.rows)


@dataclass(frozen=True, slots=True)
class StoredBackendAdapter:
    """An OPTIMADE serving adapter over one data federation per entry type."""

    federations: Mapping[str, Any]
    schema: ServedSchema

    def __post_init__(self) -> None:
        object.__setattr__(self, "federations", MappingProxyType(dict(self.federations)))

    def query_function(self) -> QueryFunction:
        def query(
            entries: list[str],
            response_fields: list[str],
            unknown_response_fields: list[str],
            page_limit: int,
            page_offset: int,
            filter_ast: FilterAst | None = None,
            *,
            sort: Sequence[tuple[str, bool]] | None = None,
            debug: bool = False,
        ) -> QueryResults:
            del debug
            if len(entries) != 1 or entries[0] not in self.federations:
                raise TranslatorError(
                    "Stored OPTIMADE queries must target exactly one configured entry type.",
                    500,
                    "Internal server error",
                )
            entry_type = entries[0]
            federation = self.federations[entry_type]
            limit = int(page_limit)
            offset = int(page_offset)
            try:
                public_id = _exact_id_filter(filter_ast)
                if public_id is not None and offset == 0 and limit > 0:
                    found = federation.fetch(public_id)
                    total_count = 0 if found is None else 1
                    page_rows = () if found is None or offset or limit == 0 else (found,)
                    more_data_available = False
                else:
                    page = federation.query(
                        filter_ast,
                        sort=tuple(sort or ()),
                        offset=offset,
                        limit=limit,
                    )
                    page_rows = page.rows
                    more_data_available = page.more_data_available
                    total_count = page.total_count
            except FilterTranslationError as error:
                raise translator_error_from(error) from error
            except Exception as error:
                _raise_stored_error(error)

            projected = tuple(
                ResultRow(
                    values=_project_public_row(
                        row,
                        entry_type,
                        response_fields,
                        unknown_response_fields,
                    )
                )
                for row in page_rows
            )
            return _StoredQueryResults(projected, bool(more_data_available), int(total_count))

        return query


def _exact_id_filter(filter_ast: FilterAst | None) -> str | None:
    """Return the id from the canonical exact-id AST used by single fetches."""
    if (
        filter_ast is not None
        and len(filter_ast) == 3
        and filter_ast[0] == "="
        and filter_ast[1] == ("Identifier", "id")
        and isinstance(filter_ast[2], tuple)
        and len(filter_ast[2]) == 2
        and filter_ast[2][0] == "String"
        and isinstance(filter_ast[2][1], str)
    ):
        return cast(str, filter_ast[2][1])
    return None


def _project_public_row(
    row: Mapping[str, Any],
    entry_type: str,
    response_fields: Sequence[str],
    unknown_response_fields: Sequence[str],
) -> dict[str, Any]:
    """Select only protocol-requested fields from one public federation row."""
    try:
        public_id = row["id"]
        row_type = row["type"]
    except KeyError as error:
        raise OptimadeError(
            "Stored entry federation returned a row without public id/type.",
            500,
            "Internal server error",
        ) from error
    if not isinstance(public_id, str) or not public_id:
        raise OptimadeError(
            "Stored entry federation returned an invalid public entry id.",
            500,
            "Internal server error",
        )
    if row_type != entry_type:
        raise OptimadeError(
            "Stored entry federation returned an entry under the wrong endpoint type.",
            500,
            "Internal server error",
        )

    result: dict[str, Any] = {name: None for name in unknown_response_fields}
    result.update({name: row.get(name) for name in response_fields})
    # id/type are required response fields, but retain the invariant even for a
    # direct query-function caller that supplies a narrower field list.
    result["id"] = public_id
    result["type"] = row_type
    return result


def _raise_stored_error(error: Exception) -> NoReturn:
    """Translate data-owned federation failures without leaking SQL details."""
    from httk.data.db import DuplicateEntryIdError

    if isinstance(error, DuplicateEntryIdError):
        public_id = getattr(error, "public_id", None)
        origins = getattr(error, "origins", ())
        origin_names = tuple(
            f"{origin.source}/{origin.backing}"
            for origin in origins
            if isinstance(getattr(origin, "source", None), str) and isinstance(getattr(origin, "backing", None), str)
        )
        detail = "Duplicate public entry id"
        if isinstance(public_id, str):
            detail += f" {public_id!r}"
        if origin_names:
            detail += " was found in " + ", ".join(origin_names)
        detail += "; run audit_duplicate_ids() on the stored federation."
        raise OptimadeError(detail, 500, "Internal server error") from error
    raise error


def _validate_sortable_backings(
    plans: Sequence[Any],
    entry_type: str,
    sortable: Sequence[str],
) -> None:
    """Fail adapter construction when an advertised sort cannot be exact."""
    for name in sortable:
        if name in {"id", "type"}:
            continue
        for plan in plans:
            for backing in plan.backings:
                projection = stored_property_projections(backing).get(name)
                if projection is None or projection.sort is None:
                    raise ValueError(
                        f"Property {name!r} is marked sortable for entry type {entry_type!r}, "
                        f"but {backing.__name__} has no exact stored sort mapping."
                    )


def _sortable_intersection(plans: Sequence[Any], property_names: Sequence[str]) -> tuple[str, ...]:
    """Return properties with an exact sort mapping on every durable backing."""
    sortable: list[str] = []
    for name in property_names:
        if name in {"id", "type"}:
            sortable.append(name)
            continue
        if all(
            (projection := stored_property_projections(backing).get(name)) is not None and projection.sort is not None
            for plan in plans
            for backing in plan.backings
        ):
            sortable.append(name)
    return tuple(sortable)


def adapter_from_stores(
    sources: Sequence["StoredEntrySource"],
    **options: Any,
) -> StoredBackendAdapter:
    """Build a lazy store-backed adapter from durable entry sources.

    Sources with the same exact logical family are federated under one entry
    endpoint.  The data layer owns all source/backing traversal and global
    pagination; this adapter advertises the family's definition and turns only
    the returned page into OPTIMADE result rows.
    """
    from httk.data.db import StoredEntryFederation, StoredEntrySource, stored_property_sql_plan

    values = tuple(sources)
    if not values:
        raise ValueError("adapter_from_stores requires at least one StoredEntrySource")
    if not all(isinstance(source, StoredEntrySource) for source in values):
        raise TypeError("adapter_from_stores sources must contain StoredEntrySource values")

    grouped: dict[str, list[Any]] = {}
    families: dict[str, type] = {}
    definitions: dict[str, EntryTypeDefinition] = {}
    plans_by_entry: dict[str, list[Any]] = {}
    for source in values:
        plan = stored_property_sql_plan(source.store, source.entry_family)
        entry_type = plan.entry_type
        existing_family = families.get(entry_type)
        if existing_family is not None and existing_family is not source.entry_family:
            raise ValueError(f"entry type {entry_type!r} is supplied by more than one logical entry family")
        existing_definition = definitions.get(entry_type)
        if existing_definition is not None and existing_definition != plan.definition:
            raise ValueError(f"stored sources for entry type {entry_type!r} use different definitions")
        families[entry_type] = source.entry_family
        definitions[entry_type] = plan.definition
        grouped.setdefault(entry_type, []).append(source)
        plans_by_entry.setdefault(entry_type, []).append(plan)

    served = {entry_type: tuple(definition.properties) for entry_type, definition in definitions.items()}
    defaults = {
        entry_type: tuple(name for name in property_names if name not in {"id", "type"})
        for entry_type, property_names in served.items()
    }
    if "sortable" not in options:
        options["sortable"] = {
            entry_type: _sortable_intersection(plans_by_entry[entry_type], property_names)
            for entry_type, property_names in served.items()
        }
    schema = build_served_schema(
        definitions,
        served,
        default_response_overrides=defaults,
        **options,
    )
    for entry_type, plans in plans_by_entry.items():
        _validate_sortable_backings(plans, entry_type, schema.sortable_response_fields[entry_type])

    federations = {
        entry_type: StoredEntryFederation(tuple(entry_sources)) for entry_type, entry_sources in grouped.items()
    }
    return StoredBackendAdapter(federations, schema)
