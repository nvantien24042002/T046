"""Pure Label Check A+B audit module.

Extracts geometry and visual audit algorithms from app.py, independent of Streamlit UI.
Supports streaming bounded iteration directly from a seekable archive reader.
"""
from __future__ import annotations

from typing import Any, Callable
import zipfile

import cv2
import numpy as np
import pandas as pd

DEFAULT_LABEL_CFG: dict[str, Any] = {
    "min_area_pct": 0.01,
    "max_area_pct": 80.0,
    "min_aspect": 0.15,
    "max_aspect": 7.0,
    "duplicate_iou": 0.90,
    "min_edge": 0.02,
    "min_texture": 100.0,
    "min_sharpness": 50.0,
    "min_contrast": 12.0,
    "min_boundary": 0.02,
}


def iou(a: list[float], b: list[float]) -> float:
    """Calculate intersection over union for two bounding boxes [xtl, ytl, xbr, ybr]."""
    xx1, yy1 = max(a[0], b[0]), max(a[1], b[1])
    xx2, yy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, xx2 - xx1) * max(0.0, yy2 - yy1)
    aa = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    bb = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    denom = aa + bb - inter
    return inter / denom if denom > 0 else 0.0


def visual_metrics(image: np.ndarray, coords: list[float]) -> dict[str, float] | None:
    """Calculate visual signals (edge, texture, sharpness, contrast, boundary) for a box ROI."""
    H, W = image.shape[:2]
    x1, y1, x2, y2 = coords
    x1 = max(0, int(np.floor(x1)))
    y1 = max(0, int(np.floor(y1)))
    x2 = min(W, int(np.ceil(x2)))
    y2 = min(H, int(np.ceil(y2)))

    if x2 - x1 < 8 or y2 - y1 < 8:
        return None

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    roi = gray[y1:y2, x1:x2]

    edge = cv2.Canny(roi, 50, 150)
    density = float(np.mean(edge > 0))
    texture = float(np.var(roi.astype(np.float32)))
    sharpness = float(cv2.Laplacian(roi, cv2.CV_64F).var())

    margin = max(4, int(0.25 * max(x2 - x1, y2 - y1)))
    ox1, oy1 = max(0, x1 - margin), max(0, y1 - margin)
    ox2, oy2 = min(W, x2 + margin), min(H, y2 + margin)
    outer = lab[oy1:oy2, ox1:ox2].astype(np.float32)
    mask = np.ones(outer.shape[:2], dtype=bool)
    mask[y1 - oy1:y2 - oy1, x1 - ox1:x2 - ox1] = False
    inside = lab[y1:y2, x1:x2].astype(np.float32)

    contrast = float(np.linalg.norm(inside.mean(axis=(0, 1)) - outer[mask].mean(axis=0))) if np.any(mask) else np.nan

    band = min(4, max(1, min(x2 - x1, y2 - y1) // 8))
    perimeter_mask = np.zeros(roi.shape, dtype=bool)
    perimeter_mask[:band, :] = True
    perimeter_mask[-band:, :] = True
    perimeter_mask[:, :band] = True
    perimeter_mask[:, -band:] = True
    boundary = float(np.mean(edge[perimeter_mask] > 0))

    return {
        "edge_density": density,
        "texture_variance": texture,
        "laplacian_variance": sharpness,
        "foreground_contrast": contrast,
        "boundary_support": boundary,
    }


def run_label_audit(
    archive_source: Any,
    images: pd.DataFrame,
    boxes: pd.DataFrame,
    cfg: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int], None] | None = None
) -> pd.DataFrame:
    """Audit geometry and visual signals across dataset boxes using bounded per-image decode."""
    if boxes.empty:
        return pd.DataFrame()

    c = {**DEFAULT_LABEL_CFG, **(cfg or {})}
    results: list[dict[str, Any]] = []
    image_records = images.to_dict("records")
    total_images = len(image_records)

    with zipfile.ZipFile(archive_source) as z:
        for pos, img in enumerate(image_records):
            image_boxes = boxes[boxes.image_id == img["image_id"]].to_dict("records")
            image = None
            image_error = None

            if img.get("image_path") is not None:
                try:
                    arr = np.frombuffer(z.read(img["image_path"]), dtype=np.uint8)
                    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                    if image is None:
                        image_error = "Không đọc được ảnh"
                except Exception as e:
                    image_error = str(e)
            else:
                image_error = "Ảnh không có trong ZIP"

            H = image.shape[0] if image is not None else img.get("height", 0)
            W = image.shape[1] if image is not None else img.get("width", 0)

            valid: dict[str, bool] = {}
            for b in image_boxes:
                coords = [b[k] for k in ("xtl", "ytl", "xbr", "ybr")]
                valid[b["box_id"]] = bool(np.all(np.isfinite(coords)) and coords[2] > coords[0] and coords[3] > coords[1])

            duplicate: set[str] = set()
            for i, a in enumerate(image_boxes):
                if not valid[a["box_id"]]:
                    continue
                for b_other in image_boxes[i + 1:]:
                    if not valid[b_other["box_id"]]:
                        continue
                    if a["label"] == b_other["label"] and iou(
                        [a[k] for k in ("xtl", "ytl", "xbr", "ybr")],
                        [b_other[k] for k in ("xtl", "ytl", "xbr", "ybr")]
                    ) >= c["duplicate_iou"]:
                        duplicate.update((a["box_id"], b_other["box_id"]))

            for b in image_boxes:
                flags_a: list[str] = []
                flags_b: list[str] = []
                metrics = {
                    "edge_density": np.nan,
                    "texture_variance": np.nan,
                    "laplacian_variance": np.nan,
                    "foreground_contrast": np.nan,
                    "boundary_support": np.nan,
                }
                coords = [b[k] for k in ("xtl", "ytl", "xbr", "ybr")]
                area_pct = np.nan
                aspect = np.nan

                if not valid[b["box_id"]]:
                    flags_a.append("INVALID_BOX")
                else:
                    bw = coords[2] - coords[0]
                    bh = coords[3] - coords[1]
                    aspect = bw / bh
                    if W > 0 and H > 0:
                        area_pct = 100 * bw * bh / (W * H)
                        if coords[0] < 0 or coords[1] < 0 or coords[2] > W or coords[3] > H:
                            flags_a.append("OUT_OF_BOUNDS")
                        if area_pct < c["min_area_pct"]:
                            flags_a.append("SMALL_BOX")
                        if area_pct > c["max_area_pct"]:
                            flags_a.append("LARGE_BOX")
                    if aspect < c["min_aspect"] or aspect > c["max_aspect"]:
                        flags_a.append("ASPECT_RATIO_OUTLIER")
                    if b["box_id"] in duplicate:
                        flags_a.append("POSSIBLE_DUPLICATE")

                    if image is not None:
                        visual = visual_metrics(image, coords)
                        if visual is None:
                            flags_b.append("ROI_TOO_SMALL")
                        else:
                            metrics.update(visual)
                            if visual["edge_density"] < c["min_edge"]:
                                flags_b.append("LOW_EDGE_DENSITY")
                            if visual["texture_variance"] < c["min_texture"]:
                                flags_b.append("LOW_TEXTURE")
                            if visual["laplacian_variance"] < c["min_sharpness"]:
                                flags_b.append("LOW_SHARPNESS")
                            if np.isfinite(visual["foreground_contrast"]) and visual["foreground_contrast"] < c["min_contrast"]:
                                flags_b.append("LOW_FOREGROUND_CONTRAST")
                            if visual["boundary_support"] < c["min_boundary"]:
                                flags_b.append("WEAK_BOUNDARY_SUPPORT")
                            if all(k in flags_b for k in ("LOW_EDGE_DENSITY", "LOW_TEXTURE", "LOW_FOREGROUND_CONTRAST")):
                                flags_b.append("POSSIBLE_EMPTY_BOX")

                if ("INVALID_BOX" in flags_a or "OUT_OF_BOUNDS" in flags_a
                        or "POSSIBLE_DUPLICATE" in flags_a or "POSSIBLE_EMPTY_BOX" in flags_b):
                    priority = "HIGH"
                elif flags_a or any(x not in ("LOW_SHARPNESS", "ROI_TOO_SMALL") for x in flags_b):
                    priority = "MEDIUM"
                else:
                    priority = "LOW"

                status = "REVIEW" if (flags_a or any(x != "ROI_TOO_SMALL" for x in flags_b)) else "NO_SIGNAL"
                if image is None:
                    status = "A_ONLY" if status == "NO_SIGNAL" else "REVIEW_A_ONLY"

                results.append({
                    **b,
                    "image_available": image is not None,
                    "image_error": image_error,
                    "area_pct": area_pct,
                    "aspect_ratio": aspect,
                    **metrics,
                    "geometry_issues": ", ".join(flags_a),
                    "visual_issues": ", ".join(flags_b),
                    "issues": ", ".join(flags_a + flags_b),
                    "issue_count": len(flags_a) + len(flags_b),
                    "priority": priority,
                    "status": status,
                })

            if progress_callback:
                progress_callback(pos + 1, total_images)

    return pd.DataFrame(results)
