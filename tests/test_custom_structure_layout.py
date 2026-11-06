"""Serve a user-defined structures record with its own sites table over OPTIMADE."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Annotated, Any, ClassVar

from httk.atomistic import StructureEntry
from httk.core.storage import (
    IdentitySkip,
    Indexed,
    QueryContext,
    QueryExpression,
    QueryLiteralError,
    Shape,
    StorageInfo,
    StoredPropertyProjection,
    Unique,
)
from httk.store import Backend, EntryFamilyDeclaration, EntryIdScheme, EntryRecordDeclaration, SqlStore
from starlette.testclient import TestClient

from httk.serve.optimade import create_asgi_app


@dataclass(frozen=True)
class _Site:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="test_custom_layout_site", dedup="none")

    x: float
    y: float
    z: float
    symbol: str


def _nsites_query(context: QueryContext, operator: str, literal: object) -> QueryExpression:
    if not isinstance(literal, int) or operator not in {"<", "<=", ">", ">="}:
        raise QueryLiteralError("nsites supports integer orderings only in this test")
    return context.compare(context.count(context.scope("sites")), operator, context.constant(literal))


def _elements_query(context: QueryContext, operator: str, literal: object) -> QueryExpression:
    if operator != "HAS_ALL" or not isinstance(literal, tuple):
        raise QueryLiteralError("elements supports HAS and HAS ALL only in this test")
    sites = context.scope("sites")
    return context.and_(
        *(context.exists(sites, context.equal(sites.field("symbol"), context.constant(value))) for value in literal)
    )


def _symbols(record: Any) -> list[str]:
    return [site.symbol for site in record.sites]


def _elements(record: Any) -> list[str]:
    return sorted(set(_symbols(record)))


@dataclass(frozen=True)
class _SitesStructure:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="test_custom_layout_structure")
    __httk_stored_properties__: ClassVar[Mapping[str, StoredPropertyProjection]] = {
        "structure_features": StoredPropertyProjection(response=lambda record: []),
        "nsites": StoredPropertyProjection(response=lambda record: len(_symbols(record)), query=_nsites_query),
        "elements": StoredPropertyProjection(response=_elements, query=_elements_query),
        "species_at_sites": StoredPropertyProjection(response=_symbols),
    }

    basis: Annotated[tuple[tuple[float, ...], ...], Shape(3, 3)]
    sites: tuple[_Site, ...]
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)

    @property
    def type(self) -> str:
        return "structures"


def _cubic(a: float) -> tuple[tuple[float, ...], ...]:
    return ((a, 0.0, 0.0), (0.0, a, 0.0), (0.0, 0.0, a))


def test_custom_sites_layout_serves_filters_and_response_fields() -> None:
    declaration = EntryFamilyDeclaration(
        name="test-custom-layout-structures",
        family=StructureEntry,
        definition_id=StructureEntry.definition_id,
        records=(EntryRecordDeclaration(name="test-custom-layout-structure", record=_SitesStructure),),
    )
    store = SqlStore(Backend.sqlite(), entry_families=(declaration,), entry_ids=EntryIdScheme("httk.test", "1"))
    store.save(_SitesStructure(_cubic(5.64), (_Site(0.0, 0.0, 0.0, "Na"), _Site(0.5, 0.5, 0.5, "Cl"))))
    store.save(_SitesStructure(_cubic(3.35), (_Site(0.0, 0.0, 0.0, "Po"),)))

    def ids(query: str) -> list[str]:
        response = client.get(query)
        assert response.status_code == 200, response.text
        return [entry["id"] for entry in response.json()["data"]]

    with TestClient(create_asgi_app(store, baseurl="http://testserver"), base_url="http://testserver") as client:
        assert ids("/structures") == ["httk.test-1-1", "httk.test-1-2"]
        assert ids("/structures?filter=nsites>1") == ["httk.test-1-1"]
        assert ids('/structures?filter=elements HAS "Cl"') == ["httk.test-1-1"]
        assert ids('/structures?filter=NOT elements HAS "Cl"') == ["httk.test-1-2"]
        # species_at_sites is projected without query=, so it is response-only.
        assert client.get('/structures?filter=species_at_sites HAS "Na"').status_code == 501
        data = client.get("/structures?response_fields=species_at_sites").json()["data"]
        assert [entry["attributes"] for entry in data] == [
            {"species_at_sites": ["Na", "Cl"]},
            {"species_at_sites": ["Po"]},
        ]
    store.close()
