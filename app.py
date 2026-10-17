"""Unified CVAT Quality Dashboard - merged from two user-provided applications.
Upload one ZIP; Image Quality works with image-only ZIP; Label Check requires CVAT for images annotations.xml.
"""
import os
import time
import io
import math
import hashlib
import zipfile
import xml.etree.ElementTree as ET
from pathlib import PurePosixPath
from collections import Counter
from dataclasses import dataclass
import cv2
import numpy as np
import pandas as pd
import streamlit as st
import plotly.express as px
from PIL import Image

from ui.backend_adapter import BackendClient, BackendError, render_backend_scene_finder, upload_source_key

st.set_page_config(page_title="CVAT Quality Dashboard | Leader Workspace", page_icon="📊", layout="wide", initial_sidebar_state="expanded")
from ui_theme import apply_theme, hero
apply_theme()
hero()
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
IMAGE_EXT = IMAGE_EXTENSIONS
MAX_IMAGE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
ISSUE_LABELS = {
 "BLUR":"Ảnh mờ", "DARK":"Thiếu sáng", "OVEREXPOSED":"Quá sáng",
 "LOW_CONTRAST":"Tương phản thấp", "LOW_RESOLUTION":"Độ phân giải thấp",
 "POSSIBLE_HIGH_NOISE":"Nghi nhiễu cao", "READ_ERROR":"Không đọc được ảnh"
}

def estimate_noise(gray):
    """Ước lượng nhiễu bằng MAD trên các vùng có gradient thấp."""
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

def analyze_image(uploaded_file, cfg):
    """Phân tích một ảnh và trả về các chỉ số chất lượng."""
    try:
        image_bytes = uploaded_file.getvalue()
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        image = cv2.imdecode(image_array, cv2.IMREAD_COLOR)

        if image is None:
            raise ValueError("Không thể đọc hoặc giải mã ảnh.")

        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        blur_score = float(cv2.Laplacian(
            gray, cv2.CV_64F
        ).var())

        brightness = float(np.mean(gray))
        contrast = float(np.std(gray))
        noise_score = estimate_noise(gray)

        black_pct = float(
            np.mean(gray < cfg["black_threshold"]) * 100
        )
        white_pct = float(
            np.mean(gray > cfg["white_threshold"]) * 100
        )

        issues = []

        if blur_score < cfg["blur_threshold"]:
            issues.append("BLUR")

        if brightness < cfg["dark_threshold"]:
            issues.append("DARK")

        if brightness > cfg["bright_threshold"]:
            issues.append("OVEREXPOSED")

        if contrast < cfg["contrast_threshold"]:
            issues.append("LOW_CONTRAST")

        if width < cfg["min_width"] or height < cfg["min_height"]:
            issues.append("LOW_RESOLUTION")

        if np.isfinite(noise_score):
            if noise_score > cfg["noise_threshold"]:
                issues.append("POSSIBLE_HIGH_NOISE")
        # Không tính được MAD thì không tự động kết luận ảnh nhiễu.

        score = 100
        score -= 20 if "BLUR" in issues else 0
        score -= 15 if "DARK" in issues else 0
        score -= 15 if "OVEREXPOSED" in issues else 0
        score -= 15 if "LOW_CONTRAST" in issues else 0
        score -= 15 if "LOW_RESOLUTION" in issues else 0
        score -= 15 if "POSSIBLE_HIGH_NOISE" in issues else 0
        score = max(0, score)

        status = "REVIEW" if issues else "PASS"

        return {
            "filename": uploaded_file.name,
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
            "image_bytes": image_bytes,
            "error": "",
        }

    except Exception as exc:
        return {
            "filename": uploaded_file.name,
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
            "image_bytes": uploaded_file.getvalue(),
            "error": str(exc),
        }

@dataclass
class ZipImage:
    name: str
    data: bytes

    def getvalue(self):
        return self.data

def read_images_from_zip(zip_bytes):
    """Đọc ảnh từ ZIP, giữ đường dẫn tương đối để tránh trùng tên."""
    images = []
    skipped = []
    total_uncompressed = 0
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            path = info.filename.replace("\\", "/")
            if path.startswith("__MACOSX/") or "/._" in path or path.startswith("._"):
                continue
            if not path.lower().endswith(IMAGE_EXTENSIONS):
                continue
            if info.file_size > MAX_IMAGE_BYTES:
                skipped.append(f"{path}: ảnh vượt quá 50 MB")
                continue
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_TOTAL_BYTES:
                raise ValueError("Tổng dung lượng ảnh giải nén vượt quá 1 GB.")
            try:
                images.append(ZipImage(name=path, data=archive.read(info)))
            except Exception as exc:
                skipped.append(f"{path}: {exc}")
    return images, skipped

def norm_path(s):
    return str(s).replace("\\", "/").lstrip("./").lower()

def load_cvat(data):
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        xmls = [n for n in names if PurePosixPath(norm_path(n)).name == "annotations.xml"]
        if not xmls:
            raise ValueError("ZIP không chứa annotations.xml (CVAT for images).")
        xml_name = sorted(xmls, key=lambda n: (n.count("/"), len(n)))[0]
        root = ET.fromstring(z.read(xml_name))
        img_files = [n for n in names if n.lower().endswith(IMAGE_EXT)]
        exact = {norm_path(n): n for n in img_files}
        basename = {}
        for n in img_files:
            basename.setdefault(PurePosixPath(norm_path(n)).name, []).append(n)
        images, boxes = [], []
        for idx, el in enumerate(root.findall(".//image")):
            image_id = el.get("id", str(idx))
            name = el.get("name", f"image_{idx}")
            path = exact.get(norm_path(name))
            if path is None:
                candidates = [v for k, v in exact.items() if k.endswith("/" + norm_path(name))]
                if len(candidates) == 1:
                    path = candidates[0]
            if path is None:
                candidates = basename.get(PurePosixPath(norm_path(name)).name, [])
                if len(candidates) == 1:
                    path = candidates[0]
            w, h = int(el.get("width", "0")), int(el.get("height", "0"))
            images.append(dict(image_id=image_id, image_name=name, image_path=path, width=w, height=h))
            for k, box in enumerate(el.findall("box")):
                try:
                    coords = [float(box.get(p)) for p in ("xtl", "ytl", "xbr", "ybr")]
                except (ValueError, TypeError):
                    coords = [float("nan")] * 4
                boxes.append(dict(image_id=image_id, image_name=name, box_id=f"{idx}:{k}",
                                  label=box.get("label", "unknown"), occluded=box.get("occluded", "0"),
                                  xtl=coords[0], ytl=coords[1], xbr=coords[2], ybr=coords[3]))
    return pd.DataFrame(images), pd.DataFrame(boxes)

def decode_image(z, path):
    arr = np.frombuffer(z.read(path), dtype=np.uint8)
    image = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Không đọc được ảnh")
    return image

def iou(a, b):
    xx1, yy1 = max(a[0], b[0]), max(a[1], b[1])
    xx2, yy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0, xx2-xx1) * max(0, yy2-yy1)
    aa = max(0, a[2]-a[0]) * max(0, a[3]-a[1])
    bb = max(0, b[2]-b[0]) * max(0, b[3]-b[1])
    return inter/(aa+bb-inter) if aa+bb-inter > 0 else 0.0

def visual_metrics(image, coords):
    H, W = image.shape[:2]
    x1, y1, x2, y2 = coords
    x1, y1, x2, y2 = max(0, int(np.floor(x1))), max(0, int(np.floor(y1))), min(W, int(np.ceil(x2))), min(H, int(np.ceil(y2)))
    if x2-x1 < 8 or y2-y1 < 8:
        return None
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    roi = gray[y1:y2, x1:x2]
    edge = cv2.Canny(roi, 50, 150)
    density = float(np.mean(edge > 0))
    texture = float(np.var(roi.astype(np.float32)))
    sharpness = float(cv2.Laplacian(roi, cv2.CV_64F).var())
    margin = max(4, int(0.25 * max(x2-x1, y2-y1)))
    ox1, oy1 = max(0, x1-margin), max(0, y1-margin)
    ox2, oy2 = min(W, x2+margin), min(H, y2+margin)
    outer = lab[oy1:oy2, ox1:ox2].astype(np.float32)
    mask = np.ones(outer.shape[:2], dtype=bool)
    mask[y1-oy1:y2-oy1, x1-ox1:x2-ox1] = False
    inside = lab[y1:y2, x1:x2].astype(np.float32)
    contrast = float(np.linalg.norm(inside.mean(axis=(0, 1)) - outer[mask].mean(axis=0))) if np.any(mask) else np.nan
    # Edge density in a narrow strip along box perimeter (proxy, NOT proof of tightness).
    band = min(4, max(1, min(x2-x1, y2-y1)//8))
    perimeter_mask = np.zeros(roi.shape, dtype=bool)
    perimeter_mask[:band, :] = True
    perimeter_mask[-band:, :] = True
    perimeter_mask[:, :band] = True
    perimeter_mask[:, -band:] = True
    boundary = float(np.mean(edge[perimeter_mask] > 0))
    return dict(edge_density=density, texture_variance=texture,
                laplacian_variance=sharpness, foreground_contrast=contrast,
                boundary_support=boundary)

def audit(data, images, boxes, cfg):
    results = []
    if boxes.empty:
        return pd.DataFrame()
    progress = st.progress(0, text="Đang kiểm tra...")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for pos, img in enumerate(images.to_dict("records")):
            image_boxes = boxes[boxes.image_id == img["image_id"]].to_dict("records")
            image = None
            image_error = None
            if img["image_path"] is not None:
                try:
                    image = decode_image(z, img["image_path"])
                except Exception as e:
                    image_error = str(e)
            else:
                image_error = "Ảnh không có trong ZIP"
            H = image.shape[0] if image is not None else img["height"]
            W = image.shape[1] if image is not None else img["width"]
            valid = {}
            for b in image_boxes:
                coords = [b[k] for k in ("xtl", "ytl", "xbr", "ybr")]
                valid[b["box_id"]] = bool(np.all(np.isfinite(coords)) and coords[2] > coords[0] and coords[3] > coords[1])
            duplicate = set()
            for i, a in enumerate(image_boxes):
                if not valid[a["box_id"]]:
                    continue
                for b in image_boxes[i+1:]:
                    if not valid[b["box_id"]]:
                        continue
                    if a["label"] == b["label"] and iou([a[k] for k in ("xtl", "ytl", "xbr", "ybr")],
                        [b[k] for k in ("xtl", "ytl", "xbr", "ybr")]) >= cfg["duplicate_iou"]:
                        duplicate.update((a["box_id"], b["box_id"]))
            for b in image_boxes:
                flags_a, flags_b = [], []
                metrics = dict(edge_density=np.nan, texture_variance=np.nan,
                               laplacian_variance=np.nan, foreground_contrast=np.nan,
                               boundary_support=np.nan)
                c = [b[k] for k in ("xtl", "ytl", "xbr", "ybr")]
                area_pct, aspect = np.nan, np.nan
                if not valid[b["box_id"]]:
                    flags_a.append("INVALID_BOX")
                else:
                    bw, bh = c[2]-c[0], c[3]-c[1]
                    aspect = bw / bh
                    if W > 0 and H > 0:
                        area_pct = 100 * bw * bh / (W * H)
                        if c[0] < 0 or c[1] < 0 or c[2] > W or c[3] > H:
                            flags_a.append("OUT_OF_BOUNDS")
                        if area_pct < cfg["min_area_pct"]:
                            flags_a.append("SMALL_BOX")
                        if area_pct > cfg["max_area_pct"]:
                            flags_a.append("LARGE_BOX")
                    if aspect < cfg["min_aspect"] or aspect > cfg["max_aspect"]:
                        flags_a.append("ASPECT_RATIO_OUTLIER")
                    if b["box_id"] in duplicate:
                        flags_a.append("POSSIBLE_DUPLICATE")
                    if image is not None:
                        visual = visual_metrics(image, c)
                        if visual is None:
                            flags_b.append("ROI_TOO_SMALL")
                        else:
                            metrics.update(visual)
                            if visual["edge_density"] < cfg["min_edge"]:
                                flags_b.append("LOW_EDGE_DENSITY")
                            if visual["texture_variance"] < cfg["min_texture"]:
                                flags_b.append("LOW_TEXTURE")
                            if visual["laplacian_variance"] < cfg["min_sharpness"]:
                                flags_b.append("LOW_SHARPNESS")
                            if np.isfinite(visual["foreground_contrast"]) and visual["foreground_contrast"] < cfg["min_contrast"]:
                                flags_b.append("LOW_FOREGROUND_CONTRAST")
                            if visual["boundary_support"] < cfg["min_boundary"]:
                                flags_b.append("WEAK_BOUNDARY_SUPPORT")
                            if all(k in flags_b for k in ("LOW_EDGE_DENSITY", "LOW_TEXTURE", "LOW_FOREGROUND_CONTRAST")):
                                flags_b.append("POSSIBLE_EMPTY_BOX")
                # Multiple correlated visual metrics should not independently escalate severity.
                if "INVALID_BOX" in flags_a or "OUT_OF_BOUNDS" in flags_a or "POSSIBLE_DUPLICATE" in flags_a or "POSSIBLE_EMPTY_BOX" in flags_b:
                    priority = "HIGH"
                elif flags_a or any(x not in ("LOW_SHARPNESS", "ROI_TOO_SMALL") for x in flags_b):
                    priority = "MEDIUM"
                elif flags_b:
                    priority = "LOW"
                else:
                    priority = "LOW"
                status = "REVIEW" if flags_a or any(x != "ROI_TOO_SMALL" for x in flags_b) else "NO_SIGNAL"
                if image is None:
                    status = "A_ONLY" if status == "NO_SIGNAL" else "REVIEW_A_ONLY"
                results.append({**b, "image_available": image is not None, "image_error": image_error,
                                "area_pct": area_pct, "aspect_ratio": aspect, **metrics,
                                "geometry_issues": ", ".join(flags_a), "visual_issues": ", ".join(flags_b),
                                "issues": ", ".join(flags_a+flags_b), "issue_count": len(flags_a)+len(flags_b),
                                "priority": priority, "status": status})
            progress.progress((pos+1)/len(images), text=f"Đã xử lý {pos+1}/{len(images)} ảnh")
    progress.empty()
    return pd.DataFrame(results)

def annotate(image, rows):
    out = image.copy()
    for r in rows.to_dict("records"):
        c = [r[k] for k in ("xtl", "ytl", "xbr", "ybr")]
        if not np.all(np.isfinite(c)) or c[2] <= c[0] or c[3] <= c[1]:
            continue
        H, W = out.shape[:2]
        x1, y1, x2, y2 = int(max(0,c[0])), int(max(0,c[1])), int(min(W-1,c[2])), int(min(H-1,c[3]))
        if x2 <= x1 or y2 <= y1:
            continue
        color = (0, 0, 255) if r["priority"] == "HIGH" else (0, 165, 255) if r["priority"] == "MEDIUM" else (0, 200, 0)
        cv2.rectangle(out, (x1,y1), (x2,y2), color, 2)
        label = f'{r["label"]} [{r["priority"]}]'
        cv2.putText(out, label, (x1, max(15, y1-5)), cv2.FONT_HERSHEY_SIMPLEX, 0.48, color, 2)
    return cv2.cvtColor(out, cv2.COLOR_BGR2RGB)



def get_cfg_image():
    with st.sidebar.expander("Image Quality thresholds", expanded=False):
        return dict(
            blur_threshold=st.number_input("Độ nét tối thiểu", 0.0, value=100.0, step=10.0),
            dark_threshold=st.slider("Ngưỡng tối", 0, 150, 40),
            bright_threshold=st.slider("Ngưỡng quá sáng", 150, 255, 215),
            contrast_threshold=st.number_input("Độ tương phản tối thiểu", 0.0, value=25.0),
            noise_threshold=st.number_input("Ngưỡng nhiễu MAD", 0.0, value=10.0),
            min_width=int(st.number_input("Chiều rộng tối thiểu", 1, value=320)),
            min_height=int(st.number_input("Chiều cao tối thiểu", 1, value=240)),
            black_threshold=20, white_threshold=245,
        )


def get_cfg_label():
    with st.sidebar.expander("Label Check A/B thresholds", expanded=False):
        return dict(
            min_area_pct=st.number_input("Min box area (% ảnh)", 0.0, 100.0, 0.01, 0.01, format="%.3f"),
            max_area_pct=st.number_input("Max box area (% ảnh)", 0.0, 10000.0, 80.0, 1.0),
            min_aspect=st.number_input("Min aspect ratio", 0.001, 100.0, 0.15, 0.05),
            max_aspect=st.number_input("Max aspect ratio", 0.01, 1000.0, 7.0, 0.5),
            duplicate_iou=st.slider("Duplicate IoU", 0.5, 1.0, 0.90, 0.01),
            min_edge=st.slider("Min edge density", 0.0, 0.3, 0.02, 0.005),
            min_texture=st.number_input("Min grayscale variance", 0.0, 10000.0, 100.0, 10.0),
            min_sharpness=st.number_input("Min Laplacian variance", 0.0, 10000.0, 50.0, 5.0),
            min_contrast=st.number_input("Min LAB contrast", 0.0, 200.0, 12.0, 1.0),
            min_boundary=st.slider("Min boundary edge density", 0.0, 0.3, 0.02, 0.005),
        )


def chart_or_info(df, kind, **kwargs):
    if df is None or df.empty:
        st.info("Chưa có dữ liệu để hiển thị biểu đồ.")
    else:
        fig = getattr(px, kind)(df, **kwargs)
        fig.update_layout(margin=dict(l=15, r=15, t=45, b=15), legend_title_text="")
        st.plotly_chart(fig, use_container_width=True)


st.sidebar.header("⚙️ Cấu hình")
page_size = st.sidebar.selectbox("Số ảnh mỗi trang", [5, 8, 10, 16], index=2)
image_cfg = get_cfg_image()
label_cfg = get_cfg_label()

with st.sidebar.expander("🔌 Kết nối Backend (Stage 6)", expanded=False):
    backend_url = st.text_input("Backend URL", value=os.environ.get("CVAT_BACKEND_URL", "http://127.0.0.1:8011"))
    owner_key = st.text_input("Owner Key", value=os.environ.get("CVAT_OWNER_KEY", ""), type="password")
    use_backend = st.checkbox("Sử dụng Backend bền vững", value=bool(os.environ.get("CVAT_USE_BACKEND", "0") == "1" or os.environ.get("CVAT_OWNER_KEY")))

backend_client = BackendClient(backend_url, owner_key) if use_backend else None

uploaded = st.file_uploader("Upload một file ZIP chứa ảnh và (nếu có) annotations.xml", type=["zip"])
if uploaded is None:
    st.info("Tải ZIP để bắt đầu. ZIP chỉ có ảnh vẫn chạy Image Quality; Label Check yêu cầu annotations.xml.")
    st.stop()

if use_backend and backend_client:
    pos = uploaded.tell()
    h = hashlib.sha256()
    while chunk := uploaded.read(65536):
        h.update(chunk)
    uploaded.seek(pos)
    file_sig = h.hexdigest()

    # Scope cached results to the endpoint, owner and exact source identity.
    source_identity = upload_source_key(uploaded.name, uploaded.size, file_sig)
    connection_identity = hashlib.sha256((backend_url + "\0" + owner_key).encode()).hexdigest()
    recovery_session = None
    with st.expander("Khôi phục phiên upload cũ (tùy chọn)"):
        st.caption("Chỉ dùng thông tin phiên gốc. Không chọn phiên chỉ vì cùng Owner Key. Tên, kích thước và mọi chunk đã nhận sẽ được kiểm tra.")
        recovery_id = st.text_input("Upload ID của phiên gốc")
        recovery_capability = st.text_input("Upload capability của phiên gốc", type="password")
        confirm_source = st.checkbox("Tôi xác nhận đây là đúng tệp nguồn của phiên cũ chưa có dấu vân tay toàn tệp.", key="confirm_" + source_identity + connection_identity + hashlib.sha256(recovery_id.encode()).hexdigest())
        if recovery_id or recovery_capability:
            if not recovery_id or not recovery_capability or not confirm_source:
                st.info("Cần đủ thông tin phiên và xác nhận tệp nguồn trước khi tiếp tục.")
                st.stop()
            recovery_session = {"upload_id": recovery_id, "upload_capability": recovery_capability}
    restart_terminal = st.checkbox("Tạo lần tải mới chỉ khi phiên cùng tệp đã bị hủy hoặc hết hạn", value=False)
    retry_validation = st.checkbox("Thử lại kiểm tra dataset đã thất bại (chỉ khi đã xem lỗi)", value=False)

    image_key = (file_sig, tuple(sorted(image_cfg.items())))
    label_key = (file_sig, tuple(sorted(label_cfg.items())))

    cached_session = st.session_state.get("backend_session")
    if not cached_session or cached_session.get("identity") != (connection_identity, source_identity):
        if not st.button("Tải lên hoặc tiếp tục phiên đã xác minh", type="primary"):
            st.stop()
        if not backend_client.check_health():
            st.error(f"Không thể kết nối tới FastAPI Backend tại {backend_url}. Vui lòng kiểm tra dịch vụ.")
            st.stop()

        with st.status("Đang tải và xử lý dataset qua Backend...", expanded=True) as status_box:
            status_box.write("Bắt đầu tải dữ liệu lên backend...")
            def on_progress(offset, total):
                status_box.write(f"Đã truyền {offset:,} / {total:,} bytes ({int(offset/total*100)}%)")

            try:
                upload_info = backend_client.stream_upload(
                    uploaded,
                    uploaded.name,
                    uploaded.size,
                    on_progress=on_progress,
                    idempotency_key=source_identity,
                    recovery_session=recovery_session,
                    confirmed_legacy_source=bool(recovery_session and confirm_source),
                    restart_terminal=restart_terminal,
                )
            except BackendError as e:
                status_box.update(label=f"❌ Upload thất bại: {e.message}", state="error")
                st.error(e.message)
                st.stop()

            upload_id = upload_info["upload_id"]
            capability = upload_info["upload_capability"]
            status_box.write("✅ Upload hoàn tất (payload đã niêm phong).")

            status_box.write("Đang kiểm tra và lập chỉ mục...")
            try:
                backend_client.start_validation(upload_id, capability, retry=retry_validation)
            except BackendError as e:
                status_box.update(label=f"❌ Không thể bắt đầu kiểm tra: {e.message}", state="error")
                st.error(e.message)
                st.stop()

            deadline = time.monotonic() + 120
            while True:
                if time.monotonic() > deadline:
                    status_box.update(label="❌ Quá thời gian chờ xử lý", state="error")
                    st.error("Quá trình kiểm tra dataset vượt quá thời gian cho phép.")
                    st.stop()
                st_info = backend_client.get_dataset_status(upload_id, capability)
                state = st_info["state"]
                if state == "ready":
                    status_box.update(label="✅ Dataset đã sẵn sàng và được niêm phong", state="complete", expanded=False)
                    dataset_id = st_info["dataset_id"]
                    break
                elif state in ("validating", "indexing", "publishing"):
                    status_box.write(f"Trạng thái lifecycle: {state}...")
                    time.sleep(0.5)
                elif state == "failed":
                    status_box.update(label=f"❌ Xử lý thất bại: {st_info.get('error_code')}", state="error")
                    st.error(f"Dataset không hợp lệ: {st_info.get('error_code')}")
                    st.stop()
                elif state == "cancelled":
                    status_box.update(label="⚠️ Quá trình đã bị hủy", state="error")
                    st.stop()

            with st.spinner("Đang thực hiện phân tích chất lượng ảnh và nhãn (Stage 5 Pipeline)..."):
                analysis = backend_client.trigger_analysis(dataset_id, capability, image_cfg, label_cfg)

            st.session_state["backend_session"] = {
                "sig": file_sig,
                "identity": (connection_identity, source_identity),
                "dataset_id": dataset_id,
                "capability": capability,
                "analysis": analysis,
                "image_key": image_key,
                "label_key": label_key,
            }

    session_data = st.session_state["backend_session"]
    dataset_id = session_data["dataset_id"]
    capability = session_data["capability"]

    if session_data.get("image_key") != image_key or session_data.get("label_key") != label_key:
        with st.spinner("Cập nhật phân tích theo cấu hình ngưỡng mới..."):
            session_data["analysis"] = backend_client.trigger_analysis(dataset_id, capability, image_cfg, label_cfg)
            session_data["image_key"] = image_key
            session_data["label_key"] = label_key

    analysis = session_data["analysis"]
    quality_rows = analysis["quality_rows"]
    audit_rows = analysis["audit_rows"]
    classes_rows = analysis["classes"]
    attribute_rows = analysis["attribute_distribution"]
    dist_images_rows = analysis.get("distribution_images", [])
    dist_boxes_rows = analysis.get("distribution_boxes", [])
    detected_attributes = analysis.get("attributes", [])

    image_results = quality_rows
    image_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ("image_bytes", "issues")} | {"issues": ", ".join(r["issues"]) if isinstance(r.get("issues"), list) else str(r.get("issues", ""))} for r in image_results])
    label_df = pd.DataFrame(audit_rows)
    can_label = not label_df.empty
    cvat_boxes = label_df
    cvat_images = pd.DataFrame([{"image_name": r.get("image_name"), "image_path": r.get("image_name")} for r in audit_rows]).drop_duplicates() if can_label else pd.DataFrame()
    skipped_files = []
    zip_data = b""
    dist_images = pd.DataFrame(dist_images_rows)
    dist_boxes = pd.DataFrame(dist_boxes_rows)
    dist = pd.DataFrame(classes_rows)
    att = pd.DataFrame(attribute_rows)

else:
    zip_data = uploaded.getvalue()
    file_sig = hashlib.sha256(zip_data).hexdigest()
    image_key = (file_sig, tuple(sorted(image_cfg.items())))
    label_key = (file_sig, tuple(sorted(label_cfg.items())))

    if st.session_state.get("merged_zip_sig") != file_sig:
        try:
            with st.spinner("Đang đọc ZIP..."):
                zip_images, skipped_files = read_images_from_zip(zip_data)
                if not zip_images:
                    st.error("Không tìm thấy ảnh hợp lệ trong ZIP. Kiểm tra định dạng JPG/PNG/WEBP hoặc kích thước ảnh.")
                    st.stop()
                try:
                    cvat_images, cvat_boxes = load_cvat(zip_data)
                    cvat_error = None
                except Exception as e:
                    cvat_images, cvat_boxes = pd.DataFrame(), pd.DataFrame()
                    cvat_error = str(e)
        except (zipfile.BadZipFile, ValueError, OSError) as e:
            st.error(f"Không đọc được ZIP: {e}")
            st.stop()
        st.session_state["merged_zip"] = (zip_images, skipped_files, cvat_images, cvat_boxes, cvat_error)
        st.session_state["merged_zip_sig"] = file_sig
        st.session_state.pop("merged_image_key", None)
        st.session_state.pop("merged_label_key", None)

    zip_images, skipped_files, cvat_images, cvat_boxes, cvat_error = st.session_state["merged_zip"]

    if st.session_state.get("merged_image_key") != image_key:
        with st.spinner("Đang phân tích chất lượng ảnh..."):
            st.session_state["merged_image_results"] = [analyze_image(f, image_cfg) for f in zip_images]
        st.session_state["merged_image_key"] = image_key

    image_results = st.session_state["merged_image_results"]
    image_df = pd.DataFrame([{k: v for k, v in r.items() if k not in ("image_bytes", "issues")} | {"issues": ", ".join(r["issues"])} for r in image_results])

    can_label = not cvat_images.empty and not cvat_boxes.empty
    if can_label and st.session_state.get("merged_label_key") != label_key:
        with st.spinner("Đang kiểm tra label (A + B)..."):
            st.session_state["merged_label_results"] = audit(zip_data, cvat_images, cvat_boxes, label_cfg)
        st.session_state["merged_label_key"] = label_key
    label_df = st.session_state.get("merged_label_results", pd.DataFrame()) if can_label else pd.DataFrame()

    if skipped_files:
        with st.expander(f"{len(skipped_files)} ảnh bị bỏ qua"):
            st.write("\n".join(skipped_files))


# Separate tabs, all fed by the same ZIP and cached results.
tabs = st.tabs(["📊 Tổng quan", "📈 Phân bố & thuộc tính", "🖼️ Image Quality", "🏷️ Label Check A+B", "🔎 Review ảnh", "📥 Xuất báo cáo", "🎯 Coverage & Slice"])

with tabs[0]:
    st.subheader("Tổng quan dữ liệu")
    c1,c2,c3,c4,c5 = st.columns(5)
    c1.metric("Tổng ảnh", len(image_results))
    c2.metric("Ảnh cần review", sum(r["status"] == "REVIEW" for r in image_results))
    c3.metric("CVAT boxes", len(cvat_boxes) if can_label else 0)
    c4.metric("Box cần review", int(label_df.status.str.startswith("REVIEW").sum()) if not label_df.empty else 0)
    c5.metric("Số class", int(label_df.label.nunique()) if not label_df.empty else 0)
    left, right = st.columns(2)
    with left:
        counts = image_df.status.value_counts().rename_axis("status").reset_index(name="count")
        chart_or_info(counts, "pie", names="status", values="count", hole=0.55, title="Trạng thái ảnh")
    with right:
        if not label_df.empty:
            counts = label_df.label.value_counts().rename_axis("class").reset_index(name="boxes")
            chart_or_info(counts, "bar", x="class", y="boxes", title="Số bounding box theo class")
        else:
            st.info("Không có annotation CVAT for images; tab Label Check chưa có dữ liệu.")
    if not label_df.empty:
        counts = label_df.priority.value_counts().rename_axis("priority").reset_index(name="boxes")
        chart_or_info(counts, "pie", names="priority", values="boxes", hole=0.45, title="Mức ưu tiên kiểm tra box")


from core.distribution import parse_distribution, class_distribution, imbalance_warnings, attribute_distribution

with tabs[1]:
    st.subheader("Phân bố class, bounding box và thuộc tính CVAT")
    st.caption("Tự phát hiện class/attribute từ annotations.xml. Cảnh báo mất cân bằng là tín hiệu thống kê, không phải lỗi annotation.")
    if not (use_backend and backend_client):
        try:
            dist_images, dist_boxes, detected_attributes = parse_distribution(zip_data)
        except (ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
            st.warning(f"Không thể thống kê annotation: {exc}")
            dist = pd.DataFrame()
        else:
            dist = class_distribution(dist_boxes)
    else:
        dist = class_distribution(dist_boxes) if not dist_boxes.empty else pd.DataFrame()

    a,b,c,d = st.columns(4)
    a.metric("Ảnh có annotation record", len(dist_images))
    b.metric("Tổng box", len(dist_boxes))
    c.metric("Số class", len(dist))
    d.metric("Thuộc tính phát hiện", len(detected_attributes))
    if dist.empty:
        st.info("XML chưa có bounding box. Vẫn có thể thống kê thời tiết và thời điểm nếu được gán tag.")
    else:
        st.markdown("#### Phân bố class")
        chart_or_info(dist, "bar", x="class", y="boxes", hover_data=["share_pct", "images"], title="Số box theo class")
        st.dataframe(dist, hide_index=True, use_container_width=True)
        st.markdown("#### Cảnh báo class lệch tỷ lệ")
        col1,col2,col3 = st.columns(3)
        with col1: min_count = st.number_input("Số box tối thiểu", min_value=0, value=30, step=10)
        with col2: min_share = st.number_input("Tỷ lệ tối thiểu (%)", min_value=0.0, max_value=100.0, value=1.0, step=0.5)
        with col3: max_share = st.number_input("Tỷ lệ tối đa (%)", min_value=0.0, max_value=100.0, value=60.0, step=5.0)
        if min_share > max_share:
            st.error("Tỷ lệ tối thiểu không được lớn hơn tỷ lệ tối đa.")
        else:
            alerts = imbalance_warnings(dist, min_count, min_share, max_share)
            if alerts.empty: st.success("Không có class nào vượt các ngưỡng cảnh báo đã chọn.")
            else: st.warning(f"{len(alerts)} class cần xem xét theo ngưỡng cấu hình."); st.dataframe(alerts, hide_index=True, use_container_width=True)
        st.markdown("#### Kích thước box theo class")
        valid = dist_boxes[dist_boxes.valid_geometry].copy() if "valid_geometry" in dist_boxes.columns else dist_boxes.copy()
        if not valid.empty and "area_pct" in valid.columns:
            bins = [-float('inf'), 0.1, 1.0, float('inf')]
            valid['size_group'] = pd.cut(valid.area_pct, bins=bins, labels=['Nhỏ (<0.1%)','Vừa (0.1–1%)','Lớn (>1%)'])
            sizes = valid.groupby(['label','size_group'], observed=True).size().reset_index(name='boxes')
            chart_or_info(sizes, 'bar', x='label', y='boxes', color='size_group', barmode='stack', title='Phân nhóm diện tích box / diện tích ảnh')
            st.caption("Ngưỡng 0.1% và 1% chỉ là cách phân nhóm minh họa, không phải tiêu chuẩn lỗi.")
            st.dataframe(valid.groupby('label').area_pct.agg(['count','mean','median','min','max']).reset_index(), hide_index=True, use_container_width=True)
        st.markdown("#### Độ che khuất theo class")
        if not dist_boxes.empty and "occluded" in dist_boxes.columns:
            occ = dist_boxes.groupby(['label','occluded']).size().reset_index(name='boxes')
            chart_or_info(occ, 'bar', x='label', y='boxes', color='occluded', barmode='stack', title='Occluded theo class')
            st.dataframe(occ, hide_index=True, use_container_width=True)
    st.markdown("#### Thời tiết và thời điểm (đếm theo ảnh)")
    col1,col2=st.columns(2)
    with col1:
        wx=dist_images.weather.value_counts(dropna=False).rename_axis('weather').reset_index(name='images') if "weather" in dist_images.columns else pd.DataFrame(columns=["weather","images"])
        chart_or_info(wx,'bar',x='weather',y='images',title='Weather')
    with col2:
        tod=dist_images.timeofday.value_counts(dropna=False).rename_axis('timeofday').reset_index(name='images') if "timeofday" in dist_images.columns else pd.DataFrame(columns=["timeofday","images"])
        chart_or_info(tod,'bar',x='timeofday',y='images',title='Time of day')
    st.markdown("#### Phân bố kết hợp thời tiết × thời điểm")
    if "weather" in dist_images.columns and "timeofday" in dist_images.columns:
        scene=dist_images.groupby(['weather','timeofday'],dropna=False).size().reset_index(name='images')
        st.dataframe(scene,hide_index=True,use_container_width=True)
    from core.scene_finder import render_scene_finder
    if use_backend and backend_client:
        render_backend_scene_finder(dist_images, dataset_id, capability, backend_client)
    else:
        render_scene_finder(dist_images, zip_data, MAX_IMAGE_BYTES)
    st.markdown("#### Thuộc tính phát hiện động")
    if not (use_backend and backend_client):
        att=attribute_distribution(dist_images,dist_boxes,detected_attributes)
    st.write("Tên thuộc tính:", ', '.join(detected_attributes) if detected_attributes else 'Không có attribute tùy chỉnh')
    if not att.empty:
        st.dataframe(att,hide_index=True,use_container_width=True)
    st.download_button('Tải class distribution CSV', dist.to_csv(index=False).encode('utf-8-sig'), 'class_distribution.csv','text/csv')
    st.download_button('Tải attributes CSV', att.to_csv(index=False).encode('utf-8-sig'), 'attribute_distribution.csv','text/csv')
    if not dist_boxes.empty:
        drop_cols = [c for c in ['attributes'] if c in dist_boxes.columns]
        st.download_button('Tải box metrics CSV', dist_boxes.drop(columns=drop_cols).to_csv(index=False).encode('utf-8-sig'), 'box_distribution.csv','text/csv')


with tabs[2]:
    st.subheader("Image Quality — Phân tích chất lượng ảnh")
    a,b,c,d = st.columns(4)
    a.metric("PASS", int((image_df.status == "PASS").sum()))
    b.metric("REVIEW", int((image_df.status == "REVIEW").sum()))
    c.metric("ERROR", int((image_df.status == "ERROR").sum()))
    d.metric("Điểm trung bình", f"{image_df.score.mean():.1f}/100")
    l,r = st.columns(2)
    with l:
        chart_or_info(image_df, "histogram", x="blur_score", nbins=35, title="Phân bố độ nét (Laplacian variance)")
        chart_or_info(image_df, "box", y="score", points="outliers", title="Phân bố điểm sàng lọc")
    with r:
        chart_or_info(image_df, "scatter", x="brightness", y="contrast", color="status", hover_name="filename", title="Độ sáng × Tương phản")
        issue_count = Counter(x for result in image_results for x in result["issues"])
        issue_table = pd.DataFrame([{"issue": ISSUE_LABELS.get(k,k), "count": v} for k,v in issue_count.items()])
        chart_or_info(issue_table, "bar", x="count", y="issue", orientation="h", title="Tần suất vấn đề ảnh")
    st.dataframe(image_df, hide_index=True, use_container_width=True)

with tabs[3]:
    st.subheader("Label Check — Geometry (A) + Visual (B)")
    if label_df.empty:
        st.warning("ZIP cần annotations.xml định dạng CVAT for images có thẻ <image> và <box>. " + (cvat_error or "Không có bounding box để kiểm tra."))
    else:
        st.caption("Class tự đọc từ XML. Kết quả là review signals, không phải lỗi đã xác minh.")
        chosen_classes = st.multiselect("Class", sorted(label_df.label.unique()), default=sorted(label_df.label.unique()), key="class_filter")
        chosen_priorities = st.multiselect("Priority", ["HIGH","MEDIUM","LOW"], default=["HIGH","MEDIUM","LOW"], key="priority_filter")
        only_review = st.checkbox("Chỉ hiện box có cảnh báo", value=True)
        filtered = label_df[label_df.label.isin(chosen_classes) & label_df.priority.isin(chosen_priorities)].copy()
        if only_review:
            filtered = filtered[filtered.status.str.startswith("REVIEW")]
        m1,m2,m3,m4 = st.columns(4)
        m1.metric("Tổng box", len(label_df))
        m2.metric("Box cần review", int(label_df.status.str.startswith("REVIEW").sum()))
        m3.metric("HIGH", int(((label_df.priority=="HIGH") & label_df.status.str.startswith("REVIEW")).sum()))
        m4.metric("Có ảnh kiểm tra B", int(label_df.image_available.sum()))
        l,r = st.columns(2)
        with l:
            chart_or_info(label_df, "histogram", x="area_pct", nbins=40, title="Phân bố diện tích box (% ảnh)")
            chart_or_info(label_df, "scatter", x="edge_density", y="foreground_contrast", color="priority", hover_name="image_name", title="Edge density × Foreground contrast")
        with r:
            chart_or_info(label_df, "box", x="label", y="aspect_ratio", title="Aspect ratio theo class")
            counts = Counter(x.strip() for value in label_df.issues.fillna("") for x in value.split(",") if x.strip())
            issue_table = pd.DataFrame([{"issue":k,"count":v} for k,v in counts.items()])
            chart_or_info(issue_table, "bar", x="count", y="issue", orientation="h", title="Tín hiệu bất thường")
        summary = label_df.groupby("label", as_index=False).agg(total_boxes=("box_id","size"), review_boxes=("status", lambda x:int(x.str.startswith("REVIEW").sum())), images=("image_name","nunique"))
        summary["review_rate_pct"] = (summary.review_boxes / summary.total_boxes * 100).round(1)
        st.subheader("Thống kê theo class")
        st.dataframe(summary, hide_index=True, use_container_width=True)
        st.subheader("Chi tiết box")
        st.dataframe(filtered, hide_index=True, use_container_width=True)

with tabs[4]:
    st.subheader("Review ảnh trực quan")
    mode = st.radio("Xem", ["Ảnh gốc + Image Quality", "Ảnh có box CVAT + Label Check"], horizontal=True)
    if mode == "Ảnh gốc + Image Quality":
        options = list(range(len(image_results)))
        selected_status = st.selectbox("Trạng thái", ["ALL","PASS","REVIEW","ERROR"])
        options = [i for i in options if selected_status == "ALL" or image_results[i]["status"] == selected_status]
        if not options:
            st.info("Không có ảnh phù hợp.")
        else:
            page_count = math.ceil(len(options)/page_size)
            page = st.number_input("Trang", 1, page_count, 1, key="image_page")
            subset = options[(page-1)*page_size:page*page_size]
            cols = st.columns(3)
            for n, i in enumerate(subset):
                row = image_results[i]
                with cols[n % 3]:
                    with st.container(border=True):
                        try:
                            if use_backend and backend_client:
                                im_bytes = backend_client.fetch_preview(dataset_id, capability, row["filename"], annotated=False)
                                st.image(im_bytes, use_container_width=True)
                            else:
                                st.image(Image.open(io.BytesIO(row["image_bytes"])), use_container_width=True)
                        except Exception:
                            st.warning("Không đọc được ảnh")
                        st.write(row["filename"])
                        st.caption(f'{row["status"]} · Score {row["score"]}/100 · {", ".join(row["issues"]) or "Không có cảnh báo"}')
                        with st.expander("Chỉ số"):
                            st.json({k:v for k,v in row.items() if k not in ("image_bytes", "issues")})
    elif label_df.empty:
        st.info("Không có label CVAT để vẽ.")
    else:
        choices = label_df.image_name.dropna().unique().tolist()
        selected = st.selectbox("Chọn ảnh", choices)
        img_rows = cvat_images[cvat_images.image_name == selected]
        rows = label_df[label_df.image_name == selected]
        if img_rows.empty or img_rows.iloc[0]["image_path"] is None:
            st.warning("Không tìm thấy ảnh tương ứng trong ZIP. Vẫn xem được bảng Geometry A.")
        else:
            try:
                if use_backend and backend_client:
                    annotated_bytes = backend_client.fetch_preview(dataset_id, capability, selected, annotated=True)
                    st.image(annotated_bytes, caption="Đỏ: HIGH · Cam: MEDIUM · Xanh: LOW", use_container_width=True)
                else:
                    with zipfile.ZipFile(io.BytesIO(zip_data)) as z:
                        img = decode_image(z, img_rows.iloc[0]["image_path"])
                    st.image(annotate(img, rows), caption="Đỏ: HIGH · Cam: MEDIUM · Xanh: LOW", use_container_width=True)
            except Exception as e:
                st.warning(f"Không hiển thị được ảnh: {e}")
        st.dataframe(rows, hide_index=True, use_container_width=True)

with tabs[5]:
    st.subheader("Xuất báo cáo CSV")
    st.download_button("Tải Image Quality CSV", image_df.to_csv(index=False).encode("utf-8-sig"), "image_quality_report.csv", "text/csv")
    if not label_df.empty:
        st.download_button("Tải Label Check A+B CSV", label_df.to_csv(index=False).encode("utf-8-sig"), "label_check_ab_report.csv", "text/csv")
        summary = label_df.groupby("label",as_index=False).agg(total_boxes=("box_id","size"), review_boxes=("status",lambda x:int(x.str.startswith("REVIEW").sum())))
        st.download_button("Tải Class Summary CSV", summary.to_csv(index=False).encode("utf-8-sig"), "class_summary.csv", "text/csv")
    st.info("PASS/NO_SIGNAL chỉ nghĩa là không kích hoạt các quy tắc hiện tại; không xác nhận annotation đúng. Visual metrics phụ thuộc chất lượng ảnh và ngưỡng.")


from core.coverage_ui import render_coverage
with tabs[6]:
    try:
        if not (use_backend and backend_client):
            cov_images, cov_boxes, _ = parse_distribution(zip_data)
        else:
            cov_images, cov_boxes = dist_images, dist_boxes
        render_coverage(cov_images, cov_boxes, file_sig)
    except (ValueError, zipfile.BadZipFile, ET.ParseError) as exc:
        st.warning(f"Không thể phân tích Coverage: {exc}")

