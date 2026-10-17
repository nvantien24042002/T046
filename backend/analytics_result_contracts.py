"""Synthetic-provider validation only; no hashing, persistence or publication."""
from typing import Annotated, Literal

from pydantic import ConfigDict, Field, model_validator

from backend.analytics_contracts import (
    Wire, ID, SHA, Count, MAX_SAFE, ConfigurationNumber, Descriptor,
)

Metric = float | None


class QualityRow(Wire):
    filename: str = Field(min_length=1)
    status: Literal["PASS", "REVIEW", "ERROR"]
    score: Annotated[float, Field(ge=0, le=100)]
    issues: list[str]
    width: Count
    height: Count
    size_kb: Annotated[float, Field(ge=0)]
    blur_score: Metric
    brightness: Metric
    contrast: Metric
    noise_score: Metric
    black_pct: Metric
    white_pct: Metric
    error: str


class AuditRow(Wire):
    image_id: str
    image_name: str
    box_id: str
    label: str
    occluded: str
    xtl: Metric
    ytl: Metric
    xbr: Metric
    ybr: Metric
    image_available: bool
    image_error: str | None
    area_pct: Metric
    aspect_ratio: Metric
    edge_density: Metric
    texture_variance: Metric
    laplacian_variance: Metric
    foreground_contrast: Metric
    boundary_support: Metric
    geometry_issues: str
    visual_issues: str
    issues: str
    issue_count: Count
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    status: Literal["REVIEW", "REVIEW_A_ONLY", "NO_SIGNAL", "A_ONLY"]


class ClassRow(Wire):
    class_name: str = Field(alias="class")
    boxes: Count
    images: Count
    share_pct: Annotated[float, Field(ge=0, le=100)]


class AttributeRow(Wire):
    attribute: str
    value: str
    label: str
    count: Count


class DistributionImage(Wire):
    image_id: str
    image_name: str
    weather: str
    timeofday: str


class DistributionBox(Wire):
    image_id: str
    image_name: str
    box_id: str
    label: str
    occluded: str
    valid_geometry: bool
    area_pct: Metric
    attributes: dict[str, str | None]


class Payload(Wire):
    dataset_id: ID
    quality_rows: list[QualityRow] = Field(min_length=1)
    audit_rows: list[AuditRow]
    classes: list[ClassRow]
    attribute_distribution: list[AttributeRow]
    distribution_images: list[DistributionImage]
    distribution_boxes: list[DistributionBox]
    attributes: list[str]

    @model_validator(mode="before")
    @classmethod
    def safe_json_integers(cls, value):
        def walk(item):
            if type(item) is int and abs(item) > MAX_SAFE:
                raise ValueError("Unsafe payload integer")
            if isinstance(item, dict):
                for child in item.values():
                    walk(child)
            elif isinstance(item, list):
                for child in item:
                    walk(child)
        walk(value)
        return value


class Submitted(Wire):
    model_config = ConfigDict(strict=True, extra="forbid")
    image_cfg: dict[str, ConfigurationNumber] | None
    label_cfg: dict[str, ConfigurationNumber] | None


class Provenance(Wire):
    submitted_config: Submitted
    validation_attempt_id: ID
    validation_manifest_sha256: SHA
    validation_index_sha256: SHA
    producer_source_commit: str = Field(min_length=1, max_length=128)
    dependency_digest: SHA


class SyntheticEnvelope(Wire):
    schema_version: Literal["analytics.v1"]
    descriptor: Descriptor
    provenance: Provenance
    payload: Payload
