"""Explicit baseline capture against original, hash-verified sources only.

Run once before refactoring: python -m tests.baseline.capture
Do not regenerate expected outputs to hide regressions.
"""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

from core.coverage_dynamic import evaluate_targets
from core.distribution import parse_distribution, class_distribution, attribute_distribution
from tests.fixtures import ROOT, legacy_functions, make_zip, source_digest

IMAGE_CFG = dict(blur_threshold=100.0, dark_threshold=40, bright_threshold=215,
                 contrast_threshold=25.0, noise_threshold=10.0, min_width=320,
                 min_height=240, black_threshold=20, white_threshold=245)
LABEL_CFG = dict(min_area_pct=0.01, max_area_pct=80.0, min_aspect=0.15,
                 max_aspect=7.0, duplicate_iou=0.90, min_edge=0.02,
                 min_texture=100.0, min_sharpness=50.0, min_contrast=12.0,
                 min_boundary=0.02)


def verify_sources() -> None:
    for item in json.loads((ROOT / "tests/baseline/source_hashes.json").read_text(encoding="utf-8-sig")):
        assert source_digest(ROOT / item["path"]) == item["sha256"], item["path"]


def capture(path: Path) -> dict:
    legacy = legacy_functions()
    # Intentional tiny fixture through the unmodified legacy byte API.
    data = path.read_bytes()
    images, skipped = legacy.read_images_from_zip(data)
    cvat_images, boxes = legacy.load_cvat(data)
    import pandas as pd
    quality = pd.DataFrame([{k: v for k, v in legacy.analyze_image(image, IMAGE_CFG).items()
                             if k != "image_bytes"} for image in images])
    audit = legacy.audit(data, cvat_images, boxes, LABEL_CFG)
    di, db, attrs = parse_distribution(data)
    coverage = evaluate_targets(di, db, [dict(name="cars", filters={"class": "car"}, target=2),
                                       dict(name="rain", filters={"weather": "rain"}, target=2)])
    finder = legacy_functions("core/scene_finder.py")
    def records(frame):
        return json.loads(frame.to_json(orient="records", double_precision=10))
    return dict(skipped=skipped, quality=records(quality), audit=records(audit),
                cvat_images=records(cvat_images), cvat_boxes=records(boxes),
                distribution_images=records(di), distribution_boxes=records(db),
                classes=records(class_distribution(db)), attributes=records(attribute_distribution(di, db, attrs)),
                coverage=records(coverage),
                resolver=[finder._resolve("flat.png", [i.name for i in images]),
                          finder._resolve("a.png", ["a/a.png", "b/a.png"])],
                csv_headers=dict(quality=",".join(quality.columns), audit=",".join(audit.columns)))


if __name__ == "__main__":
    verify_sources()
    destination = ROOT / "tests/baseline/expected.json"
    if destination.exists():
        raise SystemExit("Baseline exists; refusing to overwrite.")
    with tempfile.TemporaryDirectory(prefix="cvat-baseline-") as folder:
        result = capture(make_zip(Path(folder) / "fixture.zip"))
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("Captured original synthetic fixture outputs; no application module import/UI execution.")
