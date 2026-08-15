"""Load and validate the bundled DSP and local profile schemas offline."""

from typing import Any

from httk.serve.http.openapi import OpenAPIContract, OpenAPISchemaError

_CONTRACT_PACKAGE = "httk.serve.dsp"


class DspSchemaError(ValueError):
    """Report a DSP schema-validation failure."""


def _normalized_references(value: Any) -> Any:
    """Correct the three pinned upstream JSON-Pointer fragment typos in memory."""
    if isinstance(value, dict):
        return {key: _normalized_references(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalized_references(child) for child in value]
    if isinstance(value, str) and ".json#definitions/" in value:
        return value.replace(".json#definitions/", ".json#/definitions/")
    return value


def dsp_contract() -> OpenAPIContract:
    """Return the cached bundled DSP OpenAPI contract and offline schema registry.

    :return: Contract parsed exclusively from package data.
    :raises DspSchemaError: If any bundled schema document is invalid.
    :raises httk.serve.http.openapi.OpenAPIContractError: If the bundled
        OpenAPI document uses an unsupported construct.
    """
    try:
        return OpenAPIContract.from_package(_CONTRACT_PACKAGE, schema_transform=_normalized_references)
    except OpenAPISchemaError as error:
        raise DspSchemaError(str(error)) from error


__all__ = ["DspSchemaError", "dsp_contract"]
