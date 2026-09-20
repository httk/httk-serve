"""Live database setup shared by the store-backed serving tests."""

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import sqlalchemy
from httk.store.backend.sql import Backend


def clickhouse_test_uri() -> str:
    """Return the configured ClickHouse URI, skipping the optional arm when absent."""
    uri = os.environ.get("HTTK_TEST_CLICKHOUSE_URI")
    if not uri:
        pytest.skip("HTTK_TEST_CLICKHOUSE_URI is not set; configure the ClickHouse test server first")
    return uri


@contextmanager
def clickhouse_database() -> Iterator[Backend]:
    """Yield an isolated ClickHouse database with the required Keeper table."""
    source = sqlalchemy.engine.make_url(clickhouse_test_uri())
    name = f"httk_serve_{uuid.uuid4().hex}"
    admin = sqlalchemy.create_engine(source.set(database="default"))
    database = None
    try:
        with admin.begin() as connection:
            present = connection.execute(
                sqlalchemy.text(
                    "SELECT count() FROM system.tables WHERE database = 'default' AND name = '_httk_bootstrap'"
                )
            ).scalar_one()
            if not present:
                raise RuntimeError("ClickHouse deployment table _httk_bootstrap is absent")
            connection.execute(sqlalchemy.text(f"CREATE DATABASE {name}"))
        bootstrap = sqlalchemy.create_engine(source.set(database=name))
        try:
            with bootstrap.begin() as connection:
                connection.execute(
                    sqlalchemy.text(
                        "CREATE TABLE _httk_bootstrap (key String, value String) "
                        "ENGINE=KeeperMap('/_httk_bootstrap') PRIMARY KEY key"
                    )
                )
        finally:
            bootstrap.dispose()
        database = Backend.clickhouse(source, database=name)
        yield database
    finally:
        if database is not None:
            database.dispose()
        with admin.begin() as connection:
            connection.execute(sqlalchemy.text(f"DROP DATABASE IF EXISTS {name}"))
        admin.dispose()


def postgres_test_uri() -> str:
    """Return the configured PostgreSQL URI, skipping the optional arm when absent."""
    uri = os.environ.get("HTTK_TEST_POSTGRES_URI")
    if not uri:
        pytest.skip("HTTK_TEST_POSTGRES_URI is not set; configure the PostgreSQL test server first")
    return uri


@contextmanager
def postgres_database() -> Iterator[Backend]:
    """Yield an isolated PostgreSQL database."""
    source = sqlalchemy.engine.make_url(postgres_test_uri())
    name = f"httk_serve_{uuid.uuid4().hex}"
    admin = sqlalchemy.create_engine(source, isolation_level="AUTOCOMMIT")
    try:
        with admin.begin() as connection:
            connection.execute(sqlalchemy.text(f'CREATE DATABASE "{name}"'))
        database = Backend.postgresql(source.set(database=name))
        try:
            yield database
        finally:
            database.dispose()
    finally:
        with admin.begin() as connection:
            connection.execute(sqlalchemy.text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()
