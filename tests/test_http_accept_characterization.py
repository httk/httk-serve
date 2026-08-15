"""Characterization tests pinning the current HTTP ``Accept`` behaviour.

These are characterization tests. They pin the CURRENT, UNCHANGED behaviour of the
two independent RFC 9110 ``Accept``-header implementations that live in httk-serve,
exactly as the code behaves today, ahead of a later phase that will rewrite both
onto one shared parser. Their sole purpose is to make that refactor verifiable;
they pin behaviour as-is and do not endorse it, and they must never drive a change
to production code. If one of these tests ever fails, the correct response is to
decide whether the behaviour change was intended, not to "fix" the test blindly.

The two groups are EXPECTED to disagree on parameterised accept ranges:

* Group 1 -- ``httk.serve.http.api`` exercised through ``json_get_app`` /
  ``jsonld_get_app`` -- KEEPS an accept range that carries parameters and requires
  each of those parameters to be present and equal in the response media type
  (``api.py`` lines 173-174).
* Group 2 -- ``httk.serve.dsp.catalogue`` exercised through
  ``MinimalDspCataloguePolicy.select_catalogue_representation`` -- DISCARDS any
  accept range that carries any parameter at all (``catalogue.py`` ``_range_quality``,
  the ``or item.parameters`` clause).

Consequence: a request range such as ``application/json;charset=utf-8`` can be
acceptable under Group 1 (when the response media type carries the same parameter)
but is never acceptable under Group 2. The two anchor tests below each name the
other side and state that the divergence is deliberate.

Group 1 is exercised only through the public app surface (never through the
private ``_``-prefixed helpers) so the tests survive those helpers being moved.
Group 2 reaches every pinned behaviour through the public
``select_catalogue_representation`` method, so no private import is needed there.
"""

import httpx
import pytest
from starlette.testclient import TestClient
from test_dsp_config import config

from httk.serve.dsp import (
    DCAT_MEDIA_TYPE,
    DspProtocolError,
    MinimalDspCataloguePolicy,
)
from httk.serve.http import json_get_app, jsonld_get_app

# ---------------------------------------------------------------------------
# Group 1 -- httk.serve.http.api semantics (public json_get_app / jsonld_get_app)
# ---------------------------------------------------------------------------


def _get(app: object, accept: str | None) -> httpx.Response:
    """Issue a GET, sending an explicit ``Accept`` only when one is given."""
    with TestClient(app) as client:  # type: ignore[arg-type]
        headers = {} if accept is None else {"Accept": accept}
        return client.get("/", headers=headers)


# Plain ``application/json`` response: header -> observed status code, pinned as-is.
_PLAIN_JSON_STATUS = (
    ("application/json", 200),
    ("*/*", 200),
    ("application/*", 200),
    ("", 200),  # empty Accept is treated as "accept anything"
    ("   ", 200),  # whitespace-only Accept likewise
    ("text/turtle", 406),
    ("text/*", 406),  # a wildcard in a non-matching major does not help
    ("application/json;q=0", 406),  # q=0 on the only matching range
    ("application/json;q=0.5", 200),
    ("application/json;q=1.0", 200),
    ("application/json;q=not-a-quality", 406),  # malformed q -> range discarded, none left
    ("application/xml, application/json", 200),  # one of several ranges matches
    # Pinned as-is (surprising): a parameterised range against a response that has
    # NO parameters is NOT acceptable, because api.py requires the parameter to be
    # present and equal in the response media type. Contrast with the charset
    # response below, where the same header IS accepted.
    ("application/json;charset=utf-8", 406),
)


@pytest.mark.parametrize(("accept", "status"), _PLAIN_JSON_STATUS)
def test_plain_json_app_accept_statuses(accept: str, status: int) -> None:
    assert _get(json_get_app({"value": 1}), accept).status_code == status


def test_plain_json_app_accepts_when_no_accept_header_is_sent() -> None:
    # Pin the "Accept header entirely absent -> accepted" branch. httpx injects a
    # default Accept, so the header is deleted before sending to reach the real
    # None path in api.py rather than the equivalent */* path.
    with TestClient(json_get_app({"value": 1})) as client:
        request = client.build_request("GET", "/")
        del request.headers["accept"]
        assert client.send(request).status_code == 200


# Specificity ordering: a more specific range wins over a wildcard regardless of
# order, so its q-value decides the outcome. Pinned against a plain response.
_SPECIFICITY_STATUS = (
    ("application/json;q=0, */*;q=1", 406),  # specific q=0 beats wildcard q=1
    ("application/json;q=0, application/*;q=1", 406),
    ("application/*;q=0, application/json;q=1", 200),  # specific q=1 beats wildcard q=0
    ("*/*;q=0, application/json;q=1", 200),
)


@pytest.mark.parametrize(("accept", "status"), _SPECIFICITY_STATUS)
def test_plain_json_app_specificity_ordering(accept: str, status: int) -> None:
    assert _get(json_get_app({"value": 1}), accept).status_code == status


# THE DIVERGENCE ANCHOR (Group 1 side). With a response media type that carries a
# parameter, api.py KEEPS a parameterised accept range and matches its parameters.
# The mirrored dsp/catalogue.py side (test_dsp_policy_parameterised_range_is_discarded)
# DISCARDS any parameterised range outright, so the same header is rejected there.
# The two behaviours differ deliberately and both must survive the later refactor.
_CHARSET_STATUS = (
    ("application/json;charset=utf-8", 200),  # ANCHOR: parameter present and equal -> accepted
    ("application/json;charset=iso-8859-1", 406),  # parameter value mismatch -> not acceptable
    ("application/json", 200),  # bare range imposes no parameter requirement
    ("application/json;charset=utf-8;q=0", 406),  # q=0 still forces 406
)


@pytest.mark.parametrize(("accept", "status"), _CHARSET_STATUS)
def test_http_api_keeps_and_matches_parameterised_accept_range(accept: str, status: int) -> None:
    app = json_get_app({"value": 1}, media_type="application/json; charset=utf-8")
    assert _get(app, accept).status_code == status


def test_http_api_quoted_string_parameters_are_not_split_on_comma_or_semicolon() -> None:
    # A comma or semicolon inside a quoted parameter value is not a list or
    # parameter separator, so the media type round-trips; a duplicated parameter
    # name is rejected as a broken range.
    media_type = 'application/json; profile="https://example.test/p;a,t"'
    app = json_get_app({"value": 1}, media_type=media_type)
    assert _get(app, media_type).status_code == 200
    duplicate = 'application/json;profile="bad";profile="https://example.test/p;a,t"'
    assert _get(app, duplicate).status_code == 406


def test_jsonld_app_profile_parameter_matching_and_link_header() -> None:
    profile_iri = "https://example.test/profiles/catalogue"
    protocol_iri = "https://example.test/protocols/public-get"
    app = jsonld_get_app(
        {"@context": {}},
        media_type=f'application/ld+json; profile="{profile_iri}"',
        profile=protocol_iri,
    )
    with TestClient(app) as client:
        accepted = client.get("/", headers={"Accept": "application/ld+json"})
        # The RFC 6906 Link header is emitted exactly as api.py currently formats it.
        assert accepted.status_code == 200
        assert accepted.headers["link"] == f'<{protocol_iri}>; rel="profile"'
        assert accepted.headers["content-type"] == f'application/ld+json; profile="{profile_iri}"'
        # A matching profile parameter is accepted; a mismatching one is not.
        matched = client.get("/", headers={"Accept": f'application/ld+json; profile="{profile_iri}"'})
        mismatched = client.get("/", headers={"Accept": 'application/ld+json; profile="other"'})
    assert matched.status_code == 200
    assert mismatched.status_code == 406


# ---------------------------------------------------------------------------
# Group 2 -- httk.serve.dsp.catalogue semantics (select_catalogue_representation)
# ---------------------------------------------------------------------------


def _policy() -> MinimalDspCataloguePolicy:
    return MinimalDspCataloguePolicy()


# Plain DSP default: no Accept, or an Accept that does not request the DCAT-AP
# profiled type, selects the plain "application/json" representation. Exercised
# without content negotiation, so no Vary header is attached.
_DSP_PLAIN = (
    None,
    "",
    "   ",
    "application/json",
    "*/*",
    "application/*",
    "application/json;q=0.5",
)


@pytest.mark.parametrize("accept", _DSP_PLAIN)
def test_dsp_policy_default_plain_representation(accept: str | None) -> None:
    representation = _policy().select_catalogue_representation(config(), accept)
    assert representation.media_type == "application/json"
    assert representation.alternate is False
    assert representation.headers == ()


def test_dsp_policy_selects_dcat_ap_alternate_with_content_negotiation() -> None:
    # An exact single range naming the DCAT-AP media type (or a bare
    # application/ld+json, whose parameters are the empty set) selects the alternate
    # DCAT-AP representation when content negotiation is enabled.
    provider_config = config(dcat_ap_content_negotiation=True)
    expected_headers = (
        ("Vary", "Accept"),
        ("Link", f'<{provider_config.dcat_ap_profile}>; rel="profile"'),
    )
    for accept in (DCAT_MEDIA_TYPE, "application/ld+json"):
        representation = _policy().select_catalogue_representation(provider_config, accept)
        assert representation.media_type == DCAT_MEDIA_TYPE
        assert representation.alternate is True
        assert representation.headers == expected_headers


def test_dsp_policy_rejects_dcat_ap_request_without_content_negotiation() -> None:
    with pytest.raises(DspProtocolError) as raised:
        _policy().select_catalogue_representation(config(), DCAT_MEDIA_TYPE)
    error = raised.value
    assert (error.kind, error.status_code, error.code) == ("catalog", 406, "not-acceptable")
    assert error.detail == "the DCAT-AP catalogue representation is not available"


# THE DIVERGENCE ANCHOR (Group 2 side), mirroring
# test_http_api_keeps_and_matches_parameterised_accept_range. The dsp/catalogue.py
# path DISCARDS any accept range carrying any parameter (the ``or item.parameters``
# clause in _range_quality), so a parameterised range never matches and the policy
# raises 406. On the http/api.py path the same header can be ACCEPTED when the
# response media type carries the same parameter. The divergence is deliberate.
@pytest.mark.parametrize(
    "accept",
    (
        "application/json;charset=utf-8",  # parameter on an application/json range -> discarded
        "application/ld+json;charset=utf-8",  # only bare or profile=DCAT parameters select the alternate
    ),
)
def test_dsp_policy_parameterised_range_is_discarded(accept: str) -> None:
    provider_config = config(dcat_ap_content_negotiation=True)
    with pytest.raises(DspProtocolError) as raised:
        _policy().select_catalogue_representation(provider_config, accept)
    error = raised.value
    assert (error.kind, error.status_code, error.code) == ("catalog", 406, "not-acceptable")
    assert error.detail == "no acceptable catalogue representation is available"


def test_dsp_policy_exactly_one_media_range_is_required_for_the_alternate() -> None:
    # The alternate DCAT-AP branch fires only when the raw comma count and the
    # parsed-range count are both exactly one. More than one media range never
    # selects the alternate, even when one of them is the DCAT-AP profiled type.
    provider_config = config(dcat_ap_content_negotiation=True)
    # Two ld+json ranges: no plain application/json fallback matches -> 406.
    with pytest.raises(DspProtocolError) as raised:
        _policy().select_catalogue_representation(provider_config, "application/ld+json, application/ld+json")
    assert raised.value.status_code == 406
    # A DCAT-AP range plus a plain json range: alternate skipped, plain json wins
    # (with a Vary header because content negotiation is enabled).
    representation = _policy().select_catalogue_representation(provider_config, "application/ld+json, application/json")
    assert representation.media_type == "application/json"
    assert representation.alternate is False
    assert representation.headers == (("Vary", "Accept"),)


def test_dsp_policy_empty_and_unparseable_list_items_current_behaviour() -> None:
    # Pin how the current code treats empty and unparseable list items. Empty items
    # (trailing or doubled comma) are dropped from BOTH the raw count (blank filter)
    # and the parsed count (they fail media-type parsing), so a single real range
    # with a trailing comma still counts as exactly one range.
    provider_config = config(dcat_ap_content_negotiation=True)
    trailing = _policy().select_catalogue_representation(provider_config, "application/ld+json,")
    assert trailing.media_type == DCAT_MEDIA_TYPE
    assert trailing.alternate is True

    doubled = _policy().select_catalogue_representation(config(), "application/json,,")
    assert doubled.media_type == "application/json"
    assert doubled.alternate is False

    # A NON-blank but unparseable extra item ("garbage") is counted by the raw
    # comma split yet dropped by the parser, so the two counts diverge, the exact
    # alternate branch is skipped, and the sole ld+json range does not match the
    # plain application/json fallback -> 406. This pins the two-splits dependency.
    with pytest.raises(DspProtocolError) as raised:
        _policy().select_catalogue_representation(provider_config, "application/ld+json, garbage")
    assert raised.value.status_code == 406


def test_dsp_policy_unacceptable_type_raises_catalog_not_acceptable() -> None:
    with pytest.raises(DspProtocolError) as raised:
        _policy().select_catalogue_representation(config(), "text/turtle")
    error = raised.value
    assert (error.kind, error.status_code, error.code) == ("catalog", 406, "not-acceptable")
    assert error.detail == "no acceptable catalogue representation is available"
