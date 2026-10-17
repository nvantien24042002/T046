"""Read-only dashboard projections. Business aggregates are computed on the server.

The source is an existing analysis, never an implicit analyze or mock fallback.
"""
from collections import Counter, defaultdict
from statistics import median
import math
from typing import Literal
import json
import pandas as pd
from core.coverage_dynamic import build_universe, choices, evaluate_targets, recommendations, generate_targets, DIMS
from pydantic import BaseModel, ConfigDict, Field, model_validator
from backend.analytics_result_contracts import Payload

Page = Literal["overview", "quality", "labels", "distribution", "review", "reports", "coverage", "scene-finder"]

class CoverageTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    name: str | None = Field(default=None, max_length=512)
    filters: dict[str, str]
    unit: Literal["images", "objects"] = "images"
    target: int = Field(gt=0)
    weight: float = Field(default=1.0, gt=0)

    @model_validator(mode="after")
    def valid_filters(self):
        if not self.filters or any(k not in DIMS or not v or len(v) > 512 for k,v in self.filters.items()):
            raise ValueError("Invalid slice filter")
        return self

class TargetGeneration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    dimensions: list[Literal["class", "weather", "timeofday", "occluded"]] = Field(min_length=1, max_length=4)
    unit: Literal["images", "objects"] = "images"
    target: int = Field(gt=0)
    weight: float = Field(default=1.0, gt=0)
    include_unobserved: bool = False

class DashboardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)
    weather: str | None = Field(default=None, max_length=512)
    timeofday: str | None = Field(default=None, max_length=512)
    keyword: str | None = Field(default=None, max_length=512)
    targets: list[CoverageTarget] | None = Field(default=None, max_length=250)
    generate: TargetGeneration | None = None
    class_label: str | None = Field(default=None, max_length=512)
    priority: Literal["HIGH", "MEDIUM", "LOW"] | None = None
    review_scope: Literal["review"] | None = None
    min_count: int | None = Field(default=None, ge=0)
    min_share: float | None = Field(default=None, ge=0, le=100)
    max_share: float | None = Field(default=None, ge=0, le=100)

    @model_validator(mode="after")
    def ordered_shares(self):
        if self.targets is not None and self.generate is not None: raise ValueError("Conflicting target sources")
        if (self.min_share if self.min_share is not None else 1) > (self.max_share if self.max_share is not None else 60):
            raise ValueError("Reversed share limits")
        return self

    quality_status: Literal["PASS", "REVIEW", "ERROR"] | None = None

class DashboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    page: Page
    expected_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_payload_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    query: DashboardQuery = Field(default_factory=DashboardQuery)

def metric(key, label, value):
    return {"key": key, "label": label, "value": value}

def table(key, title, rows, filename=None):
    return {"key": key, "title": title, "rows": rows, "export_filename": filename}

def chart(key, title, counts):
    return {"key": key, "title": title, "rows": [{"name": name, "value": count} for name, count in sorted(counts.items())]}

def project_dashboard(request, payload, summary):
    parsed = Payload.model_validate(payload).model_dump(by_alias=True)
    if parsed["dataset_id"] != summary["dataset_id"]:
        raise ValueError("DATASET_MISMATCH")
    result = {"schema_version": "dashboard.v1", "dataset_id": parsed["dataset_id"],
              "page": request.page, "state": "ready", "reason": None,
              "metrics": [], "tables": [], "charts": [], "controls": [], "notes": []}
    quality, audit = parsed["quality_rows"], parsed["audit_rows"]
    capability = {"quality": "image_quality", "labels": "label_audit", "distribution": "distribution", "coverage": "coverage", "scene-finder": "scene_finder"}.get(request.page)
    available = summary.get("capabilities", {}).get(capability) is True
    if capability and not available:
        result.update(state="unsupported", reason="Backend không hỗ trợ dữ liệu này cho dataset đã chọn.")
        return result
    if request.page == "quality":
        rows = [r for r in quality if request.query.quality_status is None or r["status"] == request.query.quality_status]
        counts = Counter(r["status"] for r in rows)
        issues = Counter(issue for row in rows for issue in row["issues"])
        result["metrics"] = [metric("images", "Ảnh trong bộ lọc", len(rows)),
            *[metric(status, status, counts[status]) for status in ("PASS", "REVIEW", "ERROR")],
            metric("mean_score", "Điểm trung bình", round(sum(r["score"] for r in rows) / len(rows), 1) if rows else None)]
        result["tables"] = [table("quality", "Image Quality", rows, "image_quality.csv")]
        result["charts"] = [chart("statuses", "Trạng thái ảnh", counts), chart("issues", "Tần suất cảnh báo", issues)]
        result["controls"] = [{"key": "quality_status", "label": "Trạng thái ảnh", "choices": ["PASS", "REVIEW", "ERROR"], "value": request.query.quality_status}]
        config = payload.get("effective_config", {}).get("image_cfg")
        if config is not None:
            result["tables"].append(table("thresholds", "Ngưỡng thực thi từ backend", [{"parameter": k, "value": v} for k, v in config.items()]))
        else:
            result["notes"].append("Kết quả chưa có effective configuration; không suy diễn ngưỡng mặc định.")
        if not rows: result.update(state="empty", reason="Không có ảnh khớp bộ lọc.")
        return result
    if request.page == "labels":
        rows = [r for r in audit if (request.query.class_label is None or r["label"] == request.query.class_label)
            and (request.query.priority is None or r["priority"] == request.query.priority)
            and (request.query.review_scope is None or r["status"].startswith("REVIEW"))]
        groups = []
        grouped = defaultdict(list)
        for row in rows: grouped[row["label"]].append(row)
        for label, group in sorted(grouped.items()):
            reviews = sum(r["status"].startswith("REVIEW") for r in group)
            groups.append({"label": label, "boxes": len(group), "reviews": reviews,
                "images": len({r["image_name"] for r in group}), "review_rate_pct": round(100 * reviews / len(group), 1)})
        result["metrics"] = [metric("boxes", "Tổng box trong bộ lọc", len(rows)),
            metric("reviews", "Box cần review", sum(r["status"].startswith("REVIEW") for r in rows)),
            metric("high", "HIGH cần review", sum(r["priority"] == "HIGH" and r["status"].startswith("REVIEW") for r in rows)),
            metric("available", "Box có ảnh kiểm tra B", sum(r["image_available"] for r in rows))]
        result["tables"] = [table("audit", "Label Check A+B", rows, "label_check.csv"), table("label_classes", "Review theo class", groups, "class_review.csv")]
        result["charts"] = [chart("issues", "Tần suất cảnh báo", Counter(issue.strip() for row in rows for issue in row["issues"].split(",") if issue.strip()))]
        result["controls"] = [
            {"key": "class_label", "label": "Class", "choices": sorted({r["label"] for r in audit}), "value": request.query.class_label},
            {"key": "priority", "label": "Priority", "choices": ["HIGH", "MEDIUM", "LOW"], "value": request.query.priority},
            {"key": "review_scope", "label": "Phạm vi review", "choices": ["review"], "value": request.query.review_scope}]
        if not rows: result.update(state="empty", reason="Không có box khớp bộ lọc.")
        return result
    if request.page == "distribution":
        boxes, images = parsed["distribution_boxes"], parsed["distribution_images"]
        # Same policy defaults as the existing Streamlit distribution controls.
        minimum = request.query.min_count if request.query.min_count is not None else 30
        lower = request.query.min_share if request.query.min_share is not None else 1
        upper = request.query.max_share if request.query.max_share is not None else 60
        classes = parsed["classes"]
        alerts = [r for r in classes if r["boxes"] < minimum or r["share_pct"] < lower or r["share_pct"] > upper]
        sizes = []
        values_by_class = defaultdict(list)
        for row in boxes:
            if row["valid_geometry"] and row["area_pct"] is not None and math.isfinite(row["area_pct"]): values_by_class[row["label"]].append(row["area_pct"])
        for label, values in sorted(values_by_class.items()):
            if values: sizes.append({"label": label, "count": len(values), "mean": sum(values)/len(values), "median": median(values), "min": min(values), "max": max(values)})
        occlusion = Counter((r["label"], r["occluded"]) for r in boxes)
        scenes = Counter((r["weather"], r["timeofday"]) for r in images)
        result["metrics"] = [metric("images", "Ảnh có annotation record", len(images)), metric("boxes", "Tổng box", len(boxes)),
            metric("classes", "Số class", len(classes)), metric("attributes", "Thuộc tính phát hiện", len(parsed["attributes"]))]
        result["tables"] = [table("classes", "Class distribution", classes, "class_distribution.csv"),
            table("attributes", "Attribute distribution", parsed["attribute_distribution"], "attribute_distribution.csv"),
            table("imbalance", "Cảnh báo mất cân bằng", alerts), table("sizes", "Thống kê diện tích box", sizes),
            table("occlusion", "Occlusion theo class", [{"label": k[0], "occluded": k[1], "boxes": n} for k,n in sorted(occlusion.items())]),
            table("scenes", "Weather × Time of day", [{"weather": k[0], "timeofday": k[1], "images": n} for k,n in sorted(scenes.items())]),
            table("boxes", "Box distribution", boxes, "box_distribution.csv")]
        result["charts"] = [chart("classes", "Phân bố class", {r["class"]: r["boxes"] for r in classes}),
            chart("weather", "Weather", Counter(r["weather"] for r in images)), chart("timeofday", "Time of day", Counter(r["timeofday"] for r in images))]
        result["parameters"] = [{"key": "min_count", "label": "Số box tối thiểu", "value": minimum},
            {"key": "min_share", "label": "Tỷ lệ tối thiểu (%)", "value": lower},
            {"key": "max_share", "label": "Tỷ lệ tối đa (%)", "value": upper}]
        result["notes"].append("Ngưỡng và cảnh báo do backend xử lý; đây không phải kết luận annotation sai.")
        return result
    if request.page == "review":
        paths = [r["filename"] for r in quality]
        quality_enabled = summary.get("capabilities", {}).get("image_quality") is True
        audit_enabled = summary["annotations"] and summary.get("capabilities", {}).get("label_audit") is True
        if quality_enabled:
            result["tables"] = [table("preview_images", "Ảnh xem trước", [
                {"image_name": r["filename"], "image_path": r["filename"], "status": r["status"], "score": r["score"]} for r in quality]),
                table("quality", "Image Quality", quality)]
        else:
            result["notes"].append("Image Quality chưa được backend cho phép; không tạo dữ liệu thay thế.")
        if audit_enabled:
            rows = [{**r, "image_path": canonical_image_path(r["image_name"], paths) if quality_enabled else None} for r in audit]
            result["tables"].append(table("audit", "Bounding box và tín hiệu QA/QC", rows))
        else:
            result["notes"].append("Label Check chưa được backend cho phép; không tạo dữ liệu thay thế.")
        if not result["tables"]:
            result.update(state="unsupported", reason="Không có capability review được backend cho phép.")
        result["notes"] += ["Overlay chỉ hiển thị geometry CVAT; màu box không biểu thị priority.",
            "Ghi quyết định QA và chỉnh annotation chưa được hỗ trợ qua API này."]
        return result
    if request.page == "reports":
        if summary.get("capabilities", {}).get("image_quality") is True:
            result["tables"].append(table("quality", "Image Quality", quality, "image_quality_report.csv"))
        if summary["annotations"] and summary.get("capabilities", {}).get("label_audit") is True:
            label_request = DashboardRequest(page="labels")
            label_view = project_dashboard(label_request, payload, summary)
            result["tables"] += [table("audit", "Label Check A+B", audit, "label_check_report.csv"),
                table("classes", "Class Review Summary", label_view["tables"][1]["rows"], "class_review_summary.csv")]
        if summary["annotations"] and summary.get("capabilities", {}).get("distribution") is True:
            result["tables"].append(table("boxes", "Box Distribution", parsed["distribution_boxes"], "box_distribution.csv"))
        if not result["tables"]:
            result.update(state="unsupported", reason="Không có capability báo cáo được backend cho phép.")
        result["notes"].append("Nội dung báo cáo do backend tổng hợp; trình duyệt chỉ mã hóa file CSV.")
        return result
    if request.page == "coverage":
        images = pd.DataFrame(parsed["distribution_images"], columns=["image_id", "image_name", "weather", "timeofday"])
        boxes = pd.DataFrame(parsed["distribution_boxes"], columns=["image_id", "image_name", "box_id", "label", "occluded", "valid_geometry", "area_pct", "attributes"])
        if images.empty:
            result.update(state="empty", reason="Không có image record trong annotation để tính coverage.")
            return result
        universe = build_universe(images, boxes)
        class_rows = []
        if not universe.empty:
            for label, group in universe.groupby("class"):
                class_rows.append({"class": label, "objects": len(group), "images": group.image_id.nunique(),
                    "image_coverage_pct": round(100 * group.image_id.nunique()/len(images), 2),
                    "object_share_pct": round(100 * len(group)/len(universe), 2)})
        result["tables"] = [table("observed_coverage", "Coverage quan sát theo class", class_rows, "observed_coverage.csv")]
        result["can_submit_targets"] = True
        result["notes"] += ["Chỉ đếm object có geometry hợp lệ. Mẫu số tỷ lệ ảnh là image record trong XML.",
            "Targets là đầu vào người dùng, chưa lưu trên server. Không có ground truth để kết luận chất lượng annotation."]
        targets = [t.model_dump() for t in request.query.targets] if request.query.targets is not None else []
        if request.query.generate is not None:
            if request.query.targets is not None: raise ValueError("Conflicting target sources")
            g = request.query.generate
            targets = generate_targets(images, boxes, g.dimensions, g.unit, g.target, g.weight, g.include_unobserved, cap=250)
        if not targets:
            result.update(state="no_results", reason="Chưa có targets do người dùng cung cấp; chưa tính coverage mục tiêu hoặc recommendation.")
            return result
        evaluation = evaluate_targets(images, boxes, targets)
        rec = recommendations(evaluation)
        records = lambda frame: json.loads(frame.to_json(orient="records", double_precision=10))
        result["tables"] += [table("targets", "Targets người dùng", targets, "coverage_targets.csv"),
            table("evaluation", "Coverage theo targets", records(evaluation), "coverage_results.csv"),
            table("recommendations", "Recommendation từ backend", records(rec), "slice_recommendations.csv")]
        result["metrics"] = [metric("total", "Tổng slice", len(evaluation)), metric("met", "Slice đạt", int((evaluation.gap == 0).sum())),
            metric("deficit", "Slice thiếu", int((evaluation.gap > 0).sum())),
            metric("weighted_coverage", "Weighted coverage", f"{100 * evaluation.loc[evaluation.gap == 0, 'weight'].sum()/evaluation.weight.sum():.1f}%")]
        return result
    if request.page == "scene-finder":
        images = [{**r, "weather": r["weather"].strip() or "missing", "timeofday": r["timeofday"].strip() or "missing"} for r in parsed["distribution_images"]]
        filtered = [r for r in images if (request.query.weather is None or r["weather"] == request.query.weather)
            and (request.query.timeofday is None or r["timeofday"] == request.query.timeofday)
            and (not request.query.keyword or request.query.keyword.strip().casefold() in r["image_name"].casefold())]
        result["metrics"] = [metric("matched", "Ảnh khớp bộ lọc", len(filtered)), metric("total", "Tổng ảnh trong XML", len(images)),
            metric("unresolved", "Ảnh có metadata chưa rõ", sum(r["weather"] in ("missing", "unknown") or r["timeofday"] in ("missing", "unknown") for r in images))]
        result["controls"] = [{"key": k, "label": label, "choices": sorted({r[k] for r in images}), "value": getattr(request.query, k)} for k,label in [("weather", "Weather"),("timeofday", "Time of day")]]
        result["keyword_search"] = True
        paths = [r["filename"] for r in quality]
        result["tables"] = [table("preview_images", "Scene images", [{**r, "image_path": canonical_image_path(r["image_name"], paths)} for r in filtered], "scene_filtered_images.csv")]
        if not filtered: result.update(state="empty", reason="Không có ảnh khớp bộ lọc.")
        result["notes"].append("Weather/Time of day lấy từ tag CVAT; không suy luận từ ảnh.")
        return result
    result["metrics"] = [metric("images", "Ảnh trong archive", summary["images"])]
    if summary.get("capabilities", {}).get("image_quality") is True:
        result["metrics"] += [metric("analyzed_images", "Ảnh đã phân tích", len(quality)),
            metric("review_images", "Ảnh cần review", sum(r["status"] == "REVIEW" for r in quality))]
        result["charts"].append(chart("statuses", "Trạng thái ảnh", Counter(r["status"] for r in quality)))
    else:
        result["notes"].append("Image Quality chưa được backend cho phép; không tạo KPI thay thế.")
    if summary["annotations"] and summary.get("capabilities", {}).get("distribution") is True:
        result["metrics"] += [metric("boxes", "CVAT boxes", len(parsed["distribution_boxes"])),
            metric("classes", "Số class", len(parsed["classes"]))]
        result["tables"].append(table("classes", "Phân bố class", parsed["classes"]))
    if summary["annotations"] and summary.get("capabilities", {}).get("label_audit") is True:
        result["metrics"].append(metric("review_boxes", "Box cần review", sum(r["status"].startswith("REVIEW") for r in audit)))
        result["charts"].append(chart("priorities", "Ưu tiên box", Counter(r["priority"] for r in audit)))
    if not summary["annotations"]:
        result["notes"].append("Dataset chỉ có ảnh; chưa có annotation CVAT.")
    return result

def canonical_image_path(name, paths):
    from core.cvat_parser import norm_path
    from pathlib import PurePosixPath
    key = norm_path(name)
    exact = [p for p in paths if norm_path(p) == key]
    if len(exact) == 1: return exact[0]
    suffix = [p for p in paths if norm_path(p).endswith("/" + key)]
    if len(suffix) == 1: return suffix[0]
    basename = [p for p in paths if PurePosixPath(norm_path(p)).name == PurePosixPath(key).name]
    return basename[0] if len(basename) == 1 else None
