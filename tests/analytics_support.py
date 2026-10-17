"""Synthetic authority/transport records only; no runtime datasets or credentials."""
from dataclasses import replace
import copy
import threading

from backend.analytics_contracts import (
    AUDIENCE, ORIGIN, ConfigurationRevision, RevisionRegistry, ThresholdRule,
)
from backend.analytics_grants import IssuerRecord


class Clock:
    value = 1800000000
    def __call__(self):
        return self.value


class FakeIssuer:
    def __init__(self, records):
        self.records = dict(records)
        self.lock = threading.Lock()
    def consume(self, code):
        with self.lock:
            return self.records.pop(code, None)


def record(clock, **changes):
    value = IssuerRecord(int(clock()), AUDIENCE, ORIGIN, "dataset-a",
                         frozenset({"summary:read", "results:read"}),
                         frozenset({"result-a"}), "FAKE-UPSTREAM-CAPABILITY")
    return replace(value, **changes)


def ready():
    return {"dataset_id": "dataset-a", "upload_id": "upload-a", "state": "ready", "ready": True,
            "archive_bytes": 42, "archive_sha256": "a"*64, "entries": 1, "images": 1,
            "annotations": False, "capabilities": {"image_quality": True, "label_audit": False,
              "distribution": False, "coverage": False, "scene_finder": False}}


class FakeUpstream:
    def __init__(self, value=None):
        self.value = ready() if value is None else value
        self.calls = []
    def summary(self, dataset_id, capability, origin):
        self.calls.append((dataset_id, capability, origin))
        return copy.deepcopy(self.value)

    def preview(self, dataset_id, capability, image_path, annotated, origin):
        self.calls.append((dataset_id, capability, image_path, annotated, origin))
        if image_path == "missing.png":
            from backend.analytics_contracts import AnalyticsError
            raise AnalyticsError(404, "IMAGE_NOT_FOUND")
        return b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82", "image/png"


def registry():
    # This identifier is exclusively a synthetic test revision, never production.
    return RevisionRegistry({"synthetic-test-only": ConfigurationRevision(
        "f"*64, {"min_width": ThresholdRule(minimum=1, integer_only=True)},
        {"min_aspect": ThresholdRule(positive=True), "max_aspect": ThresholdRule(positive=True)},
        (("label_cfg", "min_aspect", "max_aspect"),),
        frozenset({("synthetic-defaults", "synthetic-canonical", "synthetic-algorithm")}))})


def descriptor():
    return {"result_id": "result-a", "dataset_id": "dataset-a", "archive_sha256": "a"*64,
            "config_id": "config-a", "request_fingerprint": None,
            "configuration_schema_revision": "synthetic-test-only",
            "effective_config": {"image_cfg": {"min_width": {"kind": "integer", "decimal": "9007199254740993"}},
              "label_cfg": {"min_aspect": {"kind": "integer", "decimal": "1"},
                            "max_aspect": {"kind": "integer", "decimal": "2"}}},
            "defaults_version": "synthetic-defaults", "canonicalization_version": "synthetic-canonical",
            "algorithm_version": "synthetic-algorithm", "producer_build_id": "synthetic-build",
            "payload_sha256": "b"*64, "verification": "verified"}


def envelope():
    # Schema/orchestration example, NOT real provenance or a verified publication.
    return {"schema_version": "analytics.v1", "descriptor": descriptor(),
      "provenance": {"submitted_config": {"image_cfg": None, "label_cfg": None},
        "validation_attempt_id": "synthetic-attempt", "validation_manifest_sha256": "c"*64,
        "validation_index_sha256": "d"*64, "producer_source_commit": "synthetic-not-a-commit",
        "dependency_digest": "e"*64},
      "payload": {"dataset_id": "dataset-a", "quality_rows": [{"filename": "synthetic.png", "status": "ERROR",
        "score": 0, "issues": [], "width": 0, "height": 0, "size_kb": 0,
        **{key: None for key in ("blur_score", "brightness", "contrast", "noise_score", "black_pct", "white_pct")},
        "error": "synthetic"}], "audit_rows": [], "classes": [], "attribute_distribution": [],
        "distribution_images": [], "distribution_boxes": [], "attributes": []}}


class FakeResults:
    def __init__(self):
        self.listed, self.detail = [descriptor()], envelope()
        self.calls = []
    def list_results(self, dataset_id):
        self.calls.append(("list", dataset_id))
        return copy.deepcopy(self.listed)
    def read_result(self, dataset_id, result_id):
        self.calls.append(("detail", dataset_id, result_id))
        return copy.deepcopy(self.detail)
