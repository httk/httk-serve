import pytest
from definition_fixtures import structures_definition
from materials_fixtures import materials_schema

from httk.serve.optimade.engine.validate import (
    determine_optimade_version,
    validate_optimade_request,
)
from httk.serve.optimade.model import OptimadeError, RawRequest
from httk.serve.optimade.schema.served import build_served_schema

SCHEMA = materials_schema()
REVISION_SCHEMA = build_served_schema({"structures": structures_definition()}, revisions=("structures",))
ALT_SCHEMA = build_served_schema({"structures": structures_definition()}, alternatives=("structures",))


def make_request(representation: str, **kwargs: object) -> RawRequest:
    return RawRequest(baseurl="http://localhost/", representation=representation, **kwargs)  # type: ignore[arg-type]


def test_base_endpoint() -> None:
    validated = validate_optimade_request(make_request("/"), "1.3.0", SCHEMA)
    assert validated.endpoint == ''
    assert validated.request_id is None
    assert validated.version == "1.3.0"


def test_fixed_endpoints() -> None:
    for endpoint in ("info", "links", "structures", "calculations", "info/structures"):
        validated = validate_optimade_request(make_request("/" + endpoint), "1.3.0", SCHEMA)
        assert validated.endpoint == endpoint


def test_versioned_endpoints() -> None:
    for prefix in ("v1", "v1.3", "v1.3.0"):
        validated = validate_optimade_request(make_request("/" + prefix + "/info"), "1.3.0", SCHEMA)
        assert validated.endpoint == "info"
        assert validated.url_version == prefix
        assert validated.version == "1.3.0"


def test_unsupported_version() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/v9/info"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 553


def test_unknown_endpoint() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/nosuch"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 404


def test_entry_endpoint_with_id() -> None:
    validated = validate_optimade_request(make_request("/structures/abc-123"), "1.3.0", SCHEMA)
    assert validated.endpoint == "structures"
    assert validated.request_id == "abc-123"


def test_entry_endpoint_with_bad_id() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures/\x01bad"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


@pytest.mark.parametrize(
    ("path", "request_id", "immutable_id", "endpoint_path"),
    (
        ("/structures/httk.test-1-1/_httk_revs", "httk.test-1-1", None, "structures/httk.test-1-1/_httk_revs"),
        ("/structures/httk.test-1-1/_httk_revs/", "httk.test-1-1", None, "structures/httk.test-1-1/_httk_revs"),
        (
            "/structures/httk.test-1-1/_httk_revs/3",
            "httk.test-1-1",
            "httk.test-1-1~3",
            "structures/httk.test-1-1/_httk_revs",
        ),
        ("/_httk_structures~revs", None, None, None),
        ("/_httk_structures~revs/httk.test-1-1~3", None, "httk.test-1-1~3", None),
    ),
)
def test_revision_paths(path: str, request_id: str | None, immutable_id: str | None, endpoint_path: str | None) -> None:
    validated = validate_optimade_request(make_request(path), "1.3.0", REVISION_SCHEMA)
    assert validated.endpoint == "_httk_structures~revs"
    assert validated.revisions is True
    assert validated.request_id == request_id
    assert validated.request_immutable_id == immutable_id
    assert validated.endpoint_path == endpoint_path


@pytest.mark.parametrize(
    "path",
    (
        "/structures/httk.test-1-1/_httk_revs/0",
        "/structures/httk.test-1-1/_httk_revs/07",
        "/structures/httk.test-1-1/_httk_revs/abc",
        "/widgets/httk.test-1-1/_httk_revs",
        "/structures/httk.test-1-1/_httk_revs",
        "/_httk_structures~revs",
    ),
)
def test_invalid_or_unsupported_revision_paths_are_404(path: str) -> None:
    schema = REVISION_SCHEMA if path.endswith(("/0", "/07", "/abc")) else SCHEMA
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request(path), "1.3.0", schema)
    assert excinfo.value.response_code == 404


@pytest.mark.parametrize(
    ("path", "request_id", "immutable_id", "endpoint_path"),
    (
        ("/structures/httk.test-1-1/_httk_alts", "httk.test-1-1", None, "structures/httk.test-1-1/_httk_alts"),
        ("/structures/httk.test-1-1/_httk_alts/", "httk.test-1-1", None, "structures/httk.test-1-1/_httk_alts"),
        (
            "/structures/httk.test-1-1/_httk_alts/conventional",
            "httk.test-1-1",
            "httk.test-1-1~conventional",
            "structures/httk.test-1-1/_httk_alts",
        ),
        ("/_httk_structures~alts", None, None, None),
        ("/_httk_structures~alts/httk.test-1-1~conventional", None, "httk.test-1-1~conventional", None),
    ),
)
def test_alternative_paths(
    path: str, request_id: str | None, immutable_id: str | None, endpoint_path: str | None
) -> None:
    validated = validate_optimade_request(make_request(path), "1.3.0", ALT_SCHEMA)
    assert validated.endpoint == "_httk_structures~alts"
    assert validated.alternatives is True
    assert validated.revisions is False
    assert validated.request_id == request_id
    assert validated.request_immutable_id == immutable_id
    assert validated.endpoint_path == endpoint_path


@pytest.mark.parametrize(
    ("path", "on_alt_schema"),
    (
        ("/structures/httk.test-1-1/_httk_alts/Conventional", True),
        ("/structures/httk.test-1-1/_httk_alts/0", True),
        ("/structures/httk.test-1-1/_httk_alts/2primitive", True),
        ("/widgets/httk.test-1-1/_httk_alts", True),
        ("/structures/httk.test-1-1/_httk_alts", False),
        ("/_httk_structures~alts", False),
    ),
)
def test_invalid_or_unsupported_alternative_paths_are_404(path: str, on_alt_schema: bool) -> None:
    schema = ALT_SCHEMA if on_alt_schema else SCHEMA
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request(path), "1.3.0", schema)
    assert excinfo.value.response_code == 404


def test_base_endpoint_accepts_composite_id_as_plain_single() -> None:
    # A composite id on the base endpoint is a valid single-entry request at the
    # validation layer; it is the store defaults that serve mains only, so the
    # composite simply resolves to no main downstream (data: null).
    validated = validate_optimade_request(make_request("/structures/httk.test-1-1~conventional"), "1.3.0", ALT_SCHEMA)
    assert validated.endpoint == "structures"
    assert validated.alternatives is False
    assert validated.request_id == "httk.test-1-1~conventional"


def test_versions_endpoint_only_unversioned() -> None:
    validated = validate_optimade_request(make_request("/versions"), "1.3.0", SCHEMA)
    assert validated.endpoint == "versions"
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/v1/versions"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 404


def test_page_limit_at_max_is_accepted() -> None:
    validated = validate_optimade_request(make_request("/structures?page_limit=50"), "1.3.0", SCHEMA)
    assert validated.query.page_limit == 50


def test_page_limit_over_max_is_forbidden() -> None:
    # OPTIMADE mandates 403 for an oversized page_limit, not a silent clamp.
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?page_limit=51"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 403


def test_page_limit_max_is_configurable() -> None:
    validated = validate_optimade_request(
        make_request("/structures?page_limit=500"), "1.3.0", SCHEMA, page_limit_max=500
    )
    assert validated.query.page_limit == 500
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?page_limit=501"), "1.3.0", SCHEMA, page_limit_max=500)
    assert excinfo.value.response_code == 403


def test_negative_page_offset_clamped() -> None:
    validated = validate_optimade_request(make_request("/structures?page_offset=-5"), "1.3.0", SCHEMA)
    assert validated.query.page_offset == 0


def test_bad_page_limit() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?page_limit=x"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


def test_bad_response_format() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?response_format=xml"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


@pytest.mark.parametrize("value", ("not-an-integer", "-1", "+42", " 42", "42_0", "٤٢", "042"))
def test_bad_as_of(value: str) -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request(f"/structures?_httk_as_of={value}"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


def test_as_of_is_validated_and_emitted() -> None:
    validated = validate_optimade_request(make_request("/structures?_httk_as_of=42"), "1.3.0", SCHEMA)
    assert validated.query.as_of == 42
    assert validated.query.as_query_dict()["_httk_as_of"] == "42"


def test_response_fields_recognized() -> None:
    validated = validate_optimade_request(
        make_request("/structures?response_fields=elements,nelements"), "1.3.0", SCHEMA
    )
    assert "elements" in validated.recognized_response_fields
    assert "nelements" in validated.recognized_response_fields
    # Required fields are always included:
    assert "id" in validated.recognized_response_fields
    assert "type" in validated.recognized_response_fields
    assert validated.unrecognized_response_fields == []


def test_response_fields_unknown_spec_field() -> None:
    validated = validate_optimade_request(make_request("/structures?response_fields=species"), "1.3.0", SCHEMA)
    assert "species" in validated.unrecognized_response_fields
    assert "species" not in validated.recognized_response_fields


def test_response_fields_unrecognized_raises() -> None:
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?response_fields=nosuchfield"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400


def test_response_fields_unknown_prefix_passed_through() -> None:
    validated = validate_optimade_request(make_request("/structures?response_fields=_other_prop"), "1.3.0", SCHEMA)
    assert "_other_prop" in validated.unrecognized_response_fields


def test_default_response_fields_used_without_query() -> None:
    validated = validate_optimade_request(make_request("/structures"), "1.3.0", SCHEMA)
    assert "elements" in validated.recognized_response_fields
    assert "id" in validated.recognized_response_fields


def test_defaults_not_mutated_by_required_fields() -> None:
    before = list(SCHEMA.default_response_fields["structures"])
    validate_optimade_request(make_request("/structures"), "1.3.0", SCHEMA)
    assert list(SCHEMA.default_response_fields["structures"]) == before


def test_filter_passed_through() -> None:
    validated = validate_optimade_request(make_request("/structures?filter=nelements=3"), "1.3.0", SCHEMA)
    assert validated.query.filter == "nelements=3"


# Regression tests for bugs fixed relative to the httk v1 implementation:


def test_querystr_used_when_no_query_dict() -> None:
    # v1 read query['querystr'] before query was assigned (NameError/KeyError)
    request = make_request("/structures", querystr="page_limit=10")
    validated = validate_optimade_request(request, "1.3.0", SCHEMA)
    assert validated.query.page_limit == 10


def test_query_derived_from_representation() -> None:
    # v1 subscripted the urlparse() result, which raises TypeError
    request = RawRequest(baseurl="http://localhost/", representation="/structures?page_limit=7", relurl="/structures")
    validated = validate_optimade_request(request, "1.3.0", SCHEMA)
    assert validated.query.page_limit == 7


def test_caller_supplied_endpoint() -> None:
    # v1 hit an unbound local variable when the caller supplied the endpoint directly
    request = make_request("/structures?response_fields=elements", endpoint="structures")
    validated = validate_optimade_request(request, "1.3.0", SCHEMA)
    assert validated.endpoint == "structures"
    assert "elements" in validated.recognized_response_fields


@pytest.mark.parametrize("url_alias", ("v1", "v1.3", "v1.3.0"))
def test_caller_supplied_endpoint_preserves_url_version(url_alias: str) -> None:
    request = RawRequest(
        baseurl="http://localhost/outer/v1/",
        representation=f"/{url_alias}/structures",
        endpoint="structures",
    )
    validated = validate_optimade_request(request, "1.3.0", SCHEMA)

    assert validated.url_version == url_alias
    assert validated.baseurl == f"http://localhost/outer/v1/{url_alias}/"


def test_caller_supplied_endpoint_still_rejects_unsupported_url_version() -> None:
    request = make_request("/v9/structures", endpoint="structures")
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(request, "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 553


def test_determine_optimade_version() -> None:
    assert determine_optimade_version(make_request("/structures")) == "1.3.0"
    assert determine_optimade_version(make_request("/v1/structures")) == "1.3.0"
    assert determine_optimade_version(make_request("/v1.3/structures")) == "1.3.0"
    assert determine_optimade_version(make_request("/")) == "1.3.0"
    with pytest.raises(OptimadeError) as excinfo:
        determine_optimade_version(make_request("/v2/structures"))
    assert excinfo.value.response_code == 553


def test_determine_optimade_version_two_char_prefix() -> None:
    # v1 required len > 2 in determine_optimade_version but >= 2 in validation;
    # the port treats /v1 consistently in both.
    assert determine_optimade_version(make_request("/v1/info")) == "1.3.0"


def test_negative_page_limit_rejected() -> None:
    # A negative page_limit used to flow into the execution layer, where
    # negative limits mean "no bound" and bypassed the page-size cap entirely.
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/structures?page_limit=-2"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 400
