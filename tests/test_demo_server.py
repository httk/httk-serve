"""End-to-end tests over the demo server's in-memory backend.

These exercise the real demo adapter (``examples/optimade/demo_server/serve.py`` +
``inmemory_backend.py``), which evaluates filter predicates over dict rows, so
they assert actual row inclusion/exclusion rather than just translation trees.
"""

import os
import sys

from httk.serve.optimade.backend import execute_query
from httk.serve.optimade.filter import parse_optimade_filter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "examples/optimade", "demo_server"))

import serve  # noqa: E402


def structure_ids(filter_string: str) -> list[str]:
    adapter = serve.make_adapter()
    ast = parse_optimade_filter(filter_string)
    results = execute_query(
        adapter,
        ["structures"],
        ["id", "nsites", "chemical_formula_reduced", "last_modified"],
        [],
        100,
        0,
        ast,
    )
    return [row.values["id"] for row in results]


def test_nsites_not_equal_excludes_three_site_structure() -> None:
    # demo-3 (SiO2) is the only three-site structure and must be excluded.
    ids = structure_ids('nsites != 3')
    assert "demo-3" not in ids
    assert {"demo-1", "demo-2", "demo-4", "demo-5"} <= set(ids)


def test_nsites_equal_selects_three_site_structure() -> None:
    assert structure_ids('nsites = 3') == ["demo-3"]


def test_chemical_formula_reduced_ends_with_matches_computed_value() -> None:
    # SiO2 -> spec-compliant reduced formula "O2Si"; the filter must match the
    # computed column, not the raw descriptive "SiO2".
    assert structure_ids('chemical_formula_reduced ENDS WITH "O2Si"') == ["demo-3"]
    assert structure_ids('chemical_formula_reduced = "O2Si"') == ["demo-3"]


def test_chemical_formula_anonymous_matches_computed_value() -> None:
    # SiO2 -> anonymous "A2B" (proportions 2,1 descending).
    assert structure_ids('chemical_formula_anonymous = "A2B"') == ["demo-3"]


def test_last_modified_comparison_filters_without_501() -> None:
    # demo-3 is dated 2019; everything else is 2020 or later.
    ids = structure_ids('last_modified >= "2021-01-01T00:00:00Z"')
    assert "demo-3" not in ids
    assert {"demo-1", "demo-2", "demo-4"} <= set(ids)


def test_relationship_property_filter_on_references() -> None:
    # Depth-1 relationship-property filtering over the demo's declared
    # structure -> reference relationships, via the auto related-property
    # resolver (no hand-wired handler beyond 'references.id').
    assert structure_ids('references.doi ENDS WITH "2019.7"') == ["demo-3"]
    assert structure_ids('references.doi CONTAINS "10.1234"') == ["demo-1", "demo-3"]


def test_elements_has_filter_matches() -> None:
    # The demo's own advertised example filter; regression for the handler
    # tables being built from real property fulltypes (list -> HAS handler).
    assert structure_ids('elements HAS "Ga"') == ["demo-1", "demo-4"]


def test_not_has_family_is_the_exact_complement() -> None:
    # The demo's element lists: demo-1 [Ga,Ti], demo-2 [Si], demo-3 [O,Si],
    # demo-4 [As,Ga], demo-5 [Cl,Na]. NOT now goes through the plain set
    # expression (the has_inv_* forms are gone), and must still return exactly
    # the complement of the un-negated filter for every member of the family.
    for filter_string, matching in [
        ('elements HAS ANY "Ga","Ti"', ["demo-1", "demo-4"]),
        ('elements HAS ALL "Ga","Ti"', ["demo-1"]),
        ('elements HAS ONLY "Ga","Ti"', ["demo-1"]),
    ]:
        assert structure_ids(filter_string) == matching, filter_string
        negated = structure_ids("NOT " + filter_string)
        assert negated == [i for i in ["demo-1", "demo-2", "demo-3", "demo-4", "demo-5"] if i not in matching]


def test_last_modified_filter_available_on_all_entry_types() -> None:
    # A last_modified handler must exist for every entry type (no 501).
    adapter = serve.make_adapter()
    ast = parse_optimade_filter('last_modified >= "2000-01-01T00:00:00Z"')
    for entry in ("structures", "calculations", "references", "files", "trajectories"):
        results = execute_query(adapter, [entry], ["id", "last_modified"], [], 100, 0, ast)
        assert len(list(results)) >= 1
