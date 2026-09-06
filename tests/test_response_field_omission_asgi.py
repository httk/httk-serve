"""ASGI coverage for the OPTIMADE "properties with an unknown value" rule.

Item 5 / amendment 1: with no ``response_fields`` query parameter, a
null-valued attribute is omitted from the response unless its definition is
response-level ``must`` (regardless of nullability, e.g. a nullable
``last_modified``) or ``always`` (``id``/``type``). With ``response_fields``
present, every requested field is served exactly as returned, nulls kept
(including an unrecognized provider-specific field, served as null with a
warning). Included resources (``include=``) are assembled with no request
context and always follow the absent-param rule.
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
class SparseReference:
    """A ``references`` backing that only stores ``doi``; every other spec
    property (``title``, ``journal``, ...) is therefore unknown (``null``)."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e5_sparse_reference")

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
    """A ``calculations`` backing with a Related reference, for the included-resources check."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e5_work")

    name: str
    ref: Annotated[SparseReference | None, Related(role="primary")] = None
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class ReferenceFamily:
    type = "references"
    definition_id = _REFERENCES


class WorkFamily:
    type = "calculations"
    definition_id = _CALCULATIONS


register_entry_family(name="e5-sparse-reference", family=f"{__name__}:ReferenceFamily", definition_id=_REFERENCES)
register_entry_record(
    name="e5-sparse-reference-rec", family="e5-sparse-reference", record=f"{__name__}:SparseReference"
)
register_entry_family(name="e5-work", family=f"{__name__}:WorkFamily", definition_id=_CALCULATIONS)
register_entry_record(name="e5-work-rec", family="e5-work", record=f"{__name__}:WorkRecord")


@pytest.fixture
def store_and_ids() -> "Iterator[tuple[SqlStore, str, str]]":
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={ReferenceFamily: SparseReference, WorkFamily: WorkRecord},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        ref = store.fetch(SparseReference, store.save(SparseReference("10.9/sparse")), eager=True)
        work = store.fetch(WorkRecord, store.save(WorkRecord("W", ref=ref)), eager=True)
        yield store, ref.id, work.id


def _adapter(store: SqlStore) -> object:
    return adapter_from_stores(
        (StoredEntrySource(store, ReferenceFamily, "ref"), StoredEntrySource(store, WorkFamily, "work"))
    )


def _client(store: SqlStore) -> TestClient:
    return TestClient(create_asgi_app(_adapter(store), baseurl="http://testserver"), base_url="http://testserver")


def test_default_response_omits_null_optional_and_keeps_must_level_null(store_and_ids) -> None:
    store, _ref_id, _work_id = store_and_ids
    with _client(store) as c:
        attributes = c.get("/references").json()["data"][0]["attributes"]
        assert attributes["doi"] == "10.9/sparse"
        # last_modified is response-level "must" (nullable): kept even though null.
        assert "last_modified" in attributes
        assert attributes["last_modified"] is None
        # "may"-level unknown-valued properties: omitted, not requested.
        # (immutable_id is not one: the store stamps it with the revision id.)
        for name in ("title", "journal", "year", "authors"):
            assert name not in attributes


def test_explicit_response_fields_keeps_requested_nulls(store_and_ids) -> None:
    store, _ref_id, _work_id = store_and_ids
    with _client(store) as c:
        attributes = c.get("/references", params={"response_fields": "doi,title"}).json()["data"][0]["attributes"]
        assert attributes["doi"] == "10.9/sparse"
        assert "title" in attributes
        assert attributes["title"] is None
        # Only the requested (+ required) fields are served.
        assert "journal" not in attributes


def test_unrecognized_prefixed_response_field_served_null_with_warning(store_and_ids) -> None:
    store, _ref_id, _work_id = store_and_ids
    with _client(store) as c:
        payload = c.get("/references", params={"response_fields": "doi,_unknownprovider_field"}).json()
        attributes = payload["data"][0]["attributes"]
        assert "_unknownprovider_field" in attributes
        assert attributes["_unknownprovider_field"] is None
        assert any(w.get("title") == "Unrecognized response field" for w in payload["meta"]["warnings"])


def test_included_resources_omit_nulls(store_and_ids) -> None:
    store, ref_id, _work_id = store_and_ids
    with _client(store) as c:
        payload = c.get("/calculations", params={"include": "references"}).json()
        included = next(item for item in payload["included"] if item["id"] == ref_id)
        attributes = included["attributes"]
        assert attributes["doi"] == "10.9/sparse"
        assert "last_modified" in attributes
        assert attributes["last_modified"] is None
        for name in ("title", "journal", "year", "authors"):
            assert name not in attributes
