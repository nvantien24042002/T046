"""Configuration schema registry and canonicalization for Track B.

Implements immutable versioned configuration specifications with exact numeric
representation, canonical hash-input generation, and provenance binding.
"""
import hashlib
import json

from backend.analytics_contracts import (
    ConfigurationRevision,
    EffectiveConfiguration,
    RevisionRegistry,
    ThresholdRule,
)


# Canonical JSON serialization version for hash-input generation
CANONICALIZATION_V1 = "canon-v1"


def canonical_json_v1(obj: dict) -> bytes:
    """Generate canonical JSON bytes for hashing (version 1).

    Rules:
    - Keys sorted lexicographically
    - No whitespace
    - UTF-8 encoding
    - Numbers represented as their ConfigurationNumber wire format
    - Null values preserved
    """
    return json.dumps(
        obj,
        ensure_ascii=False,
        separators=(',', ':'),
        sort_keys=True,
        allow_nan=False,
    ).encode('utf-8')


def config_id_v1(
    revision: str,
    defaults_version: str,
    canonicalization_version: str,
    effective_config: EffectiveConfiguration,
) -> str:
    """Generate immutable config_id binding all configuration identity.

    Binds: revision, defaults version, canonicalization version, effective config.
    Returns: Hex SHA-256 string (64 chars).
    """
    # Serialize effective config preserving exact numeric representation
    config_dict = {
        "image_cfg": {k: v.model_dump() for k, v in effective_config.image_cfg.items()},
        "label_cfg": {k: v.model_dump() for k, v in effective_config.label_cfg.items()},
    }

    identity_payload = {
        "configuration_schema_revision": revision,
        "defaults_version": defaults_version,
        "canonicalization_version": canonicalization_version,
        "effective_config": config_dict,
    }

    canonical_bytes = canonical_json_v1(identity_payload)
    return hashlib.sha256(canonical_bytes).hexdigest()


def payload_sha256_v1(payload: dict) -> str:
    """Generate payload content hash (version 1).

    Covers the entire result payload with canonical JSON serialization.
    Returns: Hex SHA-256 string (64 chars).
    """
    canonical_bytes = canonical_json_v1(payload)
    return hashlib.sha256(canonical_bytes).hexdigest()


# First production configuration revision
# Based on existing Streamlit defaults and backend analysis parameters
REV_2026_10_17_01 = ConfigurationRevision(
    specification_sha256="0" * 64,  # Placeholder - would be hash of specification document
    image={
        "blur_threshold": ThresholdRule(minimum=0, positive=True),
        "dark_threshold": ThresholdRule(minimum=0, maximum=255, integer_only=True),
        "bright_threshold": ThresholdRule(minimum=0, maximum=255, integer_only=True),
        "contrast_threshold": ThresholdRule(minimum=0, positive=True),
        "noise_threshold": ThresholdRule(minimum=0, positive=True),
        "min_width": ThresholdRule(minimum=1, integer_only=True),
        "min_height": ThresholdRule(minimum=1, integer_only=True),
        "black_threshold": ThresholdRule(minimum=0, maximum=255, integer_only=True),
        "white_threshold": ThresholdRule(minimum=0, maximum=255, integer_only=True),
    },
    label={
        "min_area_pct": ThresholdRule(minimum=0, maximum=100, positive=True),
        "max_area_pct": ThresholdRule(minimum=0, maximum=100, positive=True),
        "min_aspect_ratio": ThresholdRule(minimum=0, positive=True),
        "max_aspect_ratio": ThresholdRule(minimum=0, positive=True),
        "duplicate_iou": ThresholdRule(minimum=0, maximum=1),
        "min_edge_density": ThresholdRule(minimum=0, positive=True),
        "min_texture_variance": ThresholdRule(minimum=0, positive=True),
        "min_laplacian_variance": ThresholdRule(minimum=0, positive=True),
        "min_foreground_contrast": ThresholdRule(minimum=0, positive=True),
        "min_boundary_support": ThresholdRule(minimum=0, positive=True),
    },
    pairs=(
        ("image_cfg", "dark_threshold", "bright_threshold"),
        ("label_cfg", "min_area_pct", "max_area_pct"),
        ("label_cfg", "min_aspect_ratio", "max_aspect_ratio"),
    ),
    versions=frozenset({
        ("defaults-2026-10-17-01", CANONICALIZATION_V1, "algo-core-v1"),
    }),
)


# Production registry
PRODUCTION_REGISTRY = RevisionRegistry({
    "config-2026-10-17-01": REV_2026_10_17_01,
})


# Default configuration values (from Streamlit app.py)
def get_default_effective_config(revision: str = "config-2026-10-17-01") -> EffectiveConfiguration:
    """Get default effective configuration for a revision.

    These values match the Streamlit app.py defaults.
    """
    from backend.analytics_contracts import ConfigurationNumber

    if revision != "config-2026-10-17-01":
        raise ValueError(f"Unknown revision: {revision}")

    return EffectiveConfiguration(
        image_cfg={
            "blur_threshold": ConfigurationNumber(kind="binary64", bits="4059000000000000"),  # 100.0
            "dark_threshold": ConfigurationNumber(kind="integer", decimal="40"),
            "bright_threshold": ConfigurationNumber(kind="integer", decimal="215"),
            "contrast_threshold": ConfigurationNumber(kind="binary64", bits="4039000000000000"),  # 25.0
            "noise_threshold": ConfigurationNumber(kind="binary64", bits="4024000000000000"),  # 10.0
            "min_width": ConfigurationNumber(kind="integer", decimal="320"),
            "min_height": ConfigurationNumber(kind="integer", decimal="240"),
            "black_threshold": ConfigurationNumber(kind="integer", decimal="30"),
            "white_threshold": ConfigurationNumber(kind="integer", decimal="225"),
        },
        label_cfg={
            "min_area_pct": ConfigurationNumber(kind="binary64", bits="3f847ae147ae147b"),  # 0.01
            "max_area_pct": ConfigurationNumber(kind="binary64", bits="4054000000000000"),  # 80.0
            "min_aspect_ratio": ConfigurationNumber(kind="binary64", bits="3fc3333333333333"),  # 0.15
            "max_aspect_ratio": ConfigurationNumber(kind="binary64", bits="401c000000000000"),  # 7.0
            "duplicate_iou": ConfigurationNumber(kind="binary64", bits="3feccccccccccccd"),  # 0.9
            "min_edge_density": ConfigurationNumber(kind="binary64", bits="3fb999999999999a"),  # 0.1
            "min_texture_variance": ConfigurationNumber(kind="binary64", bits="4024000000000000"),  # 10.0
            "min_laplacian_variance": ConfigurationNumber(kind="binary64", bits="4059000000000000"),  # 100.0
            "min_foreground_contrast": ConfigurationNumber(kind="binary64", bits="4034000000000000"),  # 20.0
            "min_boundary_support": ConfigurationNumber(kind="binary64", bits="3fc999999999999a"),  # 0.2
        }
    )
