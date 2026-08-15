"""Pin the hand-written DSP code against the bundled OpenAPI contract.

`httk.serve.dsp` writes a few facts in Python that the bundled contract
(``src/httk/serve/dsp/schemas/openapi.yaml``) also declares: which DSP error
document ``@type`` an operation produces, which media types the catalogue
policy can select, and which ``(kind, status)`` pairs a protocol error can
carry. An earlier design proposed replacing those with framework
abstractions; that was rejected because DSP is currently the only consumer.
Instead, the hand-written code stays and these tests make it verifiable: they
fail if the code and the contract are ever allowed to drift apart.
"""

import ast
from pathlib import Path

import pytest
from test_dsp_config import config

from httk.serve.dsp import DCAT_MEDIA_TYPE, DspProtocolError, MinimalDspCataloguePolicy
from httk.serve.dsp import provider as provider_module
from httk.serve.dsp.api import _error_kind
from httk.serve.dsp.validation import dsp_contract


def _schema_kind(schema_id: str) -> str:
    """Derive the DSP area implied by a bundled error schema's ``$id``.

    Every bundled DSP error schema is published under a path segment naming
    its area, e.g. ``.../catalog/catalog-error-schema.json``. That segment is
    exactly one of the ``ErrorKind`` values used throughout the DSP code.
    """
    return schema_id.split("/")[-2]


def test_error_kind_agrees_with_every_contract_declared_error_schema() -> None:
    """`_error_kind` must classify every operation as the contract's declared error kind."""
    contract = dsp_contract()
    schema_kinds: set[str] = set()
    assertions = 0
    for operation in contract.operations:
        for status, contracts in operation.responses.items():
            if status < 400:
                continue
            for _media_type, schema_id in contracts:
                if schema_id is None:
                    continue
                kind = _schema_kind(schema_id)
                schema_kinds.add(kind)
                assert _error_kind(operation.operation_id) == kind, (
                    f"{operation.operation_id} status {status} declares a {kind!r} error schema "
                    f"but _error_kind returns {_error_kind(operation.operation_id)!r}"
                )
                assertions += 1
    # Exhaustiveness: the contract must not reference an error schema kind that this
    # test never checked. If a fourth kind is ever added, this assertion breaks and
    # forces the mapping (and this test) to be extended, rather than silently
    # covering only a subset of the kinds that exist.
    assert schema_kinds == {"catalog", "negotiation", "transfer"}
    assert assertions == 27  # non-trivial and exact: pins the contract's declared error-status count too

    # Known, deliberate hole: version_discovery declares NO error response at all, so
    # the loop above contributes zero assertions for it. _error_kind("version_discovery")
    # still returns "transfer" by fall-through (it matches neither the "catalog" nor the
    # "negotiation" prefixes/exceptions in _error_kind). That mismatch is unreachable
    # today because GET /.well-known/dspace-version takes no body and no parameters, so
    # the adapter can never raise an OpenAPIRequestError for it and _schema_error() is
    # never called with operation_id="version_discovery". Pinned deliberately so this
    # known-but-unreachable inconsistency cannot silently grow reachable without this
    # test forcing a decision.
    version_discovery = contract.operation("version_discovery")
    declared_error_statuses = [status for status in version_discovery.responses if status >= 400]
    assert declared_error_statuses == []
    assert _error_kind("version_discovery") == "transfer"


def test_catalogue_policy_media_types_and_406_are_declared_by_the_contract() -> None:
    """Every media type `select_catalogue_representation` can return is declared, and so is 406."""
    contract = dsp_contract()
    operation = contract.operation("catalog_request")
    policy = MinimalDspCataloguePolicy()
    provider_config = config(dcat_ap_content_negotiation=True)

    plain = policy.select_catalogue_representation(provider_config, "application/json")
    alternate = policy.select_catalogue_representation(provider_config, DCAT_MEDIA_TYPE)
    assert plain.media_type == "application/json"
    assert alternate.media_type == DCAT_MEDIA_TYPE

    declared_media_types = {media_type for media_type, _schema_id in operation.success_contracts}
    assert plain.media_type in declared_media_types
    assert alternate.media_type in declared_media_types

    with pytest.raises(DspProtocolError):
        policy.select_catalogue_representation(config(), DCAT_MEDIA_TYPE)
    assert operation.response_contracts(406) != ()


def _protocol_error_pairs(source: str) -> tuple[list[tuple[str, int]], int]:
    """Statically collect literal ``(kind, status)`` pairs from ``DspProtocolError(...)`` calls.

    :param source: Python source to scan.
    :return: Collected literal pairs, and a count of calls skipped because
        their first two positional arguments were not both literal constants.
    """
    tree = ast.parse(source)
    pairs: list[tuple[str, int]] = []
    skipped = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name):
            name = func.id
        elif isinstance(func, ast.Attribute):
            name = func.attr
        else:
            continue
        if name != "DspProtocolError":
            continue
        args = node.args
        if (
            len(args) < 2
            or not isinstance(args[0], ast.Constant)
            or not isinstance(args[0].value, str)
            or not isinstance(args[1], ast.Constant)
            or not isinstance(args[1].value, int)
        ):
            skipped += 1
            continue
        pairs.append((args[0].value, args[1].value))
    return pairs, skipped


def test_every_raisable_protocol_error_pair_is_declared_except_the_known_409_gap() -> None:
    """Every statically-literal `DspProtocolError(kind, status, ...)` pair from provider.py
    must be declared by the contract, except a known, currently-unreachable 409 gap.
    """
    source = Path(provider_module.__file__).read_text(encoding="utf-8")
    pairs, skipped = _protocol_error_pairs(source)
    assert pairs, "expected to find at least one literal (kind, status) DspProtocolError construction"
    assert skipped > 0, (
        "expected some DspProtocolError(...) calls in provider.py to use a non-literal "
        "kind (e.g. a `kind` variable), confirming the ast scan is exercising the skip path"
    )

    contract = dsp_contract()
    declared: set[tuple[str, int]] = set()
    for operation in contract.operations:
        for status, contracts in operation.responses.items():
            if status < 400:
                continue
            for _media_type, schema_id in contracts:
                if schema_id is None:
                    continue
                declared.add((_schema_kind(schema_id), status))

    undeclared = {pair for pair in pairs if pair not in declared}
    # Known, currently-unreachable gap: provider.py raises DspProtocolError(..., 409, ...)
    # ("callback transition was superseded") in two places, on the automatic-callback
    # delivery paths for negotiation and transfer. 409 is declared nowhere in the
    # contract. It cannot reach an HTTP response today because _run_automatic swallows
    # the exception on those background callback paths. Pinned exactly so a new
    # undeclared pair introduced elsewhere cannot slip in silently: if this set grows,
    # the test fails and forces a decision (declare the status, or make it truly
    # unreachable and remove the gap).
    assert undeclared == {("negotiation", 409), ("transfer", 409)}
