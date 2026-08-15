"""Tests for the protocol-neutral JSON freeze/thaw model."""

from types import MappingProxyType

import pytest

import httk.serve.dsp.models as dsp_models
import httk.serve.jsondata as jsondata
from httk.serve.jsondata import freeze_json, thaw_json


def test_freeze_json_returns_immutable_containers_independent_of_input() -> None:
    """Freezing copies nested containers so later input mutation is not observed."""
    source: dict[str, object] = {"outer": {"inner": [1, 2]}, "list": [{"k": "v"}]}
    frozen = freeze_json(source)
    assert isinstance(frozen, MappingProxyType)
    outer = frozen["outer"]
    assert isinstance(outer, MappingProxyType)
    assert isinstance(outer["inner"], tuple)
    entries = frozen["list"]
    assert isinstance(entries, tuple)
    assert isinstance(entries[0], MappingProxyType)

    inner = source["outer"]
    assert isinstance(inner, dict)
    inner["inner"].append(3)
    source["list"].append({"k": "w"})  # type: ignore[attr-defined]
    assert outer["inner"] == (1, 2)
    assert len(entries) == 1


def test_freeze_json_rejects_non_finite_floats() -> None:
    """Non-finite floats are rejected with ValueError."""
    for value in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError, match="JSON floats must be finite"):
            freeze_json(value)


def test_freeze_json_rejects_non_string_keys() -> None:
    """Object keys that are not strings are rejected with TypeError."""
    with pytest.raises(TypeError, match="JSON object keys must be strings"):
        freeze_json({1: "one"})


def test_freeze_json_rejects_non_json_types() -> None:
    """Arbitrary non-JSON values are rejected with a descriptive TypeError."""
    with pytest.raises(TypeError, match=r"Expected a JSON-compatible value, got set"):
        freeze_json({1, 2, 3})
    with pytest.raises(TypeError, match=r"Expected a JSON-compatible value, got object"):
        freeze_json(object())


def test_freeze_json_accepts_lists_and_tuples_and_produces_tuples() -> None:
    """Both list and tuple inputs freeze into tuples."""
    assert freeze_json([1, 2, 3]) == (1, 2, 3)
    assert freeze_json((1, 2, 3)) == (1, 2, 3)
    assert isinstance(freeze_json([1]), tuple)
    assert isinstance(freeze_json((1,)), tuple)


def test_thaw_json_returns_independent_plain_containers() -> None:
    """Thawing yields plain dict/list values independent of the frozen input."""
    frozen = freeze_json({"a": [1, {"b": 2}]})
    assert isinstance(frozen, MappingProxyType)
    frozen_a = frozen["a"]
    assert isinstance(frozen_a, tuple)
    thawed = thaw_json(frozen)
    assert thawed == {"a": [1, {"b": 2}]}
    assert isinstance(thawed, dict)
    entries = thawed["a"]
    assert isinstance(entries, list)
    assert isinstance(entries[1], dict)
    entries.append(99)
    assert len(frozen_a) == 2


def test_scalars_pass_through_unchanged() -> None:
    """Scalar values round-trip identically, including booleans, None, and zero."""
    for value in ("text", 0, 42, -1.5, True, False, None):
        assert freeze_json(value) == value
        assert thaw_json(value) == value


def test_dsp_models_reexports_the_same_freeze_json() -> None:
    """The DSP models re-export is the object defined in httk.serve.jsondata."""
    assert dsp_models.freeze_json is jsondata.freeze_json
    assert dsp_models.thaw_json is jsondata.thaw_json
