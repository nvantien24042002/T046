"""Pure Image Quality QA/QC analysis module.

Extracts image quality assessment algorithms from app.py, independent of Streamlit UI.
Supports analyzing single images from raw bytes or streaming bounded iterations
directly from a seekable archive reader.
"""
from __future__ import annotations

from typing import Any, Callable
import sqlite3
import zipfile

import cv2
import numpy as np
import pandas as pd

DEFAULT_IMAGE_CFG: dict[str, Any] = {
    "blur_threshold": 100.0,
    "dark_threshold": 40,
    "bright_threshold": 215,
    "contrast_threshold": 25.0,
    "noise_threshold": 10.0,
    "min_width": 320,
    "min_height": 240,
    "black_threshold": 20,
    "white_threshold": 245,
}


def estimate_noise(gray: np.ndarray) -> float:
    """Estimate noise via median absolute deviation (MAD) on flat areas."""
    gray_float = gray.astype(np.float32)
    smooth = cv2.GaussianBlur(gray_float, (3, 3), 0)
    residual = gray_float - smooth

    gx = cv2.Sobel(gray_float, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray_float, cv2.CV_32F, 0, 1, ksize=3)
    gradient = cv2.magnitude(gx, gy)

    flat_mask = gradient < 15
    if np.count_nonzero(flat_mask) < 100:
        return float("nan")

    values = residual[flat_mask]
    median = np.median(values)
    mad = np.median(np.abs(values - median))
    return float(1.4826 * mad)


def analyze_image(
    image_input: bytes | Any,
    cfg: dict[str, Any] | None = None,
    filename: str = "image",
    retain_bytes: bool = False
) -> dict[str, Any]:
    """Analyze a single image and return quality metrics and PASS/REVIEW/ERROR status.
    
    Accepts raw bytes, or an object with getvalue() or read() method.
    """
    c = {**DEFAULT_IMAGE_CFG, **(cfg or {})}
    image_bytes = image_input.getvalue() if hasattr(image_input, "getvalue") else image_input

    try:
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("Không thể đọc hoặc giải mã ảnh.")

        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        blur_score = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))
        noise_score = estimate_noise(gray)

        black_pct = float(np.mean(gray < c["black_threshold"]) * 100)
        white_pct = float(np.mean(gray > c["white_threshold"]) * 100)

        issues: list[str] = []
        if blur_score < c["blur_threshold"]:
            issues.append("BLUR")
        if brightness < c["dark_threshold"]:
            issues.append("DARK")
        if brightness > c["bright_threshold"]:
            issues.append("OVEREXPOSED")
        if contrast < c["contrast_threshold"]:
            issues.append("LOW_CONTRAST")
        if width < c["min_width"] or height < c["min_height"]:
            issues.append("LOW_RESOLUTION")
        if np.isfinite(noise_score) and noise_score > c["noise_threshold"]:
            issues.append("POSSIBLE_HIGH_NOISE")

        score = 100
        score -= 20 if "BLUR" in issues else 0
        score -= 15 if "DARK" in issues else 0
        score -= 15 if "OVEREXPOSED" in issues else 0
        score -= 15 if "LOW_CONTRAST" in issues else 0
        score -= 15 if "LOW_RESOLUTION" in issues else 0
        score -= 15 if "POSSIBLE_HIGH_NOISE" in issues else 0
        score = max(0, score)

        status = "REVIEW" if issues else "PASS"

        res: dict[str, Any] = {
            "filename": filename,
            "status": status,
            "score": score,
            "issues": issues,
            "width": width,
            "height": height,
            "size_kb": len(image_bytes) / 1024,
            "blur_score": blur_score,
            "brightness": brightness,
            "contrast": contrast,
            "noise_score": noise_score,
            "black_pct": black_pct,
            "white_pct": white_pct,
            "error": "",
        }
        if retain_bytes:
            res["image_bytes"] = image_bytes
        return res

    except Exception as exc:
        res = {
            "filename": filename,
            "status": "ERROR",
            "score": 0,
            "issues": ["READ_ERROR"],
            "width": 0,
            "height": 0,
            "size_kb": 0,
            "blur_score": float("nan"),
            "brightness": float("nan"),
            "contrast": float("nan"),
            "noise_score": float("nan"),
            "black_pct": float("nan"),
            "white_pct": float("nan"),
            "error": str(exc),
        }
        if retain_bytes:
            res["image_bytes"] = image_bytes
        return res


def run_image_quality(
    archive_file: Any,
    index_conn: sqlite3.Connection,
    cfg: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int], None] | None = None,
    retain_bytes: bool = False
) -> pd.DataFrame:
    """Run Image Quality analysis over indexed images using bounded streaming reads.
    
    Decodes one image at a time from archive_file; does not accumulate image buffers.
    """
    img_names = [r[0] for r in index_conn.execute("SELECT name FROM entries WHERE kind='image' ORDER BY id").fetchall()]
    total = len(img_names)
    rows: list[dict[str, Any]] = []

    with zipfile.ZipFile(archive_file) as z:
        for pos, name in enumerate(img_names):
            try:
                data = z.read(name)
                res = analyze_image(data, cfg, filename=name, retain_bytes=retain_bytes)
            except Exception as e:
                res = {
                    "filename": name,
                    "status": "ERROR",
                    "score": 0,
                    "issues": ["READ_ERROR"],
                    "width": 0,
                    "height": 0,
                    "size_kb": 0,
                    "blur_score": float("nan"),
                    "brightness": float("nan"),
                    "contrast": float("nan"),
                    "noise_score": float("nan"),
                    "black_pct": float("nan"),
                    "white_pct": float("nan"),
                    "error": str(e),
                }
                if retain_bytes:
                    res["image_bytes"] = b""
            rows.append(res)
            if progress_callback:
                progress_callback(pos + 1, total)

    return pd.DataFrame(rows)
