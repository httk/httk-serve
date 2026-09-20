"""OPTIMADE integration coverage for providers backed by ``httk-store``.

These tests live with the serving package because the store package must remain
usable without installing its downstream protocol consumer.
"""

import datetime
import os
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Annotated, ClassVar

import pytest
from httk.core import (
    DataRecord,
    ProductLink,
    PropertyDefinition,
    Run,
    RunEdge,
)
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, Unique
from httk.store import DataRecordEntryProvider, EntryIdScheme, RunEntryProvider, product_relationships
from httk.store.backend.mongo import MongoDatabase, MongoStore
from httk.store.backend.mongo import StoreEntryProvider as MongoEntryProvider
from httk.store.backend.sql import Backend, SqlStore, StoreEntryProvider
from store_test_support import clickhouse_database, postgres_database

from httk.serve.optimade import adapter_from_providers
from httk.serve.optimade.backend import execute_query
from httk.serve.optimade.filter import parse_optimade_filter


@dataclass(frozen=True)
class Writer:
    name: str
    born: int
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


@dataclass(frozen=True)
class Book:
    title: str
    pages: int
    keywords: list[str]
    author: Writer | None = None
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


ADA = Writer("Ada", 1815, "httk.test.writer-1-1", "httk.test.writer-1-1~1")
BOOLE = Writer("Boole", 1815, "httk.test.writer-1-2", "httk.test.writer-1-2~1")
CARA = Writer("Cara", 1820, "httk.test.writer-1-3", "httk.test.writer-1-3~1")
BOOKS = (
    Book(
        "Analytical Engines",
        350,
        ["computing", "history"],
        ADA,
        "httk.test.book-1-1",
        "httk.test.book-1-1~1",
    ),
    Book("Silence", 120, [], id="httk.test.book-1-2", immutable_id="httk.test.book-1-2~1"),
)


@dataclass(frozen=True)
class MongoWriter:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(dedup="content_id")

    name: str
    born: int
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


@dataclass(frozen=True)
class MongoBook:
    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(dedup="content_id")

    title: str
    pages: int
    keywords: list[str]
    author: MongoWriter | None = None
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class MongoBooks:
    type = "books"


class MongoWriters:
    type = "writers"


# Defined entry families opt into store-minted public and immutable IDs.
register_entry_family(
    name="serve-mongo-books", family=f"{__name__}:MongoBooks", definition_id="urn:httk:test:serve-mongo-books"
)
register_entry_record(
    name="serve-mongo-books-mongobook",
    family="serve-mongo-books",
    record=f"{__name__}:MongoBook",
)
register_entry_family(
    name="serve-mongo-writers", family=f"{__name__}:MongoWriters", definition_id="urn:httk:test:serve-mongo-writers"
)
register_entry_record(
    name="serve-mongo-writers-mongowriter",
    family="serve-mongo-writers",
    record=f"{__name__}:MongoWriter",
)

MONGO_ADA = MongoWriter("Ada", 1815)
MONGO_BOOLE = MongoWriter("Boole", 1815)
MONGO_CARA = MongoWriter("Cara", 1820)
MONGO_BOOKS = (
    MongoBook(
        "Analytical Engines",
        350,
        ["computing", "history"],
        MONGO_ADA,
    ),
    MongoBook("Silence", 120, []),
)


@pytest.fixture(params=("sqlite", "clickhouse", "postgresql"))
def sql_provider(request) -> Iterator[StoreEntryProvider]:
    if request.param == "sqlite":
        database_manager = Backend.sqlite()
    elif request.param == "clickhouse":
        database_manager = clickhouse_database()
    else:
        database_manager = postgres_database()
    with database_manager as database:
        store = SqlStore(database, entry_records={}, entry_ids=EntryIdScheme("httk.test", "1"))
        if request.param == "clickhouse":
            with store.bulk_ingest(finalize="deferred") as bulk:
                for writer in (ADA, BOOLE, CARA):
                    bulk.save(writer)
                for book in BOOKS:
                    bulk.save(book)
        else:
            with store.transaction():
                for writer in (ADA, BOOLE, CARA):
                    store.save(writer)
                for book in BOOKS:
                    store.save(book)
        yield StoreEntryProvider(store, {"books": Book, "writers": Writer})


def test_sql_provider_is_consumed_by_optimade(sql_provider: StoreEntryProvider) -> None:
    adapter = adapter_from_providers([sql_provider])
    assert set(adapter.schema.all_entries) == {"books", "writers"}
    rows = list(
        execute_query(
            adapter,
            ["books"],
            ["id", "_httk_custom_title"],
            [],
            100,
            0,
            parse_optimade_filter("_httk_custom_pages > 200"),
        )
    )
    assert [row.values["id"] for row in rows] == ["httk.test.book-1-1"]
    assert rows[0].values["_httk_custom_title"] == "Analytical Engines"
    rows = list(
        execute_query(
            adapter, ["books"], ["id"], [], 100, 0, parse_optimade_filter('_httk_custom_keywords HAS "history"')
        )
    )
    assert [row.values["id"] for row in rows] == ["httk.test.book-1-1"]
    rows = list(
        execute_query(adapter, ["writers"], ["id"], [], 100, 0, parse_optimade_filter("_httk_custom_born = 1820"))
    )
    assert [row.values["id"] for row in rows] == ["httk.test.writer-1-3"]


@pytest.fixture(scope="session")
def _mongo_client():
    uri = os.environ.get("HTTK_TEST_MONGODB_URI")
    if not uri:
        pytest.skip("HTTK_TEST_MONGODB_URI is not set")
    from pymongo import MongoClient

    client = MongoClient(uri, w="majority", journal=True, readConcernLevel="majority", serverSelectionTimeoutMS=1000)
    try:
        client.admin.command("ping")
    except Exception as error:
        client.close()
        raise RuntimeError(f"MongoDB test server is unreachable: {error}") from error
    try:
        yield client
    finally:
        client.close()


@pytest.fixture()
def mongo_provider(_mongo_client) -> Iterator[MongoEntryProvider]:
    name = f"httk_serve_test_{uuid.uuid4().hex}"
    database = MongoDatabase(_mongo_client, name)
    try:
        store = MongoStore(
            database,
            entry_records={MongoBooks: MongoBook, MongoWriters: MongoWriter},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        for writer in (MONGO_ADA, MONGO_BOOLE, MONGO_CARA):
            store.save(writer)
        for book in MONGO_BOOKS:
            store.save(book)
        yield MongoEntryProvider(store, {"books": MongoBook, "writers": MongoWriter})
    finally:
        database.client.drop_database(name)


def test_mongo_provider_is_consumed_by_optimade(mongo_provider: MongoEntryProvider) -> None:
    adapter = adapter_from_providers([mongo_provider])
    rows = list(
        execute_query(
            adapter,
            ["books"],
            ["id", "_httk_custom_title"],
            [],
            100,
            0,
            parse_optimade_filter("_httk_custom_pages > 200"),
        )
    )
    assert len(rows) == 1
    assert rows[0].values["id"] == "httk.test-1-1"
    assert rows[0].values["_httk_custom_title"] == "Analytical Engines"
    assert rows[0].relationships == {"writers": [{"type": "writers", "id": "httk.test-1-1"}]}


def test_clickhouse_provider_is_consumed_by_optimade() -> None:
    with clickhouse_database() as database:
        store = SqlStore(database, entry_records={}, entry_ids=EntryIdScheme("httk.test", "1"))
        with store.bulk_ingest(finalize="deferred") as bulk:
            for writer in (ADA, BOOLE, CARA):
                bulk.save(writer)
            for book in BOOKS:
                bulk.save(book)
        provider = StoreEntryProvider(store, {"books": Book, "writers": Writer})
        assert sorted(row["__id"] for row in provider.records("books")) == [
            "httk.test.book-1-1",
            "httk.test.book-1-2",
        ]
        adapter = adapter_from_providers([provider])
        rows = list(
            execute_query(
                adapter,
                ["books"],
                ["id", "_httk_custom_title"],
                [],
                100,
                0,
                parse_optimade_filter("_httk_custom_pages > 200"),
            )
        )
        assert [row.values["id"] for row in rows] == ["httk.test.book-1-1"]


UTC = datetime.UTC
ENERGY_ID = "https://schemas.example.org/properties/energy"
FORCE_ID = "https://schemas.example.org/properties/force"


def _definition(name: str, definition_id: str) -> PropertyDefinition:
    return PropertyDefinition.from_simple(
        name, description=f"The {name} value.", fulltype="float", definition_id=definition_id
    )


def test_run_provider_serves_through_optimade() -> None:
    definition = _definition("_httk_energy", ENERGY_ID)
    record = DataRecord.from_value(ENERGY_ID, "_httk_energy", 3.5)
    product = DataRecord.from_value(FORCE_ID, "_httk_force", 2.0)
    run = Run(source_id="ws:job", inputs=(RunEdge("labeled-input", "records", "record-1"),))
    product_links = product_relationships(
        [ProductLink("_httk_records", "record-1", "_httk_records", "record-2", "derived")]
    )
    adapter = adapter_from_providers(
        [
            RunEntryProvider({"run-1": run, "run-2": Run(source_id="other-job")}),
            DataRecordEntryProvider(
                {"record-1": record, "record-2": product},
                definitions={"_httk_energy": definition, "_httk_force": _definition("_httk_force", FORCE_ID)},
                relationships=product_links["_httk_records"],
            ),
        ]
    )
    from starlette.testclient import TestClient

    from httk.serve.optimade import create_asgi_app

    with TestClient(create_asgi_app(adapter, baseurl="http://testserver/")) as client:
        run_response = client.get("/_httk_runs")
        filtered_run_response = client.get("/_httk_runs", params={"filter": '_httk_source_id = "ws:job"'})
        records_response = client.get("/_httk_records")
    assert run_response.status_code == records_response.status_code == 200
    assert filtered_run_response.status_code == 200
    assert [item["id"] for item in filtered_run_response.json()["data"]] == ["run-1"]
    assert filtered_run_response.json()["data"][0]["attributes"]["_httk_source_id"] == "ws:job"
    run_relation = run_response.json()["data"][0]["relationships"]["_httk_has_input"]["data"][0]
    assert run_relation["type"] == "_httk_records"
    assert run_relation["meta"]["_httk_label"] == "labeled-input"
    record = next(item for item in records_response.json()["data"] if item["id"] == "record-1")
    product_relation = record["relationships"]["_httk_has_product"]["data"][0]
    assert product_relation["type"] == "_httk_records"
    assert product_relation["meta"] == {"role": "product", "_httk_label": "derived"}
