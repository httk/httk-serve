"""ASGI coverage for per-entry-type default includes (item 6 / amendment 2).

``adapter_from_stores(..., default_includes={<served type>: (<served type>,
...)})`` declares, per served entry type, the served entry types included by
default on a single-entry request when the client sends no ``include=``
parameter. Every configured default is automatically unioned with
``references`` (the OPTIMADE-mandated include default); an entry type with no
configured default gets exactly that references-only default (unchanged
behavior). An explicit ``include=`` (even empty) fully overrides the default,
and the existing >1-row-list suppression is unaffected.
"""

from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from httk.core.data_records import RECORDS_DEFINITION_ID
from httk.core.provenance import Run, RunEdge, RunEntry
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, Unique
from httk.store import EntryIdScheme
from httk.store.backend.sql import Backend, SqlStore, StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_stores, create_asgi_app
from httk.serve.optimade.schema.served import build_served_schema

_REFERENCES = "https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/references"


@dataclass(frozen=True)
class EdgeRecordRow:
    """A ``records`` backing (prefixed wire type ``_httk_records``) as an edge target."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e6_edge_record")

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class EdgeRecordFamily:
    type = "records"
    definition_id = RECORDS_DEFINITION_ID


@dataclass(frozen=True)
class UnlinkedReference:
    """A ``references`` backing that nothing points to (proves the union, not data)."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="e6_unlinked_reference")

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class ReferenceFamily:
    type = "references"
    definition_id = _REFERENCES


register_entry_family(
    name="e6-edge-records", family=f"{__name__}:EdgeRecordFamily", definition_id=RECORDS_DEFINITION_ID
)
register_entry_record(name="e6-edge-records-rec", family="e6-edge-records", record=f"{__name__}:EdgeRecordRow")
register_entry_family(name="e6-unlinked-reference", family=f"{__name__}:ReferenceFamily", definition_id=_REFERENCES)
register_entry_record(
    name="e6-unlinked-reference-rec", family="e6-unlinked-reference", record=f"{__name__}:UnlinkedReference"
)


def _client(adapter: object) -> TestClient:
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


@contextmanager
def _store_with_one_run():
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={RunEntry: (Run,), EdgeRecordFamily: (EdgeRecordRow,), ReferenceFamily: (UnlinkedReference,)},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        rec = store.fetch(EdgeRecordRow, store.save(EdgeRecordRow("art")), eager=True)
        store.save(UnlinkedReference("unrelated"))
        run = store.fetch(
            Run,
            store.save(Run(artifacts=(RunEdge("art-rec", "records", rec.id),), source_id="ws:job")),
            eager=True,
        )
        yield database, store, run, rec


def _adapter(store: SqlStore) -> object:
    return adapter_from_stores(
        (StoredEntrySource(store, RunEntry, "runs"), StoredEntrySource(store, EdgeRecordFamily, "recs")),
        default_includes={"_httk_runs": ("_httk_records",)},
    )


def test_default_include_hydrates_on_single_entry() -> None:
    with _store_with_one_run() as (_database, store, run, rec), _client(_adapter(store)) as client:
        included = client.get(f"/_httk_runs/{run.id}").json().get("included", [])
        assert ("_httk_records", rec.id) in {(item["type"], item["id"]) for item in included}


def test_explicit_include_overrides_configured_default() -> None:
    with _store_with_one_run() as (_database, store, run, rec), _client(_adapter(store)) as client:
        payload = client.get(f"/_httk_runs/{run.id}", params={"include": "references"}).json()
        included = payload.get("included", [])
        # The configured default (_httk_records) is NOT applied once the
        # client names an explicit include, even though that include
        # selects an unrelated type.
        assert ("_httk_records", rec.id) not in {(item["type"], item["id"]) for item in included}


def test_empty_include_yields_none() -> None:
    with _store_with_one_run() as (_database, store, run, _rec), _client(_adapter(store)) as client:
        payload = client.get(f"/_httk_runs/{run.id}", params={"include": ""}).json()
        assert not payload.get("included")


def test_multi_row_list_suppresses_default_include() -> None:
    with _store_with_one_run() as (_database, store, _run, _rec):
        store.save(Run(source_id="ws:job-2"))  # a second run: listing now has >1 row
        with _client(_adapter(store)) as client:
            payload = client.get("/_httk_runs").json()
            assert not payload.get("included")


def test_references_is_always_unioned_into_configured_defaults() -> None:
    """Every configured per-type default is unioned with 'references' (spec: the
    include-default MUST always cover references), verified at the schema
    level: a served type with no configured default keeps the references-only
    default unchanged.
    """
    from httk.core import load_entry_type_definition

    definitions = {
        "_httk_runs": load_entry_type_definition(RunEntry.definition_id).served_form(),
        "_httk_records": load_entry_type_definition(RECORDS_DEFINITION_ID).served_form(),
        "references": load_entry_type_definition(_REFERENCES),
    }
    schema = build_served_schema(
        definitions,
        default_includes={"_httk_runs": ("_httk_records",)},
    )
    assert schema.default_include_paths["_httk_runs"] == ("_httk_records", "references")
    # No configured entry for _httk_records: exactly today's references-only default.
    assert schema.default_include_paths["_httk_records"] == ("references",)
