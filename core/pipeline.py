"""End-to-end dataset analysis pipeline consuming DatasetManager.reader.

Coordinates disk-backed CVAT indexing, bounded image quality analysis, label audit,
distributions, and preview generation without loading full archives or uncompressed
datasets into memory.
"""
from __future__ import annotations

import io
import math
from typing import Any, Callable
import zipfile

import cv2
import numpy as np
import pandas as pd
from PIL import Image

from core.cvat_parser import load_cvat_from_index, parse_distribution_from_index
from core.distribution import class_distribution, attribute_distribution
from core.image_quality import run_image_quality, DEFAULT_IMAGE_CFG
from core.label_audit import run_label_audit, DEFAULT_LABEL_CFG


def execute_pipeline(
    reader: dict[str, Any],
    image_cfg: dict[str, Any] | None = None,
    label_cfg: dict[str, Any] | None = None,
    quality_callback: Callable[[int, int], None] | None = None,
    audit_callback: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Execute complete QA/QC pipeline on an active leased reader.
    
    `reader` is the dictionary yielded by `DatasetManager.reader()` containing:
      - 'summary': manifest dict
      - 'archive': seekable binary file handle (payload.part)
      - 'index': sqlite3.Connection to readonly index.sqlite3
    """
    manifest = reader["summary"]
    archive_file = reader["archive"]
    index_conn = reader["index"]

    has_annotations = bool(manifest.get("annotations", False))

    # 1. Image Quality (runs on all datasets with images)
    quality_df = run_image_quality(
        archive_file,
        index_conn,
        cfg=image_cfg,
        progress_callback=quality_callback,
        retain_bytes=False,
    )

    # 2. Annotations & Distributions
    if has_annotations:
        cvat_images, cvat_boxes = load_cvat_from_index(index_conn)
        dist_images, dist_boxes, attrs = parse_distribution_from_index(index_conn)
        audit_df = run_label_audit(
            archive_file,
            cvat_images,
            cvat_boxes,
            cfg=label_cfg,
            progress_callback=audit_callback,
        )
        classes_df = class_distribution(dist_boxes)
        attr_dist_df = attribute_distribution(dist_images, dist_boxes, attrs)
    else:
        cvat_images = pd.DataFrame(columns=["image_id", "image_name", "image_path", "width", "height"])
        cvat_boxes = pd.DataFrame(columns=["image_id", "image_name", "box_id", "label", "occluded", "xtl", "ytl", "xbr", "ybr"])
        dist_images = pd.DataFrame(columns=["image_id", "image_name", "weather", "timeofday"])
        dist_boxes = pd.DataFrame(columns=["image_id", "image_name", "box_id", "label", "occluded", "valid_geometry", "area_pct", "attributes"])
        attrs = []
        audit_df = pd.DataFrame()
        classes_df = pd.DataFrame(columns=["class", "boxes", "images", "share_pct"])
        attr_dist_df = pd.DataFrame(columns=["attribute", "value", "label", "count"])

    return {
        "manifest": manifest,
        "quality": quality_df,
        "audit": audit_df,
        "cvat_images": cvat_images,
        "cvat_boxes": cvat_boxes,
        "distribution_images": dist_images,
        "distribution_boxes": dist_boxes,
        "attributes": attrs,
        "classes": classes_df,
        "attribute_distribution": attr_dist_df,
    }


def read_single_image(
    archive_file: Any,
    image_path: str,
    max_bytes: int = 50 * 1024 * 1024
) -> bytes:
    """Read a single image member from archive with size guard."""
    with zipfile.ZipFile(archive_file) as z:
        info = z.getinfo(image_path)
        if info.file_size > max_bytes:
            raise ValueError(f"Image exceeds maximum size limit ({info.file_size} > {max_bytes})")
        return z.read(info)


def get_preview_image(
    reader: dict[str, Any],
    image_path: str,
    max_bytes: int = 50 * 1024 * 1024
) -> Image.Image:
    """Load and decode a single image for preview."""
    raw = read_single_image(reader["archive"], image_path, max_bytes=max_bytes)
    return Image.open(io.BytesIO(raw))


def _overlay_coordinates(
    box: dict[str, Any], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Validate and clip display coordinates without changing annotation data."""
    if width < 2 or height < 2:
        return None
    try:
        x1, y1, x2, y2 = (float(box[key]) for key in ("xtl", "ytl", "xbr", "ybr"))
        if not all(math.isfinite(v) for v in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
            return None
        # Clip before integer conversion: even finite floats may exceed OpenCV's
        # integer range. Keep the existing rounding for in-bounds coordinates.
        x1, x2 = (int(round(min(max(x, 0), width - 1))) for x in (x1, x2))
        y1, y2 = (int(round(min(max(y, 0), height - 1))) for y in (y1, y2))
        return (x1, y1, x2, y2) if x2 > x1 and y2 > y1 else None
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def get_annotated_preview_image(
    reader: dict[str, Any],
    image_path: str,
    boxes: list[dict[str, Any]],
    max_bytes: int = 50 * 1024 * 1024
) -> np.ndarray:
    """Load single image and draw annotated bounding boxes."""
    raw = read_single_image(reader["archive"], image_path, max_bytes=max_bytes)
    arr = np.frombuffer(raw, dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode preview image")

    height, width = image.shape[:2]
    for b in boxes:
        coords = _overlay_coordinates(b, width, height)
        if coords is None:
            continue
        x1, y1, x2, y2 = coords
        label = str(b.get("label", ""))
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        if label:
            cv2.putText(image, label, (x1, max(15, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    return image
