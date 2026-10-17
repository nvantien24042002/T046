import struct

from pydantic import ValidationError
import pytest

from backend.analytics_contracts import (
    ConfigurationNumber, Descriptor, ErrorEnvelope, GrantResponse, MAX_CONFIG_INT,
)
from tests.analytics_support import descriptor, registry


@pytest.mark.parametrize("value", ["-0", "+1", "01", "1e3", "1.0", " 1", "9"*310,
                                    str(MAX_CONFIG_INT+1)])
def test_noncanonical_or_out_of_domain_integers(value):
    with pytest.raises(ValidationError):
        ConfigurationNumber.model_validate({"kind": "integer", "decimal": value})


@pytest.mark.parametrize("bits", ["7ff0000000000000", "fff0000000000000", "7ff8000000000001",
                                   "ABCDEF0000000000", "00"])
def test_invalid_binary64(bits):
    with pytest.raises(ValidationError):
        ConfigurationNumber.model_validate({"kind": "binary64", "bits": bits})


def test_numeric_evidence_is_lossless_and_representation_sensitive():
    integer = ConfigurationNumber.model_validate({"kind": "integer", "decimal": str(MAX_CONFIG_INT)})
    assert integer.exact() == MAX_CONFIG_INT
    positive = ConfigurationNumber.model_validate({"kind": "binary64", "bits": "0000000000000000"})
    negative = ConfigurationNumber.model_validate({"kind": "binary64", "bits": "8000000000000000"})
    zero = ConfigurationNumber.model_validate({"kind": "integer", "decimal": "0"})
    assert positive.exact() == negative.exact() == zero.exact()
    assert positive != negative and positive != zero
    assert negative.model_dump() == {"kind": "binary64", "bits": "8000000000000000"}


@pytest.mark.parametrize("mutation", ["unknown", "missing", "float_dimension", "negative", "reversed", "revision"])
def test_revision_keys_domains_and_pairs(mutation):
    value = descriptor()
    image, label = value["effective_config"]["image_cfg"], value["effective_config"]["label_cfg"]
    if mutation == "unknown": image["unknown"] = {"kind": "integer", "decimal": "1"}
    elif mutation == "missing": image.clear()
    elif mutation == "float_dimension": image["min_width"] = {"kind": "binary64", "bits": "3ff0000000000000"}
    elif mutation == "negative": image["min_width"] = {"kind": "integer", "decimal": "-1"}
    elif mutation == "reversed": label["min_aspect"]["decimal"] = "3"
    else: value["configuration_schema_revision"] = "unknown"
    with pytest.raises(ValueError):
        registry().validate(Descriptor.model_validate(value))


def test_stable_error_allowlist():
    assert ErrorEnvelope.model_validate({"error": {"code": "ACCESS_SCOPE_DENIED"}})
    with pytest.raises(ValidationError):
        ErrorEnvelope.model_validate({"error": {"code": "PRIVATE_SECRET"}})


@pytest.mark.parametrize("change", [
    {"expires_at": "2026-10-17T07:10:01Z"}, {"server_time": "2026-10-17T07:00:00+00:00"},
    {"scopes": ["results:read"]}, {"scopes": ["summary:read"]},
    {"token_type": "Basic"}, {"schema_version": "analytics.v2"},
])
def test_grant_wire_policy(change):
    value = {"schema_version": "analytics.v1", "access_token": "synthetic-token-"+"x"*32,
        "token_type": "Bearer", "server_time": "2026-10-17T07:00:00Z",
        "expires_at": "2026-10-17T07:10:00Z", "dataset_id": "dataset-a", "result_ids": ["result-a"],
        "scopes": ["summary:read", "results:read"]}
    value.update(change)
    with pytest.raises(ValidationError):
        GrantResponse.model_validate(value)
