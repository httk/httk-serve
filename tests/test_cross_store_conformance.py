"""Cross-store conformance checks for the portable OPTIMADE query surface.

The remote client is deliberately exercised through a real in-process ASGI
application, rather than Starlette's ``TestClient``: its blocking portal is
not reliable in this workspace.  ``AsgiSyncClient`` is a tiny synchronous
bridge over ``httpx.ASGITransport`` and rejects every non-testserver URL, so
these tests cannot contact the network.
"""

import asyncio
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Any, ClassVar, cast
from urllib.parse import urlsplit

import httpx
import pytest
from httk.atomistic import Species, StructureEntryProvider, UnitcellStructure
from httk.core import EntryProvider, EntryTypeDefinition, RelatedEntry, load_entry_type_definition
from httk.core.optimade import OptimadeResource
from httk.store.db import Database, SqlStore

from httk.serve.optimade import OptimadeStore, adapter_from_providers, create_asgi_app
from httk.serve.optimade.backend.memory_store import InMemoryStore


class AsgiSyncClient:
    """Minimal synchronous httpx client adapter with no network escape hatch."""

    def __init__(self, app: Any, *, base_url: str) -> None:
        self.app = app
        self.base_url = base_url
        self.requests: list[str] = []
        self.closed = False

    def get(self, url: str) -> httpx.Response:
        assert urlsplit(url).netloc == urlsplit(self.base_url).netloc
        self.requests.append(url)

        async def request() -> httpx.Response:
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport) as client:
                return await client.get(url)

        return asyncio.run(request())

    def close(self) -> None:
        self.closed = True


class _FlatStructureProvider(EntryProvider):
    """A standard (not extended) structure endpoint for exact describedby."""

    _definition = load_entry_type_definition("https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/structures")
    _keys: ClassVar[dict[str, str]] = {
        "id": "id",
        "type": "type",
        "immutable_id": "immutable_id",
        "last_modified": "last_modified",
        "elements": "elements",
        "nelements": "nelements",
        "elements_ratios": "elements_ratios",
        "chemical_formula_descriptive": "chemical_formula_descriptive",
        "chemical_formula_reduced": "chemical_formula_reduced",
        "chemical_formula_anonymous": "chemical_formula_anonymous",
        "nperiodic_dimensions": "nperiodic_dimensions",
        "nsites": "nsites",
        "structure_features": "structure_features",
    }

    def __init__(self) -> None:
        base = {
            "type": "structures",
            "immutable_id": "source",
            "last_modified": "2026-07-30T12:00:00+00:00",
            "nperiodic_dimensions": 3,
            "structure_features": [],
        }
        self._rows = (
            base
            | {
                "id": "nacl",
                "elements": ["Cl", "Na"],
                "nelements": 2,
                "elements_ratios": [0.5, 0.5],
                "chemical_formula_descriptive": "ClNa",
                "chemical_formula_reduced": "ClNa",
                "chemical_formula_anonymous": "AB",
                "nsites": 2,
            },
            base
            | {
                "id": "si",
                "elements": ["Si"],
                "nelements": 1,
                "elements_ratios": [1.0],
                "chemical_formula_descriptive": "Si",
                "chemical_formula_reduced": "Si",
                "chemical_formula_anonymous": "A",
                "nsites": 1,
            },
            base
            | {
                "id": "unknown",
                "elements": None,
                "nelements": None,
                "elements_ratios": None,
                "chemical_formula_descriptive": None,
                "chemical_formula_reduced": None,
                "chemical_formula_anonymous": None,
                "nsites": None,
            },
        )

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {"structures": self._definition}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        assert entry_type == "structures"
        return self._keys

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        assert entry_type == "structures"
        return self._rows

    def relationships(self, entry_type: str) -> Mapping[str, tuple[RelatedEntry, ...]]:
        assert entry_type == "structures"
        return {"nacl": (RelatedEntry("references", "r1"),)}


class _FlatReferenceProvider(EntryProvider):
    _definition = load_entry_type_definition("https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/references")

    def entry_types(self) -> Mapping[str, EntryTypeDefinition]:
        return {"references": self._definition}

    def property_keys(self, entry_type: str) -> Mapping[str, str]:
        assert entry_type == "references"
        return {"id": "id", "type": "type", "title": "title"}

    def records(self, entry_type: str) -> Iterable[Mapping[str, Any]]:
        assert entry_type == "references"
        return ({"id": "r1", "type": "references", "title": "Source"},)


def _provider() -> _FlatStructureProvider:
    return _FlatStructureProvider()


def _adapter(provider: _FlatStructureProvider):
    return adapter_from_providers(
        [provider, _FlatReferenceProvider()],
        sortable={"structures": ("id", "nelements")},
    )


def _extended_atomistic_provider() -> StructureEntryProvider:
    sodium = Species(name="Na", chemical_symbols=("Na",), concentration=(1.0,))
    return StructureEntryProvider(
        {
            "sodium": UnitcellStructure(
                [[3, 0, 0], [0, 3, 0], [0, 0, 3]],
                [[0, 0, 0]],
                [sodium],
                ["Na"],
            )
        }
    )


@dataclass
class StoreTarget:
    name: str
    store: object
    target: object


def _ids(
    target: StoreTarget,
    build: Callable[[Any], Any],
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[str]:
    """Run one identical field-level query against one store implementation."""

    searcher = target.store.searcher()  # type: ignore[union-attr]
    variable = searcher.variable(target.target)
    searcher.add(build(variable))
    searcher.add_sort(variable.nelements, descending=True)
    searcher.add_sort(variable.id, descending=False)
    if offset:
        searcher.add_offset(offset)
    if limit is not None:
        searcher.set_limit(limit)
    results = searcher.results(record=variable, identifier=variable.id, formula=variable.chemical_formula_reduced)
    rows = list(results)
    for row in rows:
        assert row.names == ("record", "identifier", "formula")
        assert row.identifier == row["identifier"] == row[1]
        assert row.formula == row["formula"] == row[2]
    return [row.identifier for row in rows]


def _portable_expression(variable: Any) -> Any:
    """Use boolean, literal string/list, membership, and negation operations."""

    nacl = (
        (variable.nelements == 2)
        & variable.chemical_formula_descriptive.contains("Na")
        & variable.chemical_formula_descriptive.endswith("Na")
        & variable.elements.has("Na")
        & variable.elements.has_any("Cl")
        & variable.elements.has_only("Cl", "Na")
        & variable.id.is_in("nacl", "si")
    )
    silicon = (
        (variable.nelements == 1)
        & variable.chemical_formula_descriptive.startswith("Si")
        & variable.id.is_in("nacl", "si")
    )
    return (nacl | silicon) & ~(variable.id == "unknown")


def _all_remote_backends(store: OptimadeStore) -> list[Any]:
    searcher = store.searcher()
    variable = searcher.variable(store.entry_type("structures"))
    return [row.record for row in searcher.results(record=variable)]


def _database(dialect: str) -> Database:
    if dialect == "duckdb":
        pytest.importorskip("duckdb_engine")
        return Database.duckdb()
    assert dialect == "sqlite"
    return Database.sqlite()


@pytest.mark.parametrize("dialect", ("sqlite", "duckdb"))
def test_same_portable_structure_query_across_memory_sql_and_real_asgi_remote(dialect: str) -> None:
    """The normal portable profile has identical results through every store.

    It also verifies the client observes the server's filtered
    ``meta.data_available`` count, not its page's returned count.
    """

    provider = _provider()
    adapter = _adapter(provider)
    base_url = "http://testserver"
    app = create_asgi_app(adapter, baseurl=base_url)
    client = AsgiSyncClient(app, base_url=base_url)
    remote = OptimadeStore(base_url, client=client, page_limit=1)

    # The memory store gets the same public record values the provider serves.
    memory_rows = [dict(record) for record in provider.records("structures")]
    memory = InMemoryStore({"structures": memory_rows})

    remote_backends = _all_remote_backends(remote)
    assert all(type(backend).__name__ == "OptimadeStructure" for backend in remote_backends)

    with _database(dialect) as database:
        sql = SqlStore(database, entry_records={})
        for backend in remote_backends:
            sql.save(backend)

        targets = (
            StoreTarget("memory", memory, "structures"),
            StoreTarget(dialect, sql, type(remote_backends[0])),
            StoreTarget("remote", remote, type(remote_backends[0])),
        )
        for target in targets:
            assert _ids(target, _portable_expression) == ["nacl", "si"], target.name
            assert _ids(target, lambda value: value.chemical_formula_reduced != None) == ["nacl", "si"], target.name
            assert _ids(target, lambda value: value.chemical_formula_reduced == None) == ["unknown"], target.name
            assert set(_ids(target, lambda value: value.chemical_formula_reduced.is_in(None, "Si"))) == {
                "unknown",
                "si",
            }, target.name
            assert set(_ids(target, lambda value: ~value.chemical_formula_reduced.is_in(None, "Si"))) == {"nacl"}, (
                target.name
            )
            assert _ids(target, _portable_expression, limit=1, offset=1) == ["si"], target.name

        # count is unpaged; len has the plan's offset/limit applied.
        remote_searcher = remote.searcher()
        remote_variable = remote_searcher.variable(type(remote_backends[0]))
        remote_searcher.add(_portable_expression(remote_variable))
        remote_searcher.add_offset(1)
        remote_searcher.set_limit(1)
        remote_results = remote_searcher.results(record=remote_variable)
        assert remote_searcher.count() == 2
        assert len(remote_results) == 1
        assert remote_results.first() is not None
        assert remote_results.one().record.id == "si"
        assert [cast(Any, record).id for record in remote_results.scalars("record")] == ["si"]
        assert [cast(Any, record).id for record in remote_results[0:1].scalars("record")] == ["si"]

        # A direct SearchResult retains declared values/names in every backend.
        for target in targets:
            searcher = target.store.searcher()  # type: ignore[union-attr]
            variable = searcher.variable(target.target)
            searcher.add(variable.id == "nacl")
            searcher.output(variable, "record")
            searcher.output(variable.id, "identifier")
            (result,) = list(searcher)
            assert result.names == ("record", "identifier"), target.name
            assert result.values[1] == "nacl", target.name

    assert client.requests
    assert all(urlsplit(url).netloc == "testserver" for url in client.requests)
    assert not client.closed


def test_remote_typed_and_generic_resources_copy_to_sqlite_without_losing_source() -> None:
    """A remote typed backend and its generic source remain exact offline."""

    provider = _provider()
    base_url = "http://testserver"
    adapter = _adapter(provider)
    client = AsgiSyncClient(create_asgi_app(adapter, baseurl=base_url), base_url=base_url)
    remote = OptimadeStore(base_url, client=client)
    backends = _all_remote_backends(remote)
    nacl = next(value for value in backends if value.id == "nacl")
    assert nacl.elements_ratios == (Fraction(1, 2), Fraction(1, 2))
    assert nacl.resource.document.text
    assert nacl.resource.schema.info_document.text

    with Database.sqlite() as database:
        store = SqlStore(database, entry_records={})
        typed_sid = store.save(nacl)
        generic_sid = store.save(nacl.resource)
        typed = store.fetch(type(nacl), typed_sid)
        generic = store.fetch(OptimadeResource, generic_sid)

        assert type(typed) is type(nacl)
        assert typed.resource.document == nacl.resource.document
        assert typed.resource.schema == nacl.resource.schema
        assert typed.elements_ratios == nacl.elements_ratios
        assert generic == nacl.resource
        assert generic["relationships"] == nacl.resource.unwrap()["relationships"]
        assert generic.unwrap() == nacl.resource.unwrap()

        searcher = store.searcher()
        variable = searcher.variable(OptimadeResource)
        searcher.add((variable.id == "nacl") & (variable.type == "structures"))
        assert searcher.results(record=variable).one().record == nacl.resource


def test_extended_own_atomistic_structure_endpoint_is_typed_by_known_property_iris() -> None:
    """Ownerless extension IRIs do not contradict the standard structure binding."""

    base_url = "http://testserver"
    app = create_asgi_app(adapter_from_providers([_extended_atomistic_provider()]), baseurl=base_url)
    client = AsgiSyncClient(app, base_url=base_url)
    remote = OptimadeStore(base_url, client=client)

    descriptor = remote.entry_type("structures")
    assert descriptor.backend.__name__ == "OptimadeStructure"
    searcher = remote.searcher()
    variable = searcher.variable(descriptor.backend)
    row = searcher.results(record=variable, elements=variable.elements).one()
    assert row.record.id == "sodium"
    assert row.elements == ("Na",)
