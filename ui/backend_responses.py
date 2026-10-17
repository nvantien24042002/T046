"""Validate adapter JSON boundaries without exposing response contents to the UI."""
import math
import re


class InvalidResponse(ValueError):
    pass


def _require(condition):
    if not condition:
        raise InvalidResponse("Invalid backend response structure")


def _string(value):
    return isinstance(value, str) and bool(value)


def _integer(value, minimum=0):
    return type(value) is int and value >= minimum


def _number(value):
    return type(value) in (int, float)


def _analysis_rows(data):
    # These fields come from the current pipeline producers and FE consumers.
    # Nullable metrics are intentional: pandas serializes unavailable signals as null.
    schemas = {
        "quality_rows": {
            "filename": _string, "status": lambda v: isinstance(v, str) and v in {"PASS", "REVIEW", "ERROR"},
            "score": lambda v: _number(v) and 0 <= v <= 100,
            "issues": lambda v: isinstance(v, list) and all(isinstance(s, str) for s in v),
            **{k: lambda v: v is None or _number(v) for k in ("blur_score", "brightness", "contrast")},
        },
        "audit_rows": {
            **{k: lambda v: isinstance(v, str) for k in ("box_id", "image_name", "label", "status", "priority", "issues")},
            "issue_count": _integer,
            **{k: lambda v: v is None or _number(v) for k in ("area_pct", "aspect_ratio", "edge_density", "foreground_contrast")},
        },
        "classes": {
            "class": lambda v: isinstance(v, str), "boxes": _integer, "images": _integer,
            "share_pct": lambda v: _number(v) and 0 <= v <= 100,
        },
        "attribute_distribution": {
            **{k: lambda v: isinstance(v, str) for k in ("attribute", "value", "label")},
            "count": _integer,
        },
        "distribution_images": {
            **{k: lambda v: isinstance(v, str) for k in ("image_name", "weather", "timeofday")},
        },
        "distribution_boxes": {
            "label": lambda v: isinstance(v, str), "occluded": lambda v: isinstance(v, str),
            "valid_geometry": lambda v: type(v) is bool,
            "area_pct": lambda v: v is None or _number(v),
        },
    }
    for kind, fields in schemas.items():
        for row in data[kind]:
            for key, check in fields.items():
                _require(key in row and check(row[key]))


def _json_value(value):
    if isinstance(value, float):
        _require(math.isfinite(value))
    elif isinstance(value, dict):
        for item in value.values():
            _json_value(item)
    elif isinstance(value, list):
        for item in value:
            _json_value(item)


def validate_response(data, kind):
    _require(isinstance(data, dict))
    _json_value(data)
    if kind in {"create", "upload"}:
        _require(_string(data.get("upload_id")))
        if kind == "create":
            _require(_string(data.get("upload_capability")))
        required = kind == "upload"
        for key, check in {
            "filename": _string, "size_bytes": lambda v: _integer(v, 1),
            "received_bytes": _integer, "chunk_size_bytes": lambda v: _integer(v, 1),
            "state": lambda v: isinstance(v, str) and v in {"created", "uploading", "uploaded", "cancelled", "expired"},
        }.items():
            if required or key in data:
                _require(check(data.get(key)))
        if "received_bytes" in data and "size_bytes" in data:
            _require(data["received_bytes"] <= data["size_bytes"])
        for key in ("ready", "validated", "durable", "cleanup_pending", "replayed"):
            if key in data:
                _require(type(data[key]) is bool)
    elif kind in {"dataset", "summary"}:
        _require(_string(data.get("upload_id")))
        _require(isinstance(data.get("state"), str) and data["state"] in {
            "not_requested", "validating", "indexing", "publishing", "ready", "failed", "cancelled",
        })
        _require(type(data.get("ready")) is bool and data["ready"] == (data["state"] == "ready"))
        if data["state"] != "not_requested" or kind == "summary":
            _require(_string(data.get("dataset_id")))
        if kind == "summary" and data["ready"]:
            for key in ("archive_bytes", "entries", "images"):
                _require(_integer(data.get(key)))
            _require(_string(data.get("archive_sha256")))
            _require(type(data.get("annotations")) is bool)
            _require(isinstance(data.get("capabilities"), dict))
            _require(all(type(v) is bool for v in data["capabilities"].values()))
    elif kind == "analysis":
        _require(_string(data.get("dataset_id")))
        for key in ("quality_rows", "audit_rows", "classes", "attribute_distribution", "distribution_images", "distribution_boxes"):
            _require(isinstance(data.get(key), list) and all(isinstance(row, dict) for row in data[key]))
        # Validation rejects NO_IMAGES, and the pipeline emits one quality row
        # even for an unreadable image; the dashboard requires these columns.
        _require(bool(data["quality_rows"]))
        _require(isinstance(data.get("attributes"), list) and all(isinstance(v, str) for v in data["attributes"]))
        _analysis_rows(data)
        # Older analysis bundles predate request_fingerprint; never invent one.
        if "request_fingerprint" in data:
            _require(isinstance(data["request_fingerprint"], str) and re.fullmatch(r"[0-9a-f]{64}", data["request_fingerprint"]) is not None)
    elif kind == "receipts":
        _require(isinstance(data.get("receipts"), list))
        _require(_integer(data.get("next_index")))
        for row in data["receipts"]:
            _require(isinstance(row, dict))
            for key in ("chunk_index", "offset_bytes", "length_bytes"):
                _require(_integer(row.get(key), 1 if key == "length_bytes" else 0))
            _require(isinstance(row.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None)
        if data["receipts"]:
            _require(data["next_index"] == data["receipts"][-1]["chunk_index"] + 1)
    else:
        raise InvalidResponse("Unknown backend response contract")
    return data
