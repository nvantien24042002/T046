"""Track A wire validation. No production configuration revision is registered."""
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
import json
import math
import re
import struct
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

VERSION = "analytics.v1"
ORIGIN = "http://127.0.0.1:5173"
AUDIENCE = "http://127.0.0.1:8012"
UPSTREAM = "http://127.0.0.1:8011"
METADATA_LIMIT = 64 * 1024
RESPONSE_LIMIT = 64 * 1024 * 1024
MAX_SAFE = 2**53 - 1
MAX_CONFIG_INT = 2**1024 - 2**970 - 1
ID = Annotated[str, Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")]
SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Count = Annotated[int, Field(strict=True, ge=0, le=MAX_SAFE)]
ErrorCode = Literal[
    "ACCESS_GRANT_INVALID", "ACCESS_SCOPE_DENIED", "INVALID_ACCESS_REQUEST",
    "LOCAL_ONLY", "DATASET_NOT_FOUND", "RESULT_NOT_FOUND", "DATASET_NOT_READY",
    "RESULT_PROVENANCE_UNAVAILABLE", "UPSTREAM_RESULT_INVALID", "UPSTREAM_RESPONSE_LIMIT",
    "BACKEND_UNAVAILABLE", "INTERNAL_SERVER_ERROR",
    "IMAGE_NOT_FOUND", "IMAGE_SIZE_LIMIT", "IMAGE_DECODE_FAILED", "INVALID_PATH",
]


class AnalyticsError(Exception):
    def __init__(self, status: int, code: ErrorCode):
        self.status, self.code = status, code
        super().__init__(code)


class Wire(BaseModel):
    model_config = ConfigDict(strict=True, extra="ignore", allow_inf_nan=False)


class Failure(Wire):
    code: ErrorCode


class ErrorEnvelope(Wire):
    error: Failure


class ExchangeRequest(Wire):
    model_config = ConfigDict(strict=True, extra="forbid")
    exchange_code: str = Field(min_length=1, max_length=512, pattern=r"^[A-Za-z0-9_-]+$")


class GrantResponse(Wire):
    schema_version: Literal["analytics.v1"]
    access_token: str = Field(min_length=32, max_length=512, repr=False)
    token_type: Literal["Bearer"]
    server_time: str
    expires_at: str
    dataset_id: ID
    result_ids: list[ID]
    scopes: list[Literal["summary:read", "results:read"]]

    @model_validator(mode="after")
    def policy(self):
        stamps = []
        for value in (self.server_time, self.expires_at):
            if not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", value):
                raise ValueError("Invalid UTC timestamp")
            stamps.append(datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ"))
        if (stamps[1]-stamps[0]).total_seconds() != 600:
            raise ValueError("Invalid absolute lifetime")
        if (len(set(self.scopes)) != len(self.scopes) or "summary:read" not in self.scopes
                or len(set(self.result_ids)) != len(self.result_ids)
                or "results:read" not in self.scopes and self.result_ids):
            raise ValueError("Inconsistent grant scopes")
        return self


class Features(Wire):
    image_quality: bool
    label_audit: bool
    distribution: bool
    coverage: bool
    scene_finder: bool


class Summary(Wire):
    schema_version: Literal["analytics.v1"] = VERSION
    dataset_id: ID
    upload_id: ID
    state: Literal["validating", "indexing", "publishing", "ready", "failed", "cancelled"]
    ready: bool
    error_code: str | None = Field(default=None, max_length=80, pattern=r"^[A-Z_]+$")
    archive_bytes: Count | None = None
    archive_sha256: SHA | None = None
    entries: Count | None = None
    images: Count | None = None
    annotations: bool | None = None
    xml_records: dict[str, Count] | None = None
    capabilities: Features | None = None

    @model_validator(mode="after")
    def lifecycle(self):
        if self.ready != (self.state == "ready"):
            raise ValueError("Inconsistent lifecycle")
        fields = ("archive_bytes", "archive_sha256", "entries", "images", "annotations", "capabilities")
        if self.ready and (any(getattr(self, key) is None for key in fields) or self.error_code is not None):
            raise ValueError("Invalid ready summary")
        if not self.ready and any(getattr(self, key) is not None for key in fields):
            raise ValueError("Unexpected ready metadata")
        return self

    def public(self):
        data = self.model_dump(exclude_none=True)
        data["error_code"] = self.error_code
        return data


class ConfigurationNumber(Wire):
    model_config = ConfigDict(strict=True, extra="forbid")
    kind: Literal["integer", "binary64"]
    decimal: str | None = None
    bits: str | None = None

    @model_validator(mode="after")
    def representation(self):
        if self.model_fields_set != {"kind", "decimal" if self.kind == "integer" else "bits"}:
            raise ValueError("Unexpected numeric fields")
        if self.kind == "integer":
            if self.bits is not None or self.decimal is None or len(self.decimal.lstrip("-")) > 309:
                raise ValueError("Invalid integer")
            if not re.fullmatch(r"0|[1-9][0-9]*|-[1-9][0-9]*", self.decimal):
                raise ValueError("Invalid integer")
            if abs(int(self.decimal)) > MAX_CONFIG_INT:
                raise ValueError("Integer outside Python-compatible domain")
        else:
            if self.decimal is not None or self.bits is None or not re.fullmatch(r"[0-9a-f]{16}", self.bits):
                raise ValueError("Invalid binary64")
            if not math.isfinite(struct.unpack(">d", bytes.fromhex(self.bits))[0]):
                raise ValueError("Non-finite binary64")
        return self

    @model_serializer
    def serialize(self):
        key = "decimal" if self.kind == "integer" else "bits"
        return {"kind": self.kind, key: getattr(self, key)}

    def exact(self):
        if self.kind == "integer":
            return Fraction(int(self.decimal))
        return Fraction.from_float(struct.unpack(">d", bytes.fromhex(self.bits))[0])


@dataclass(frozen=True)
class ThresholdRule:
    minimum: int | float = 0
    maximum: int | float | None = None
    integer_only: bool = False
    positive: bool = False

    def check(self, number):
        value = number.exact()
        if self.integer_only and number.kind != "integer":
            raise ValueError("Integer required")
        if value < self.minimum or self.positive and value <= 0:
            raise ValueError("Threshold below domain")
        if self.maximum is not None and value > self.maximum:
            raise ValueError("Threshold above domain")


@dataclass(frozen=True)
class ConfigurationRevision:
    """Injected approved specification; identifier/digest are never invented here."""
    specification_sha256: str
    image: dict[str, ThresholdRule]
    label: dict[str, ThresholdRule]
    pairs: tuple[tuple[str, str, str], ...]
    versions: frozenset[tuple[str, str, str]]

    def __post_init__(self):
        if not re.fullmatch(r"[0-9a-f]{64}", self.specification_sha256):
            raise ValueError("Invalid specification digest")
        object.__setattr__(self, "image", MappingProxyType(dict(self.image)))
        object.__setattr__(self, "label", MappingProxyType(dict(self.label)))
        object.__setattr__(self, "pairs", tuple(tuple(pair) for pair in self.pairs))
        object.__setattr__(self, "versions", frozenset(self.versions))


class EffectiveConfiguration(Wire):
    model_config = ConfigDict(strict=True, extra="forbid")
    image_cfg: dict[str, ConfigurationNumber]
    label_cfg: dict[str, ConfigurationNumber]


class Descriptor(Wire):
    result_id: ID
    dataset_id: ID
    archive_sha256: SHA
    config_id: ID
    request_fingerprint: SHA | None
    configuration_schema_revision: ID
    effective_config: EffectiveConfiguration
    defaults_version: ID
    canonicalization_version: ID
    algorithm_version: ID
    producer_build_id: ID
    payload_sha256: SHA
    verification: Literal["verified"]


class RevisionRegistry:
    def __init__(self, specifications=None):
        self._specifications = MappingProxyType(dict(specifications or {}))

    def validate(self, descriptor):
        spec = self._specifications.get(descriptor.configuration_schema_revision)
        if spec is None or (descriptor.defaults_version, descriptor.canonicalization_version,
                            descriptor.algorithm_version) not in spec.versions:
            raise ValueError("Unsupported configuration revision/version")
        config = descriptor.effective_config
        for group, rules in (("image_cfg", spec.image), ("label_cfg", spec.label)):
            values = getattr(config, group)
            if values.keys() != rules.keys():
                raise ValueError("Unsupported configuration keys")
            for key, rule in rules.items():
                rule.check(values[key])
        for group, lower, upper in spec.pairs:
            values = getattr(config, group)
            if values[lower].exact() > values[upper].exact():
                raise ValueError("Reversed thresholds")
        return descriptor

    def validate_submitted(self, descriptor, submitted):
        self.validate(descriptor)
        spec = self._specifications[descriptor.configuration_schema_revision]
        for group, rules in (("image_cfg", spec.image), ("label_cfg", spec.label)):
            values = getattr(submitted, group)
            if values is None:
                continue
            if not values.keys() <= rules.keys():
                raise ValueError("Unknown submitted threshold")
            for key, value in values.items():
                rules[key].check(value)
                if value != getattr(descriptor.effective_config, group)[key]:
                    raise ValueError("Submitted/effective mismatch")


def encoded(value, limit):
    try:
        raw = json.dumps(value, allow_nan=False, separators=(",", ":")).encode()
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise AnalyticsError(502, "UPSTREAM_RESULT_INVALID") from None
    if len(raw) > limit:
        raise AnalyticsError(502, "UPSTREAM_RESPONSE_LIMIT")
    return raw


def parse_json(raw):
    def invalid(_):
        raise ValueError("Non-finite JSON")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, parse_constant=invalid, object_pairs_hook=unique)
