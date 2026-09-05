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

import pytest
from httk.core import load_entry_type_definition
from httk.core.data_records import RECORDS_DEFINITION_ID, DataRecord, DataRecordEntry
from httk.core.provenance import RUNS_DEFINITION_ID, Run, RunEdge, RunEntry
from httk.core.register import register_entry_family, register_entry_record
from httk.core.storage import IdentitySkip, Indexed, StorageInfo, StrongLink, Unique, WeakLink
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


def test_prefixed_relationships_across_families_and_stores() -> None:
    with Backend.sqlite() as database_a, Backend.sqlite() as database_b:
        sources = []
        for namespace, database in (("A", database_a), ("B", database_b)):
            store = SqlStore(
                database,
                entry_records={
                    RunEntry: Run,
                    CalculationFamily: CalculationRecord,
                    ReferenceFamily: ReferenceRecord,
                },
                entry_ids=EntryIdScheme("httk.same", "1"),
            )
            reference = store.fetch(ReferenceRecord, store.save(ReferenceRecord("r")), eager=True)
            calculation = store.fetch(CalculationRecord, store.save(CalculationRecord("c")), eager=True)
            store.link(calculation, "produced_by", reference)
            run = store.fetch(
                Run,
                store.save(
                    Run(
                        inputs=(
                            RunEdge("ref", "references", reference.id),
                            RunEdge("calc", "calculations", calculation.id),
                            RunEdge("remote", "external", "loose-id"),
                        ),
                        source_id="job",
                    )
                ),
                eager=True,
            )
            sources.extend(
                (
                    StoredEntrySource(store, RunEntry, namespace + "run", namespace + "R:"),
                    StoredEntrySource(store, ReferenceFamily, namespace + "ref", namespace + "P:"),
                    StoredEntrySource(store, CalculationFamily, namespace + "calc", namespace + "C:"),
                )
            )
        adapter = adapter_from_stores(sources)
        with _client(adapter) as client:

            def payload(endpoint, **params):
                response = client.get(endpoint, params=params)
                assert response.status_code == 200, response.text
                return response.json()

            def ids(endpoint, expression):
                return {row["id"] for row in payload(endpoint, filter=expression)["data"]}

            rows = payload("/_httk_runs", include="references,calculations")
            assert {row["id"] for row in rows["included"]} == {
                namespace + kind + ":" + raw
                for namespace in ("A", "B")
                for kind, raw in (("P", reference.id), ("C", calculation.id))
            }
            for row in rows["data"]:
                namespace = row["id"][0]
                edge_ids = {item["id"] for item in row["relationships"]["_httk_has_input"]["data"]}
                assert edge_ids == {
                    namespace + "P:" + reference.id,
                    namespace + "C:" + calculation.id,
                    "loose-id",
                }
            refs = payload("/references", include="_httk_runs")
            assert {row["id"] for row in refs["included"]} == {
                "AR:" + run.id,
                "BR:" + run.id,
            }
            for row in refs["data"]:
                reverse = row["relationships"]["_httk_is_input"]["data"]
                assert reverse == [
                    {
                        "type": "_httk_runs",
                        "id": row["id"][0] + "R:" + run.id,
                        "meta": {"role": "input", "_httk_label": "ref"},
                    }
                ]
            calcs = payload("/calculations", include="references")
            for row in calcs["data"]:
                assert row["relationships"]["references"]["data"][0]["id"] == row["id"][0] + "P:" + reference.id
            forward = "_httk_relationships._httk_has_input.id"
            reverse = "_httk_relationships._httk_is_input.id"
            assert ids("/_httk_runs", f'{forward} HAS "AP:{reference.id}"') == {"AR:" + run.id}
            assert ids(
                "/_httk_runs",
                f'{forward} HAS ALL "AP:{reference.id}", "AC:{calculation.id}"',
            ) == {"AR:" + run.id}
            assert (
                ids(
                    "/_httk_runs",
                    f'{forward} HAS ALL "AP:{reference.id}", "BC:{calculation.id}"',
                )
                == set()
            )
            assert ids(
                "/_httk_runs",
                f'{forward} HAS ONLY "AP:{reference.id}", "AC:{calculation.id}", "loose-id"',
            ) == {"AR:" + run.id}
            assert ids("/_httk_runs", f'{forward} HAS "{reference.id}"') == set()
            assert ids("/references", f'{reverse} HAS "AR:{run.id}"') == {"AP:" + reference.id}
            assert ids("/references", f'{reverse} HAS "{run.id}"') == set()
            assert ids("/calculations", f'references.id HAS "AP:{reference.id}"') == {"AC:" + calculation.id}
            assert ids("/calculations", f'references.id HAS "BP:{reference.id}"') == {"BC:" + calculation.id}
            assert ids("/calculations", f'references.id HAS "{reference.id}"') == set()


def test_ambiguous_relationship_mounts_require_valid_explicit_selection() -> None:
    with Backend.sqlite() as database, Backend.sqlite() as other_database:
        store = SqlStore(
            database,
            entry_records={RunEntry: Run, EdgeRecordFamily: EdgeRecordRow},
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        other_store = SqlStore(other_database, entry_records={EdgeRecordFamily: EdgeRecordRow})
        target = store.fetch(EdgeRecordRow, store.save(EdgeRecordRow("r")), eager=True)
        run = store.fetch(
            Run,
            store.save(Run(inputs=(RunEdge("in", "records", target.id),))),
            eager=True,
        )
        mounts = (
            StoredEntrySource(store, EdgeRecordFamily, "a", "A:"),
            StoredEntrySource(store, EdgeRecordFamily, "b", "B:"),
        )
        with pytest.raises(ValueError, match="Ambiguous relationship target"):
            adapter_from_stores((StoredEntrySource(store, RunEntry, "run", "R:"), *mounts))
        for invalid in ("missing", "run", "other"):
            with pytest.raises(ValueError, match="Invalid relationship source"):
                adapter_from_stores(
                    (
                        StoredEntrySource(store, RunEntry, "run", "R:", {EdgeRecordFamily: invalid}),
                        *mounts,
                        StoredEntrySource(other_store, EdgeRecordFamily, "other", "O:"),
                    )
                )
        adapter = adapter_from_stores(
            (
                StoredEntrySource(store, RunEntry, "run", "R:", {EdgeRecordFamily: "b"}),
                *mounts,
            )
        )
        with _client(adapter) as client:
            rows = client.get("/_httk_records").json()["data"]
            by_id = {row["id"]: row for row in rows}
            assert "_httk_is_input" not in by_id["A:" + target.id].get("relationships", {})
            assert by_id["B:" + target.id]["relationships"]["_httk_is_input"]["data"][0]["id"] == "R:" + run.id
            result = client.get(
                "/_httk_records",
                params={"filter": f'_httk_relationships._httk_is_input.id HAS "R:{run.id}"'},
            )
            assert result.status_code == 200, result.text
            assert [row["id"] for row in result.json()["data"]] == ["B:" + target.id]
            forward = client.get("/_httk_runs", params={"include": "_httk_records"}).json()
            assert [row["id"] for row in forward["included"]] == ["B:" + target.id]


@dataclass(frozen=True)
class UnmountedRun:
    """A separate run family sharing the mounted run family's internal type."""

    __httk_storage__: ClassVar[StorageInfo] = StorageInfo(storage_name="unmounted_edge_run")

    inputs: Annotated[tuple[RunEdge, ...], StrongLink("has_input", reverse="is_input", role="input")] = ()
    id: Annotated[str | None, IdentitySkip(), Indexed()] = field(default=None, compare=False)
    immutable_id: Annotated[str | None, IdentitySkip(), Unique()] = field(default=None, compare=False)


class UnmountedRunFamily:
    """An unmounted sibling of the core runs family."""

    type = "runs"
    definition_id = RUNS_DEFINITION_ID


register_entry_family(
    name="unmounted-edge-run", family=f"{__name__}:UnmountedRunFamily", definition_id=RUNS_DEFINITION_ID
)
register_entry_record(name="unmounted-edge-run-rec", family="unmounted-edge-run", record=f"{__name__}:UnmountedRun")


@pytest.mark.parametrize("reverse_layout", (False, True))
def test_unmounted_same_type_families_preserve_relationship_prefixes(reverse_layout: bool) -> None:
    """Same-type siblings cannot steal forward prefixes or inherit reverse prefixes."""
    with Backend.sqlite() as database:
        records = [
            (RunEntry, Run),
            (UnmountedRunFamily, UnmountedRun),
            (EdgeRecordFamily, EdgeRecordRow),
            (DataRecordEntry, DataRecord),
        ]
        store = SqlStore(
            database,
            entry_records=dict(reversed(records) if reverse_layout else records),
            entry_ids=EntryIdScheme("httk.test", "1"),
        )
        target = store.fetch(EdgeRecordRow, store.save(EdgeRecordRow("target")), eager=True)
        edge = RunEdge("in", "records", target.id)
        mounted = store.fetch(Run, store.save(Run(inputs=(edge,))), eager=True)
        store.save(UnmountedRun())
        unmounted = store.fetch(UnmountedRun, store.save(UnmountedRun(inputs=(edge,))), eager=True)
        adapter = adapter_from_stores(
            (
                StoredEntrySource(store, RunEntry, "runs", "R:"),
                StoredEntrySource(store, EdgeRecordFamily, "records", "D:"),
            )
        )
        with _client(adapter) as client:
            forward = client.get("/_httk_runs", params={"include": "_httk_records"})
            assert forward.status_code == 200, forward.text
            payload = forward.json()
            assert [row["id"] for row in payload["included"]] == ["D:" + target.id]
            assert payload["data"][0]["relationships"]["_httk_has_input"]["data"][0]["id"] == "D:" + target.id
            reverse = client.get("/_httk_records", params={"include": "_httk_runs"})
            assert reverse.status_code == 200, reverse.text
            payload = reverse.json()
            assert {row["id"] for row in payload["included"]} == {"R:" + mounted.id}
            assert {row["id"] for row in payload["data"][0]["relationships"]["_httk_is_input"]["data"]} == {
                "R:" + mounted.id,
                unmounted.id,
            }
            for endpoint, key, value, expected in (
                ("/_httk_runs", "has_input", "D:" + target.id, {"R:" + mounted.id}),
                ("/_httk_runs", "has_input", target.id, set()),
                ("/_httk_records", "is_input", "R:" + mounted.id, {"D:" + target.id}),
                ("/_httk_records", "is_input", unmounted.id, {"D:" + target.id}),
                ("/_httk_records", "is_input", "R:" + unmounted.id, set()),
            ):
                response = client.get(endpoint, params={"filter": f'_httk_relationships._httk_{key}.id HAS "{value}"'})
                assert response.status_code == 200, response.text
                assert {row["id"] for row in response.json()["data"]} == expected
