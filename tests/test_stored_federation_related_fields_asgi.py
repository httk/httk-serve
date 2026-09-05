"""ASGI coverage for stored federation Related reference/child field serving.

Pins the E3 serving edge end-to-end on the store-backed adapter: a family's
Related-marked reference and child fields render as OPTIMADE relationship blocks
keyed by the target's served wire type, ``include=`` inlines those targets off
the newly served block, and depth-1 related-property filtering
(``references.doi CONTAINS ...``) resolves the sibling family's own ids through
the resolver wired in ``adapter_from_stores``.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, ClassVar

import pytest
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, Related, StorageInfo, StoredPropertyProjection, Unique
from httk.store import EntryIdScheme
from httk.store.backend.sql import Backend, SqlStore, StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_stores, create_asgi_app

_REFERENCES = "https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/references"
_CALCULATIONS = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/calculations"


@dataclass(frozen=True)
class PeerRecord:
    """The relationship target (served ``references``), with a queryable ``doi``."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e3_related_peer")

    doi: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)

    __httk_stored_properties__: ClassVar = {
        "doi": StoredPropertyProjection(
            response=lambda record: record.doi,
            query=lambda context, operator, literal: context.compare(
                context.field("doi"), operator, context.constant(literal)
            ),
            sort=lambda context: context.field("doi"),
        )
    }


@dataclass(frozen=True)
class WorkRecord:
    """A ``calculations`` backing with Related reference and child fields."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e3_related_work")

    name: str
    lead: Annotated[PeerRecord | None, Related(role="lead", description="Lead reference")] = None
    hidden: Annotated[PeerRecord | None, Related(serve=False)] = None
    members: Annotated[tuple[PeerRecord, ...], Related(role="member")] = ()
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class PeerFamily:
    type = "references"
    definition_id = _REFERENCES


class WorkFamily:
    type = "calculations"
    definition_id = _CALCULATIONS


register_entry_family(name="e3-related-peer", family=f"{__name__}:PeerFamily", definition_id=_REFERENCES)
register_entry_record(name="e3-related-peer-rec", family="e3-related-peer", record=f"{__name__}:PeerRecord")
register_entry_family(name="e3-related-work", family=f"{__name__}:WorkFamily", definition_id=_CALCULATIONS)
register_entry_record(name="e3-related-work-rec", family="e3-related-work", record=f"{__name__}:WorkRecord")


@pytest.fixture
def store_and_ids() -> "Iterator[tuple[SqlStore, str, str, str, str]]":
    with Backend.sqlite() as database:
        yield _store_and_ids(database)


def _store_and_ids(database: "Backend") -> tuple[SqlStore, str, str, str, str]:
    store = SqlStore(
        database,
        entry_records={WorkFamily: WorkRecord, PeerFamily: PeerRecord},
        entry_ids=EntryIdScheme("httk.test", "1"),
    )
    # Distinct lead references (10.1, 10.2) drive the filter assertions; the
    # child member (10.3) exercises the served block/include but not filtering
    # (reference-field `.id` filtering resolves the single reference field, so a
    # doi asserted through filtering is always a lead's).
    ada = store.fetch(PeerRecord, store.save(PeerRecord("10.1/ada")), eager=True)
    boole = store.fetch(PeerRecord, store.save(PeerRecord("10.2/boole")), eager=True)
    cara = store.fetch(PeerRecord, store.save(PeerRecord("10.3/cara")), eager=True)
    work_a = store.fetch(WorkRecord, store.save(WorkRecord("A", lead=ada, members=(cara,))), eager=True)
    work_b = store.fetch(WorkRecord, store.save(WorkRecord("B", lead=boole)), eager=True)
    return store, ada.id, cara.id, work_a.id, work_b.id


def _adapter(store: SqlStore) -> object:
    return adapter_from_stores(
        (StoredEntrySource(store, WorkFamily, "work"), StoredEntrySource(store, PeerFamily, "peer")),
    )


def test_reference_and_child_blocks_served_and_included(store_and_ids) -> None:
    store, ada_id, cara_id, work_a_id, _work_b_id = store_and_ids
    with TestClient(create_asgi_app(_adapter(store), baseurl="http://testserver"), base_url="http://testserver") as c:
        payload = c.get("/calculations", params={"sort": "id"}).json()
        by_id = {item["id"]: item for item in payload["data"]}
        block = by_id[work_a_id]["relationships"]["references"]["data"]
        # Lead reference and child member render under the target's wire type,
        # role/description ride the identifiers, and the suppressed field is absent.
        assert [(entry["type"], entry["id"]) for entry in block] == [
            ("references", ada_id),
            ("references", cara_id),
        ]
        lead = next(entry for entry in block if entry["id"] == ada_id)
        assert lead["meta"]["role"] == "lead"
        assert lead["meta"]["description"] == "Lead reference"

        # include inlines the referenced peers off the newly served block.
        included = c.get("/calculations", params={"include": "references"}).json().get("included", [])
        assert {(item["type"], item["id"]) for item in included} >= {("references", ada_id), ("references", cara_id)}


def test_depth1_reference_property_filter_end_to_end(store_and_ids) -> None:
    store, _ada_id, _cara_id, work_a_id, work_b_id = store_and_ids
    with TestClient(create_asgi_app(_adapter(store), baseurl="http://testserver"), base_url="http://testserver") as c:

        def ids(filter_string: str) -> list[str]:
            payload = c.get("/calculations", params={"filter": filter_string}).json()
            return sorted(item["id"] for item in payload["data"])

        # work_a leads with ada (10.1); work_b leads with boole (10.2).
        assert ids('references.doi CONTAINS "10.1"') == [work_a_id]
        assert ids('references.doi CONTAINS "10.2"') == [work_b_id]
        # No reference matches: an empty result, and NOT is the complement.
        assert ids('references.doi CONTAINS "nomatch"') == []
        assert ids('NOT (references.doi CONTAINS "10.1")') == [work_b_id]


def test_prefixed_typed_relationships_include_and_filter_union(store_and_ids) -> None:
    store, ada_id, cara_id, work_a_id, work_b_id = store_and_ids
    adapter = adapter_from_stores(
        (
            StoredEntrySource(store, WorkFamily, "work", "W:"),
            StoredEntrySource(store, PeerFamily, "peer", "P:"),
        )
    )
    with TestClient(create_asgi_app(adapter, baseurl="http://testserver")) as client:
        response = client.get("/calculations", params={"include": "references"})
        assert response.status_code == 200, response.text
        payload = response.json()
        resource = next(row for row in payload["data"] if row["id"] == "W:" + work_a_id)
        assert {item["id"] for item in resource["relationships"]["references"]["data"]} == {
            "P:" + ada_id,
            "P:" + cara_id,
        }
        assert {row["id"] for row in payload["included"]} >= {
            "P:" + ada_id,
            "P:" + cara_id,
        }

        def ids(expression):
            result = client.get("/calculations", params={"filter": expression})
            assert result.status_code == 200, result.text
            return {row["id"] for row in result.json()["data"]}

        for key in ("references.id", "_httk_relationships.references.id"):
            assert ids(f'{key} HAS "P:{ada_id}"') == {"W:" + work_a_id}
            assert ids(f'{key} HAS ALL "P:{ada_id}", "P:{cara_id}"') == {"W:" + work_a_id}
            assert ids(f'{key} HAS ONLY "P:{ada_id}", "P:{cara_id}"') == {"W:" + work_a_id}
            assert ids(f'{key} HAS ALL "P:{ada_id}", "X:{cara_id}"') == set()
            assert ids(f'{key} HAS "{ada_id}"') == set()
        assert ids('references.doi CONTAINS "10.3"') == {"W:" + work_a_id}
        assert ids('NOT (references.doi CONTAINS "10.1")') == {"W:" + work_b_id}
