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

import httk.serve.dsp as dsp_package
from httk.serve.dsp import DCAT_MEDIA_TYPE, DspProtocolError, MinimalDspCataloguePolicy
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


def test_success_status_pins_the_dsp_mandated_201_and_200_statuses() -> None:
    """DSP mandates ``201 Created`` for the two process-creation operations.

    ``OpenAPIOperation.success_status`` is the only thing that checks this
    against the contract, so pin one explicit operation-to-status mapping. A
    count plus a value set would miss one operation being swapped for another,
    and a mapping derived from the contract's own keys could not detect an
    operation being added or removed at all.
    """
    contract = dsp_contract()
    statuses = {operation.operation_id: operation.success_status for operation in contract.operations}
    assert statuses == {
        "version_discovery": 200,
        "catalog_request": 200,
        "dataset_request": 200,
        "negotiation_state": 200,
        "negotiation_request": 201,
        "negotiation_counter_request": 200,
        "negotiation_event": 200,
        "agreement_verification": 200,
        "negotiation_termination": 200,
        "transfer_state": 200,
        "transfer_request": 201,
        "transfer_start": 200,
        "transfer_suspension": 200,
        "transfer_completion": 200,
        "transfer_termination": 200,
    }


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


def _dsp_package_dir() -> Path:
    """Return the directory holding the ``httk.serve.dsp`` package's Python source."""
    return Path(dsp_package.__file__).parent


def _literal_argument[T](node: ast.Call, position: int, keyword: str, kind: type[T]) -> T | None:
    """Read one call argument positionally or by keyword when it is a literal.

    :param node: The call being inspected.
    :param position: Positional index the argument may occupy.
    :param keyword: Keyword name the argument may be passed under instead.
    :param kind: Python type the literal must have.
    :return: The literal value, or ``None`` when it is absent or not a literal.
    """
    if len(node.args) > position:
        argument = node.args[position]
    else:
        argument = next((item.value for item in node.keywords if item.arg == keyword), None)
    if isinstance(argument, ast.Constant) and isinstance(argument.value, kind):
        return argument.value
    return None


def _protocol_error_call_sites(source: str) -> tuple[list[tuple[str, int]], list[int], int]:
    """Statically classify ``DspProtocolError(...)`` calls in one module.

    A call is fully literal when both its ``kind`` and ``status_code``
    arguments are literal constants. Many call sites pass a *runtime* ``kind``
    variable (the enclosing method's own error kind) alongside a *literal*
    status -- those are still checkable against the union of statuses the
    contract declares anywhere, even though the specific kind is unknown
    statically. Only a call whose status itself cannot be read statically is
    genuinely unchecked.

    Both arguments are read positionally or by keyword, so a call written as
    ``DspProtocolError(kind="catalog", status_code=400, ...)`` is checked
    exactly like the positional form rather than counted as unchecked.

    :param source: Python source to scan.
    :return: Fully literal ``(kind, status)`` pairs; literal statuses from
        calls whose ``kind`` argument was not a literal string; and a count of
        calls where even the status could not be determined statically.
    """
    tree = ast.parse(source)
    pairs: list[tuple[str, int]] = []
    status_only: list[int] = []
    fully_skipped = 0
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
        kind = _literal_argument(node, 0, "kind", str)
        status = _literal_argument(node, 1, "status_code", int)
        if status is None:
            fully_skipped += 1
        elif kind is None:
            status_only.append(status)
        else:
            pairs.append((kind, status))
    return pairs, status_only, fully_skipped


def test_every_raisable_protocol_error_pair_is_declared() -> None:
    """Every statically-checkable ``DspProtocolError(kind, status, ...)`` call site
    anywhere under ``httk.serve.dsp`` must be declared by the contract.

    Call sites with a literal ``kind`` and ``status`` are checked as an exact
    pair. Call sites with a runtime ``kind`` but a literal ``status`` (e.g.
    ``DspProtocolError(kind, 400, ...)``) are recovered by checking that the
    status alone is declared for *some* operation's error response, which is
    the strongest static claim their non-literal kind allows.
    """
    pairs: list[tuple[str, int]] = []
    status_only: list[int] = []
    fully_skipped = 0
    for path in sorted(_dsp_package_dir().rglob("*.py")):
        file_pairs, file_status_only, file_fully_skipped = _protocol_error_call_sites(path.read_text(encoding="utf-8"))
        pairs += file_pairs
        status_only += file_status_only
        fully_skipped += file_fully_skipped

    assert pairs, "expected to find at least one literal (kind, status) DspProtocolError construction"
    assert status_only, (
        "expected some DspProtocolError(...) calls under httk.serve.dsp to use a non-literal "
        "kind (e.g. a `kind` variable) alongside a literal status, confirming the status-only "
        "recovery path is exercised"
    )
    # Pinned exactly: today every DspProtocolError(...) call site under httk.serve.dsp
    # supplies at least a literal status, even where the kind is a runtime variable, so
    # nothing is currently fully unchecked. If a call site is ever added whose status is
    # ALSO computed at runtime, this assertion fails and forces a decision about how to
    # check it, rather than letting it silently join an unbounded, unchecked pile.
    assert fully_skipped == 0

    contract = dsp_contract()
    declared: set[tuple[str, int]] = set()
    declared_statuses: set[int] = set()
    for operation in contract.operations:
        for status, contracts in operation.responses.items():
            if status < 400:
                continue
            for _media_type, schema_id in contracts:
                if schema_id is None:
                    continue
                declared.add((_schema_kind(schema_id), status))
                declared_statuses.add(status)

    for status in status_only:
        assert status in declared_statuses, (
            f"status {status} from a DspProtocolError(kind, {status}, ...) call with a "
            f"non-literal kind is not declared as an error status by any contract operation"
        )

    undeclared = {pair for pair in pairs if pair not in declared}
    # Every statically-checkable DspProtocolError(kind, status, ...) construction under
    # httk.serve.dsp names a (kind, status) pair the contract declares as an error
    # response. There is no gap: the former "callback transition was superseded" race on
    # the automatic-callback delivery paths no longer raises a DspProtocolError at all --
    # it raises DspTransitionSuperseded, which carries no wire status precisely because,
    # by the time it fires, the peer callback has already been delivered and only the
    # local commit lost a race. catalogue.py's three literal call sites ((catalog, 400)
    # and twice (catalog, 406)) are all declared too. This set-equality assertion is what
    # keeps it that way: if any call site ever introduces a (kind, status) pair the
    # contract does not declare, undeclared grows non-empty and this test fails, forcing a
    # decision (declare the status in the contract, or stop modelling it as a protocol
    # error), rather than letting an undeclared status slip toward an OpenAPIContractError
    # at runtime.
    assert undeclared == set()
