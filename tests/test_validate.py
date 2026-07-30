import pytest
from materials_fixtures import materials_schema

from httk.optimade.engine.validate import (
    determine_optimade_version,
    validate_optimade_request,
)
from httk.optimade.model import OptimadeError, RawRequest

SCHEMA = materials_schema()


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


def test_versions_endpoint_only_unversioned() -> None:
    validated = validate_optimade_request(make_request("/versions"), "1.3.0", SCHEMA)
    assert validated.endpoint == "versions"
    with pytest.raises(OptimadeError) as excinfo:
        validate_optimade_request(make_request("/v1/versions"), "1.3.0", SCHEMA)
    assert excinfo.value.response_code == 404


def test_page_limit_clamped() -> None:
    validated = validate_optimade_request(make_request("/structures?page_limit=1000"), "1.3.0", SCHEMA)
    assert validated.query.page_limit == 50


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
