"""ASGI coverage for stored weak-link relationships and provider wire naming.

Exercises the serving edge added in P6: the store-backed adapter now plans each
source's SERVED (wire) definition (``EntryTypeDefinition.served_form``) and
forwards the federation's per-row exposed weak-link relationships into the
OPTIMADE envelope. Both a standard (unprefixed) declaring family and its
standard target are federated here; the run (prefixed) family is served
end-to-end by :func:`test_runs_family_served_end_to_end`.
"""

from dataclasses import dataclass, field
from typing import Annotated, ClassVar

from httk.core import load_entry_type_definition
from httk.core.data_records import RECORDS_DEFINITION_ID
from httk.core.provenance import Run, RunEdge, RunEntry
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, Unique, WeakLink
from httk.store import EntryIdScheme
from httk.store.backend.sql import Backend, SqlStore, StoredEntrySource
from starlette.testclient import TestClient

from httk.serve.optimade import adapter_from_stores, create_asgi_app

_CALCULATIONS = "https://schemas.optimade.org/defs/v1.3/entrytypes/optimade/calculations"
_REFERENCES = "https://schemas.optimade.org/defs/v1.2/entrytypes/optimade/references"


@dataclass(frozen=True)
class ReferenceRecord:
    """The linked-to (standard ``references``) family's backing."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="p6_rel_reference")

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


@dataclass(frozen=True)
class CalculationRecord:
    """The declaring (standard ``calculations``) family, exposing one weak link."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="p6_rel_calculation",
        links=(
            WeakLink(
                "produced_by",
                ReferenceRecord,
                exposed_relationship=True,
                role="artifact+output",
                description="Produced by",
            ),
        ),
    )

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class CalculationFamily:
    """The declaring, federation-served standard family."""

    type = "calculations"
    definition_id = _CALCULATIONS


class ReferenceFamily:
    """The linked-to standard family."""

    type = "references"
    definition_id = _REFERENCES


@dataclass(frozen=True)
class RunLinkingRecord:
    """A standard ``calculations`` backing whose weak link targets :class:`Run`."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(
        storage_name="p6_rel_runlink",
        links=(
            WeakLink(
                "produced_by",
                Run,
                exposed_relationship=True,
                role="artifact+output",
                description="Produced by",
            ),
        ),
    )

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class RunLinkingFamily:
    """A standard family whose exposed weak link points at the run family."""

    type = "calculations"
    definition_id = _CALCULATIONS


register_entry_family(name="p6-rel-calc", family=f"{__name__}:CalculationFamily", definition_id=_CALCULATIONS)
register_entry_record(name="p6-rel-calc-rec", family="p6-rel-calc", record=f"{__name__}:CalculationRecord")
register_entry_family(name="p6-rel-ref", family=f"{__name__}:ReferenceFamily", definition_id=_REFERENCES)
register_entry_record(name="p6-rel-ref-rec", family="p6-rel-ref", record=f"{__name__}:ReferenceRecord")
register_entry_family(name="p6-rel-runlink", family=f"{__name__}:RunLinkingFamily", definition_id=_CALCULATIONS)
register_entry_record(name="p6-rel-runlink-rec", family="p6-rel-runlink", record=f"{__name__}:RunLinkingRecord")


def _client(adapter: object) -> TestClient:
    return TestClient(create_asgi_app(adapter, baseurl="http://testserver"), base_url="http://testserver")


def test_standard_weak_link_relationships_served_end_to_end() -> None:
    """A standard family's exposed weak link renders as an OPTIMADE relationship.

    Pins the P6 serving edge for a family the federation can serve today: the
    relationship block, role/description/``_httk_label`` metadata, ``include``
    inlining, the served_form identity of a standard family, and the routable
    derived endpoints.
    """
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={CalculationFamily: (CalculationRecord,), ReferenceFamily: (ReferenceRecord,)},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        store.save(ReferenceRecord("r0"))  # shift the linked target off the first minted id
        reference = store.fetch(ReferenceRecord, store.save(ReferenceRecord("r1")), eager=True)
        calculation = store.fetch(CalculationRecord, store.save(CalculationRecord("c1")), eager=True)
        store.save(CalculationRecord("c2"))  # a sibling that carries no link
        store.link(calculation, "produced_by", reference)
        adapter = adapter_from_stores(
            (StoredEntrySource(store, CalculationFamily, "calc"), StoredEntrySource(store, ReferenceFamily, "ref")),
        )

        # (g) A standard definition's served form is identity: unprefixed name
        # and unprefixed properties, byte-identical to the pre-series expectation.
        internal = load_entry_type_definition(_CALCULATIONS)
        assert internal.served_form() is internal
        assert "calculations" in adapter.schema.all_entries
        assert all(
            name in {"id", "type", "immutable_id", "last_modified"} or not name.startswith("_")
            for name in adapter.schema.properties_by_entry["calculations"]
        )

        with _client(adapter) as client:
            # (a) info advertises the mounted endpoints and entry types, wire-keyed.
            info = client.get("/info").json()["data"]["attributes"]
            assert {"calculations", "references"} <= set(info["available_endpoints"])
            assert {"_httk_calculations~revs", "_httk_calculations~alts"} <= set(info["available_endpoints"])
            assert set(info["entry_types_by_format"]["json"]) == {"calculations", "references"}

            # (d) the declaring resource carries the relationship with role + label,
            # and its unlinked sibling and the target side carry none.
            payload = client.get("/calculations", params={"sort": "id"}).json()
            by_id = {item["id"]: item for item in payload["data"]}
            linked = by_id[calculation.id]
            related = linked["relationships"]["references"]["data"]
            assert [entry["type"] for entry in related] == ["references"]
            assert related[0]["id"] == reference.id
            assert related[0]["meta"]["role"] == "artifact+output"
            assert related[0]["meta"]["description"] == "Produced by"
            assert related[0]["meta"]["_httk_label"] == "produced_by"
            assert all("relationships" not in item for id_, item in by_id.items() if id_ != calculation.id)
            refs = client.get("/references").json()["data"]
            assert all("relationships" not in item for item in refs)

            # (e) include inlines the related reference resource.
            included = client.get("/calculations", params={"include": "references"}).json().get("included", [])
            assert (("references", reference.id)) in {(item["type"], item["id"]) for item in included}

            # (f) derived endpoints are present and routable.
            assert client.get("/_httk_calculations~revs").status_code == 200
            assert client.get("/_httk_calculations~alts").status_code == 200


def test_runs_family_served_end_to_end() -> None:
    """The runs (prefixed) family serves at ``_httk_runs`` with prefixed values.

    Documents the full P6 intent for the vendored runs definition: the endpoint,
    the F2 silent-null guard (prefixed property values serve, filter and sort),
    the wire-named relationship, and the single-prefix derived endpoints.
    """
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={RunEntry: (Run,), RunLinkingFamily: (RunLinkingRecord,)},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        run = store.fetch(
            Run,
            store.save(Run(source_id="ws:job-42", workflow_declaration_uri="https://example.org/workflows/1")),
            eager=True,
        )
        artifact = store.fetch(RunLinkingRecord, store.save(RunLinkingRecord("c1")), eager=True)
        store.link(artifact, "produced_by", run)
        adapter = adapter_from_stores(
            (StoredEntrySource(store, RunLinkingFamily, "calc"), StoredEntrySource(store, RunEntry, "runs")),
        )

        with _client(adapter) as client:
            # (a) the runs endpoint is advertised under its wire name.
            info = client.get("/info").json()["data"]["attributes"]
            assert "_httk_runs" in info["available_endpoints"]
            assert "_httk_runs" in info["entry_types_by_format"]["json"]

            # (b) prefixed property values serve (F2: never silently null), and the
            # same names filter and sort over the runs endpoint.
            data = client.get(
                "/_httk_runs",
                params={"response_fields": "id,type,_httk_source_id,_httk_workflow_declaration_uri"},
            ).json()["data"]
            resource = data[0]
            assert resource["type"] == "_httk_runs"
            assert resource["attributes"]["_httk_source_id"] == "ws:job-42"
            assert resource["attributes"]["_httk_workflow_declaration_uri"] == "https://example.org/workflows/1"
            filtered = client.get("/_httk_runs", params={"filter": '_httk_source_id="ws:job-42"'}).json()
            assert [item["id"] for item in filtered["data"]] == [run.id]
            assert client.get("/_httk_runs", params={"sort": "_httk_source_id"}).status_code == 200

            # (c) the wire type filter constant matches.
            typed = client.get("/_httk_runs", params={"filter": 'type="_httk_runs"'}).json()
            assert [item["id"] for item in typed["data"]] == [run.id]

            # (d) the declaring family carries a wire-named relationship to the run.
            calc = client.get("/calculations").json()["data"][0]
            related = calc["relationships"]["_httk_runs"]["data"]
            assert related[0]["id"] == run.id
            assert related[0]["meta"]["role"] == "artifact+output"
            assert related[0]["meta"]["_httk_label"] == "produced_by"

            # (e) include inlines the run resource.
            included = client.get("/calculations", params={"include": "_httk_runs"}).json().get("included", [])
            assert (("_httk_runs", run.id)) in {(item["type"], item["id"]) for item in included}

            # (f) single-prefix derived endpoints are present and routable.
            assert {"_httk_runs~revs", "_httk_runs~alts"} <= set(info["available_endpoints"])
            assert client.get("/_httk_runs~revs").status_code == 200
            assert client.get("/_httk_runs~alts").status_code == 200


@dataclass(frozen=True)
class EdgeRecordRow:
    """A ``records`` backing (prefixed wire type ``_httk_records``) as an edge target."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="p3_edge_record")

    name: str
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class EdgeRecordFamily:
    """The records family targeted by run provenance edges."""

    type = "records"
    definition_id = RECORDS_DEFINITION_ID


register_entry_family(
    name="p3-edge-records", family=f"{__name__}:EdgeRecordFamily", definition_id=RECORDS_DEFINITION_ID
)
register_entry_record(name="p3-edge-records-rec", family="p3-edge-records", record=f"{__name__}:EdgeRecordRow")


def test_run_provenance_edges_served_forward_and_reverse_end_to_end() -> None:
    """A stored run's provenance edges serve as forward and reverse relationships.

    Pins the P3 serving edge: ``_httk_has_*`` blocks on ``/_httk_runs`` and the
    derived ``_httk_is_*`` blocks on the targeted ``_httk_records`` entries, with
    identical identifier type/id, ``meta.role`` and ``meta._httk_label`` both
    directions, plus ``include`` inlining of the edge targets.
    """
    with Backend.sqlite() as database:
        store = SqlStore(
            database,
            entry_records={RunEntry: (Run,), EdgeRecordFamily: (EdgeRecordRow,)},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        rec_in = store.fetch(EdgeRecordRow, store.save(EdgeRecordRow("in")), eager=True)
        rec_art = store.fetch(EdgeRecordRow, store.save(EdgeRecordRow("art")), eager=True)
        run = store.fetch(
            Run,
            store.save(
                Run(
                    inputs=(RunEdge("in-rec", "records", rec_in.id),),
                    artifacts=(RunEdge("art-rec", "records", rec_art.id),),
                    source_id="ws:job",
                )
            ),
            eager=True,
        )
        adapter = adapter_from_stores(
            (StoredEntrySource(store, RunEntry, "runs"), StoredEntrySource(store, EdgeRecordFamily, "recs")),
        )

        with _client(adapter) as client:
            # (a) forward: the run carries wire-named _httk_has_* blocks, whose
            # identifiers name the prefixed target type and edge label/role.
            run_resource = client.get("/_httk_runs").json()["data"][0]
            has_input = run_resource["relationships"]["_httk_has_input"]["data"]
            assert [(d["type"], d["id"]) for d in has_input] == [("_httk_records", rec_in.id)]
            assert has_input[0]["meta"] == {"role": "input", "_httk_label": "in-rec"}
            has_artifact = run_resource["relationships"]["_httk_has_artifact"]["data"]
            assert [(d["type"], d["id"]) for d in has_artifact] == [("_httk_records", rec_art.id)]
            assert has_artifact[0]["meta"]["role"] == "artifact"

            # (b) reverse: each targeted record carries the derived _httk_is_*
            # block naming the run, with the SAME role and label.
            records = {item["id"]: item for item in client.get("/_httk_records").json()["data"]}
            is_input = records[rec_in.id]["relationships"]["_httk_is_input"]["data"]
            assert [(d["type"], d["id"]) for d in is_input] == [("_httk_runs", run.id)]
            assert is_input[0]["meta"] == {"role": "input", "_httk_label": "in-rec"}
            is_artifact = records[rec_art.id]["relationships"]["_httk_is_artifact"]["data"]
            assert [(d["type"], d["id"]) for d in is_artifact] == [("_httk_runs", run.id)]
            assert is_artifact[0]["meta"]["role"] == "artifact"
            assert (
                "relationships" not in records[rec_in.id]
                or "_httk_is_artifact" not in records[rec_in.id]["relationships"]
            )

            # (c) include inlines the edge targets both directions.
            forward_included = client.get("/_httk_runs", params={"include": "_httk_records"}).json().get("included", [])
            assert {(i["type"], i["id"]) for i in forward_included} >= {
                ("_httk_records", rec_in.id),
                ("_httk_records", rec_art.id),
            }
            reverse_included = client.get("/_httk_records", params={"include": "_httk_runs"}).json().get("included", [])
            assert ("_httk_runs", run.id) in {(i["type"], i["id"]) for i in reverse_included}
