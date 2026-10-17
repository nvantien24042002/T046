"""Tests for Track B configuration registry and canonicalization."""
import struct
from fractions import Fraction

import pytest

from backend.analytics_configuration import (
    CANONICALIZATION_V1,
    PRODUCTION_REGISTRY,
    REV_2026_10_17_01,
    canonical_json_v1,
    config_id_v1,
    get_default_effective_config,
    payload_sha256_v1,
)
from backend.analytics_contracts import ConfigurationNumber, EffectiveConfiguration


def test_canonical_json_v1_key_ordering():
    """Keys are sorted lexicographically."""
    obj = {"z": 1, "a": 2, "m": 3}
    result = canonical_json_v1(obj)
    assert result == b'{"a":2,"m":3,"z":1}'


def test_canonical_json_v1_no_whitespace():
    """No whitespace in canonical output."""
    obj = {"key": "value", "nested": {"a": 1}}
    result = canonical_json_v1(obj)
    assert b' ' not in result
    assert b'\n' not in result
    assert b'\t' not in result


def test_canonical_json_v1_utf8():
    """UTF-8 encoding without ASCII escape."""
    obj = {"message": "Xin chào"}
    result = canonical_json_v1(obj)
    assert "Xin chào".encode('utf-8') in result
    assert b'\\u' not in result


def test_canonical_json_v1_rejects_nan():
    """NaN values are rejected."""
    obj = {"value": float('nan')}
    with pytest.raises(ValueError):
        canonical_json_v1(obj)


def test_canonical_json_v1_rejects_inf():
    """Infinity values are rejected."""
    obj = {"value": float('inf')}
    with pytest.raises(ValueError):
        canonical_json_v1(obj)


def test_config_id_v1_deterministic():
    """Same inputs produce same config_id."""
    effective = get_default_effective_config()

    id1 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)
    id2 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)

    assert id1 == id2
    assert len(id1) == 64
    assert all(c in "0123456789abcdef" for c in id1)


def test_config_id_v1_distinct_for_different_revision():
    """Different revision produces different config_id."""
    effective = get_default_effective_config()

    id1 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)
    id2 = config_id_v1("config.2026-10-17.02", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)

    assert id1 != id2


def test_config_id_v1_distinct_for_different_defaults():
    """Different defaults version produces different config_id."""
    effective = get_default_effective_config()

    id1 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)
    id2 = config_id_v1("config-2026-10-17-01", "defaults.2026-10-17.02", CANONICALIZATION_V1, effective)

    assert id1 != id2


def test_config_id_v1_distinct_for_different_config():
    """Different effective config produces different config_id."""
    effective1 = get_default_effective_config()

    # Change one threshold
    effective2_dict = effective1.model_dump()
    effective2_dict["image_cfg"]["blur_threshold"] = ConfigurationNumber(
        kind="binary64",
        bits="4069000000000000"  # 200.0 instead of 100.0
    )
    effective2 = EffectiveConfiguration.model_validate(effective2_dict)

    id1 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective1)
    id2 = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective2)

    assert id1 != id2


def test_config_id_v1_integer_vs_binary64_distinct():
    """Integer 100 and binary64 100.0 produce different config_ids."""
    effective = get_default_effective_config()

    # Change blur_threshold from binary64 100.0 to integer 100
    effective_int = effective.model_copy(deep=True)
    effective_int.image_cfg["blur_threshold"] = ConfigurationNumber(kind="integer", decimal="100")

    id_binary = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)
    id_integer = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective_int)

    assert id_binary != id_integer


def test_payload_sha256_v1_deterministic():
    """Same payload produces same hash."""
    payload = {"dataset_id": "test", "quality_rows": [{"filename": "a.jpg"}]}

    hash1 = payload_sha256_v1(payload)
    hash2 = payload_sha256_v1(payload)

    assert hash1 == hash2
    assert len(hash1) == 64


def test_payload_sha256_v1_distinct_for_different_payload():
    """Different payloads produce different hashes."""
    payload1 = {"dataset_id": "test1", "quality_rows": []}
    payload2 = {"dataset_id": "test2", "quality_rows": []}

    hash1 = payload_sha256_v1(payload1)
    hash2 = payload_sha256_v1(payload2)

    assert hash1 != hash2


def test_rev_2026_10_17_01_image_thresholds():
    """First revision defines expected image thresholds."""
    assert "blur_threshold" in REV_2026_10_17_01.image
    assert "dark_threshold" in REV_2026_10_17_01.image
    assert "bright_threshold" in REV_2026_10_17_01.image
    assert "contrast_threshold" in REV_2026_10_17_01.image
    assert "noise_threshold" in REV_2026_10_17_01.image
    assert "min_width" in REV_2026_10_17_01.image
    assert "min_height" in REV_2026_10_17_01.image


def test_rev_2026_10_17_01_label_thresholds():
    """First revision defines expected label thresholds."""
    assert "min_area_pct" in REV_2026_10_17_01.label
    assert "max_area_pct" in REV_2026_10_17_01.label
    assert "min_aspect_ratio" in REV_2026_10_17_01.label
    assert "max_aspect_ratio" in REV_2026_10_17_01.label
    assert "duplicate_iou" in REV_2026_10_17_01.label


def test_rev_2026_10_17_01_cross_field_constraints():
    """First revision defines cross-field threshold pairs."""
    pairs = REV_2026_10_17_01.pairs
    assert ("image_cfg", "dark_threshold", "bright_threshold") in pairs
    assert ("label_cfg", "min_area_pct", "max_area_pct") in pairs
    assert ("label_cfg", "min_aspect_ratio", "max_aspect_ratio") in pairs


def test_rev_2026_10_17_01_approved_versions():
    """First revision defines approved version tuple."""
    versions = REV_2026_10_17_01.versions
    assert ("defaults-2026-10-17-01", CANONICALIZATION_V1, "algo-core-v1") in versions


def test_production_registry_contains_first_revision():
    """Production registry contains first revision."""
    assert "config-2026-10-17-01" in PRODUCTION_REGISTRY._specifications


def test_get_default_effective_config_values():
    """Default effective config matches Streamlit defaults."""
    effective = get_default_effective_config()

    # Check image thresholds
    assert effective.image_cfg["blur_threshold"].kind == "binary64"
    blur_value = struct.unpack(">d", bytes.fromhex(effective.image_cfg["blur_threshold"].bits))[0]
    assert blur_value == 100.0

    assert effective.image_cfg["dark_threshold"].kind == "integer"
    assert effective.image_cfg["dark_threshold"].decimal == "40"

    assert effective.image_cfg["bright_threshold"].kind == "integer"
    assert effective.image_cfg["bright_threshold"].decimal == "215"


def test_get_default_effective_config_unknown_revision():
    """Unknown revision raises ValueError."""
    with pytest.raises(ValueError, match="Unknown revision"):
        get_default_effective_config("config.unknown")


def test_production_registry_validates_descriptor(tmp_path):
    """Production registry validates complete descriptor."""
    from backend.analytics_contracts import Descriptor

    effective = get_default_effective_config()
    config_id = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)

    descriptor = Descriptor(
        result_id="test_result_01",
        dataset_id="test_dataset",
        archive_sha256="a" * 64,
        config_id=config_id,
        request_fingerprint=None,
        configuration_schema_revision="config-2026-10-17-01",
        effective_config=effective,
        defaults_version="defaults-2026-10-17-01",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="test_build",
        payload_sha256="b" * 64,
        verification="verified",
    )

    # Should not raise
    validated = PRODUCTION_REGISTRY.validate(descriptor)
    assert validated == descriptor


def test_production_registry_rejects_unsupported_revision():
    """Registry rejects descriptor with unsupported revision."""
    from backend.analytics_contracts import Descriptor

    effective = get_default_effective_config()

    descriptor = Descriptor(
        result_id="test_result_01",
        dataset_id="test_dataset",
        archive_sha256="a" * 64,
        config_id="fake_config_id",
        request_fingerprint=None,
        configuration_schema_revision="config-unsupported",
        effective_config=effective,
        defaults_version="defaults-2026-10-17-01",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="test_build",
        payload_sha256="b" * 64,
        verification="verified",
    )

    with pytest.raises(ValueError, match="Unsupported configuration revision"):
        PRODUCTION_REGISTRY.validate(descriptor)


def test_production_registry_rejects_unsupported_version_tuple():
    """Registry rejects descriptor with unsupported version combination."""
    from backend.analytics_contracts import Descriptor

    effective = get_default_effective_config()
    config_id = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective)

    descriptor = Descriptor(
        result_id="test_result_01",
        dataset_id="test_dataset",
        archive_sha256="a" * 64,
        config_id=config_id,
        request_fingerprint=None,
        configuration_schema_revision="config-2026-10-17-01",
        effective_config=effective,
        defaults_version="defaults-unsupported",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="test_build",
        payload_sha256="b" * 64,
        verification="verified",
    )

    with pytest.raises(ValueError, match="Unsupported configuration revision/version"):
        PRODUCTION_REGISTRY.validate(descriptor)


def test_threshold_rule_validates_domain():
    """ThresholdRule validates values are in domain."""
    from backend.analytics_contracts import ThresholdRule

    rule = ThresholdRule(minimum=10, maximum=100)

    # Valid values
    rule.check(ConfigurationNumber(kind="integer", decimal="50"))
    rule.check(ConfigurationNumber(kind="integer", decimal="10"))
    rule.check(ConfigurationNumber(kind="integer", decimal="100"))

    # Below minimum
    with pytest.raises(ValueError, match="below domain"):
        rule.check(ConfigurationNumber(kind="integer", decimal="9"))

    # Above maximum
    with pytest.raises(ValueError, match="above domain"):
        rule.check(ConfigurationNumber(kind="integer", decimal="101"))


def test_threshold_rule_positive_constraint():
    """ThresholdRule enforces positive constraint."""
    from backend.analytics_contracts import ThresholdRule

    rule = ThresholdRule(minimum=0, positive=True)

    # Positive value ok
    rule.check(ConfigurationNumber(kind="integer", decimal="1"))

    # Zero rejected
    with pytest.raises(ValueError, match="below domain"):
        rule.check(ConfigurationNumber(kind="integer", decimal="0"))

    # Negative rejected
    with pytest.raises(ValueError, match="below domain"):
        rule.check(ConfigurationNumber(kind="integer", decimal="-1"))


def test_threshold_rule_integer_only():
    """ThresholdRule enforces integer-only constraint."""
    from backend.analytics_contracts import ThresholdRule

    rule = ThresholdRule(minimum=0, integer_only=True)

    # Integer ok
    rule.check(ConfigurationNumber(kind="integer", decimal="100"))

    # Binary64 rejected
    with pytest.raises(ValueError, match="Integer required"):
        rule.check(ConfigurationNumber(kind="binary64", bits="4059000000000000"))


def test_registry_validates_cross_field_constraints():
    """Registry validates cross-field threshold ordering."""
    from backend.analytics_contracts import Descriptor

    effective = get_default_effective_config()

    # Swap dark and bright thresholds (invalid)
    effective_dict = effective.model_dump()
    effective_dict["image_cfg"]["dark_threshold"] = ConfigurationNumber(kind="integer", decimal="215")
    effective_dict["image_cfg"]["bright_threshold"] = ConfigurationNumber(kind="integer", decimal="40")
    effective_invalid = EffectiveConfiguration.model_validate(effective_dict)

    config_id = config_id_v1("config-2026-10-17-01", "defaults-2026-10-17-01", CANONICALIZATION_V1, effective_invalid)

    descriptor = Descriptor(
        result_id="test_result_01",
        dataset_id="test_dataset",
        archive_sha256="a" * 64,
        config_id=config_id,
        request_fingerprint=None,
        configuration_schema_revision="config-2026-10-17-01",
        effective_config=effective_invalid,
        defaults_version="defaults-2026-10-17-01",
        canonicalization_version=CANONICALIZATION_V1,
        algorithm_version="algo-core-v1",
        producer_build_id="test_build",
        payload_sha256="b" * 64,
        verification="verified",
    )

    with pytest.raises(ValueError, match="Reversed thresholds"):
        PRODUCTION_REGISTRY.validate(descriptor)


def test_configuration_number_exact_preserves_integer():
    """ConfigurationNumber.exact() preserves integer precision."""
    num = ConfigurationNumber(kind="integer", decimal="123456789012345678901234567890")
    exact = num.exact()
    assert exact == Fraction(123456789012345678901234567890)
    assert exact.denominator == 1


def test_configuration_number_exact_preserves_binary64_precision():
    """ConfigurationNumber.exact() preserves binary64 exact value."""
    # 0.1 is not exactly representable in binary64
    num = ConfigurationNumber(kind="binary64", bits="3fb999999999999a")  # 0.1
    exact = num.exact()

    # Should be exact binary64 representation, not decimal 0.1
    assert exact != Fraction(1, 10)
    assert abs(float(exact) - 0.1) < 1e-15
