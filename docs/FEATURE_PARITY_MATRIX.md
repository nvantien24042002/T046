# N2-05B Feature Parity Matrix

Date: 2026-10-17  
Assessment: Transition from Track B to Feature Parity Completion & Release Qualification  
Workspaces:
- Backend: `D:\CVAT_Dashboard_Scene_Finder` (branch `backend-dev`)
- Frontend: `D:\CVAT_Dashboard_Scene_Finder_FE` (branch `frontend-dev`)

---

## Status Legend

- **PASS**: Functionality matches original Streamlit behavior, backed by automated test evidence and runtime contract validation.
- **PARTIAL**: Basic functionality, UI structure, or metadata implemented; specific interaction or binary data flow incomplete.
- **NOT VERIFIED**: Feature is a placeholder or not yet validated against automated test suite.
- **FAIL**: Implementation deviates from expected original behavior, calculation, or security boundary.

---

## 1. Overview (📊 Tổng quan) — Route `/` and `/overview`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Total Images Metric** | `len(image_results)` | Displayed in metric card | `summary.images` / `payload.quality_rows.length` | `overview.test.tsx`, `overview-backend.test.tsx` | **PASS** | None |
| **Images Needing Review** | Count of `status == 'REVIEW'` | Displayed in metric card | Count of `quality_rows[].status == 'REVIEW'` | `overview.test.tsx` | **PASS** | None |
| **Total CVAT Boxes** | `len(cvat_boxes)` | Displayed in metric card | Count of `audit_rows` / `classes` | `overview.test.tsx` | **PASS** | None |
| **Boxes Needing Review** | Count of `status.startswith('REVIEW')` | Displayed in metric card | Filtered `audit_rows` | `overview.test.tsx` | **PASS** | None |
| **Class Count** | `len(unique_classes)` | Displayed in metric card | `payload.classes.length` | `overview.test.tsx` | **PASS** | None |
| **Status Distribution Chart** | Plotly pie chart of PASS/REVIEW/ERROR | Custom SVG accessible pie meter | Aggregated from quality rows | `overview.test.tsx` | **PASS** | None |
| **Class Distribution Meters** | Progress bars / bar chart | Accessible `<meter>` elements with aria labels | `payload.classes` | `overview.test.tsx` | **PASS** | None |
| **Dataset ID & Lifecycle** | Dataset ID, archive hash, entries | Read from verified summary | `Summary.public()` via analytics intermediary | `analytics-summary-panel.test.tsx` | **PASS** | None |
| **No-Fallback Enforcement** | In backend mode, failures must stay visible | Renders ErrorState/EmptyState; no fixture substitution | `VITE_DATA_MODE=backend` | `backend-mode.test.tsx`, `overview-backend.test.tsx` | **PASS** | None |

---

## 2. Image Quality (🖼️ Image Quality) — Route `/quality`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Score Summary Metrics** | Average score, count, review needed | Metric group cards with null marker (`—`) | `quality_rows` from verified result | `quality.test.tsx` | **PASS** | None |
| **Full Metrics Table** | 20 columns (blur, brightness, contrast, noise, etc.) | Standard HTML table with unrounded scores | `payload.quality_rows` | `quality.test.tsx` | **PASS** | None |
| **Status Translation** | Vietnamese issue labels (`BLUR` -> `Ảnh mờ`, etc.) | Translated labels mapping | `quality_rows[].issues` | `quality.test.tsx` | **PASS** | None |
| **Mock Scenario Switching** | `baseline`, `empty`, `malformed`, `error` | Controlled `<select>` with state reset | Isolated mock loader | `quality.test.tsx` | **PASS** | None |
| **Backend Integration & Isolation** | Real verified result decoding | Decoded via `decodeQuality`; blocks mock fallback | `GET /analytics/v1/datasets/{id}/results/{id}` | `quality-backend.test.tsx` | **PASS** | None |

---

## 3. Label Check A+B (🏷️ Label Check A+B) — Route `/labels`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Geometry A + Visual B Audit Table** | 20 columns with coordinates, area, aspect ratio, visual metrics | HTML table matching exact cell indices | `payload.audit_rows` | `labels.test.tsx` | **PASS** | None |
| **Filter Scope Synchronization** | Class, priority (`HIGH`/`MED`/`LOW`), `onlyReview` filter | Shared `filterLabels` state across metrics, table, chart | Client-side filter matching Python pandas rules | `labels.test.tsx` | **PASS** | None |
| **Class Summary Breakdown** | `Thống kê theo class (đã lọc)` | Table with review rate % | `labelSummary()` aggregation | `labels.test.tsx` | **PASS** | None |
| **Empty Filter State** | Do not show unfiltered table when 0 matches | Suppresses table; displays `Không có box khớp bộ lọc` | Filter state condition | `labels.test.tsx` | **PASS** | None |
| **Error & Annotation Checks** | Handles missing annotations, empty boxes, malformed | Specific notices and error alerts | Zod validation | `labels.test.tsx`, `page-validation.test.tsx` | **PASS** | None |
| **Backend Integration & Isolation** | Real verified result audit rows | Decoded via `decodeLabels`; blocks mock fallback | `GET /analytics/v1/datasets/{id}/results/{id}` | `labels-backend.test.tsx` | **PASS** | None |

---

## 4. Distribution & Attributes (📈 Phân bố & thuộc tính) — Route `/distribution`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Class Distribution Table** | Box count, image count, share % | Table with formatted shares | `payload.classes` / `payload.distribution_boxes` | `app.test.tsx`, verified-results tests | **PASS** | None |
| **Imbalance Warnings** | Configurable `min_count`, `min_share`, `max_share` | Dynamic inputs + alert list | Client-side calculation | `app.test.tsx` | **PASS** | None |
| **Box Size Grouping** | Bins `<0.1%`, `0.1–1%`, `>1%` | Class aggregation table (mean, min, max) | `distribution_boxes[].area_pct` | `app.test.tsx` | **PASS** | None |
| **Occlusion Breakdown** | `occluded=1` vs `0` per class | Occlusion matrix table | `distribution_boxes[].occluded` | `app.test.tsx` | **PASS** | None |
| **Weather × Time-of-Day Matrix** | Cross-tabulation of weather and timeofday | Two-dimensional matrix table | `distribution_images` | `app.test.tsx` | **PASS** | None |
| **Dynamic CVAT Attributes** | Auto-detected attributes table | Table of discovered attributes and values | `payload.attributes` | `app.test.tsx` | **PASS** | None |
| **CSV Exports** | Class distribution, attributes, box metrics downloads | 3 CSV download buttons | Client CSV encoding | `app.test.tsx` | **PASS** | None |

---

## 5. Review Images (🔎 Review ảnh) — Route `/review`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Mode 1: Metadata Cards** | Status, score, issues, mini metrics dl | Article cards in grid layout | `payload.quality_rows` | `app.test.tsx` | **PASS** | None |
| **Mode 1: Status Filter & Pagination** | `ALL`/`PASS`/`REVIEW`/`ERROR`, page size selector | Dynamic filter select + `Pagination` component | Client-side state | `app.test.tsx` | **PASS** | None |
| **Mode 2: Bounding Box Inspection** | Image selector dropdown + coordinate table | `<select>` image selector + coordinate table | `payload.audit_rows` | `app.test.tsx` | **PASS** | None |
| **Mode 1: Original Image Preview** | `fetch_preview(..., annotated=False)` | `<ImagePreview imagePath={row.filename} annotated={false} />` with blob objectUrl | Intermediary `GET /analytics/v1/datasets/{id}/preview?image_path=...&annotated=false` | `test_analytics_routes.py` (preview suite), `review-preview.test.tsx` | **PASS** | None |
| **Mode 2: Annotated Box Overlay Preview** | `fetch_preview(..., annotated=True)` with caption | `<ImagePreview imagePath={selected} annotated={true} caption="Đỏ: HIGH — Cam: MEDIUM — Xanh: LOW" />` | Intermediary `GET /analytics/v1/datasets/{id}/preview?image_path=...&annotated=true` | `test_analytics_routes.py` (preview suite), `review-preview.test.tsx` | **PASS** | None |

---

## 6. Export Reports (📥 Xuất báo cáo) — Route `/reports`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Image Quality CSV** | Full quality metrics download | Download button + preview table | `payload.quality_rows` | `app.test.tsx` | **PASS** | None |
| **Label Check A+B CSV** | Full audit rows download | Download button + preview table | `payload.audit_rows` | `app.test.tsx` | **PASS** | None |
| **Class Summary CSV** | Group by label: total boxes, review boxes, review rate % | Aggregated download button + preview table | Aggregated from `audit_rows` | `app.test.tsx` | **PASS** | None |
| **Box Distribution CSV** | Box coordinates, occlusion, area | Download button + preview table | `distribution_boxes` | `app.test.tsx` | **PASS** | None |

---

## 7. Coverage & Slice (🎯 Coverage & Slice) — Route `/coverage`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Observed Class Coverage** | Objects, images, `image_coverage_pct`, `object_share_pct` | Section 1 table matching exact aggregation rules | `payload.distribution_boxes` & `payload.distribution_images` | `coverage.test.tsx` | **PASS** | None |
| **Target Setting Modes** | "Thêm một slice", "Tạo nhiều slice tự động", "Nhập JSON" | Section 2 interactive modes with unit, target, weight | Client state / targets configuration | `coverage.test.tsx` | **PASS** | None |
| **Observed vs Target Evaluation** | `Tổng slice`, `Slice đạt`, `Slice thiếu`, `Weighted coverage`, support, gap, attainment % | Section 4 metric cards and evaluation table | `evaluate_targets()` logic ported losslessly | `coverage.test.tsx` | **PASS** | None |
| **Slice Recommendations** | Priority action text sorted by `weighted_gap` desc, then `gap` desc; metadata warning | Section 5 table and warning alert | `recommendations()` logic | `coverage.test.tsx` | **PASS** | None |
| **Coverage CSV & JSON Export** | Download `coverage_results.csv`, `slice_recommendations.csv`, `coverage_targets.json` | 3 dedicated export buttons using `downloadCSV` and JSON blob | Client CSV & JSON encoding | `coverage.test.tsx` | **PASS** | None |

---

## 8. Scene Finder (🔎 Scene Finder) — Route `/scene-finder`

| Feature | Original Streamlit Behavior | React Implementation | Backend Contract | Test Evidence | Status | Remaining Gap |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **Weather & Time-of-Day Filter** | Multiselects/selects for weather and timeofday | Dynamic dropdown selectors with counts | `distribution_images` tags | `scene-finder.test.tsx` | **PASS** | None |
| **Keyword Search** | Substring filter on `image_name` | Real-time text search input | `distribution_images[].image_name` | `scene-finder.test.tsx` | **PASS** | None |
| **Summary Metrics** | `Ảnh khớp bộ lọc`, `Tổng ảnh trong XML`, `Ảnh có metadata chưa rõ` | Overview metric cards with exact counts | Aggregated image tag counts | `scene-finder.test.tsx` | **PASS** | None |
| **Filtered Image Table** | `image_id`, `image_name`, `weather`, `timeofday` | Clean HTML table with pagination | Filtered image array | `scene-finder.test.tsx` | **PASS** | None |
| **CSV Export** | Download `scene_filtered_images.csv` | Export button using `downloadCSV` | Client CSV encoding | `scene-finder.test.tsx` | **PASS** | None |
| **Paginated Preview Grid** | Cards with image name, weather/timeofday tags, and image preview | Grid of cards with `<ImagePreview>` integration | Preview endpoint / adapter | `scene-finder.test.tsx` | **PASS** | None |

---

## Parity Summary by Category

| Category | Total Features | PASS | PARTIAL | NOT VERIFIED | FAIL |
| :--- | :---: | :---: | :---: | :---: | :---: |
| **1. Overview** | 9 | 9 | 0 | 0 | 0 |
| **2. Image Quality** | 5 | 5 | 0 | 0 | 0 |
| **3. Label Check** | 6 | 6 | 0 | 0 | 0 |
| **4. Distribution** | 7 | 7 | 0 | 0 | 0 |
| **5. Review** | 5 | 5 | 0 | 0 | 0 |
| **6. Reports** | 4 | 4 | 0 | 0 | 0 |
| **7. Coverage & Slice** | 5 | 5 | 0 | 0 | 0 |
| **8. Scene Finder** | 6 | 6 | 0 | 0 | 0 |
| **Total** | **47** | **47** | **0** | **0** | **0** |
