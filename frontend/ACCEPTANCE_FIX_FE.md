# N2-05B frontend implementation and acceptance

Date: 2026-10-18, Asia/Saigon.
Repository: D:/CVAT_Dashboard_Scene_Finder_FE.
Initial branch: frontend-dev. Initial/final commit: 6926b46f05817a12bdef3b79059dedae399fb561.
Final branch: fix_fe, created safely from the existing checkout.

The initial audit is in AUDIT_FIX_FE.md, including the per-page functionality map and verified API responsibilities. All eight pages, routes, filters, parameters, backend charts/statistics, exports and upload workflow remain. No business metrics are computed or fabricated by the new frontend presentation. Historical reference/mock implementations remain test-only. Explicit development mode is now visibly distinct and cannot upload or silently replace API results.

## Changes and reasons

| Frontend file | Change |
| --- | --- |
| src/App.tsx | Collapsible mobile navigation; explicit mode label; preserve all routes and focus-on-navigation |
| src/api/browserSession.ts | Clear stale session bootstrap after HTTP 401 so retry can obtain a fresh session |
| src/components/BackendDashboard.tsx | Suppress internal columns; safe dynamic text/nested cells; accessible filter names; scoped table scroll; gallery integration; preserve backend parameters/targets/search |
| src/components/presentation.ts (new) | Presentation-only protection for filenames, paths, hashes, credentials, internal field names and diagnostic columns |
| src/components/ImageGallery.tsx (new) | Click image directly to open native modal; Escape dismissal; annotated preview; restore opener focus; StrictMode-safe lifecycle |
| src/components/ImagePreview.tsx | Safe alt/captions and friendly network/decode errors; retain authenticated blob fetch and URL cleanup |
| src/components/BrowserDatasetPanel.tsx | Neutral catalog-order selection labels instead of IDs; selected state; correct initial loading and failed-catalog states |
| src/components/BrowserDatasetUploader.tsx | Hide selected filename/internal result ID; prevent file drop while busy; remove unverified server-size/memory claim |
| src/components/ResultStatus.tsx | Replace raw dataset/result IDs with non-identifying selection status |
| src/components/States.tsx | Suppress private text in shared error UI |
| src/components/Charts.tsx | Disable decorative animation for stable/reduced-motion rendering; readable backend-value legend |
| src/styles.css | Responsive shell, controls, KPI grids, table regions, gallery and viewer; retain existing palette/type |
| src/tests/presentation-privacy.test.tsx (new) | 17 tests covering sensitive strings/nested data, all eight pages, gallery interaction/focus/decode errors and safe accessibility labels |
| src/tests/browser-session-api.test.ts | Add expired-session retry regression |
| src/tests/browser-session-races.test.tsx | Assert neutral catalog labels; preserve request-generation race coverage |
| src/tests/app.test.tsx | Assert explicit development-mode labeling in its mock harness |
| src/tests/dataset-integrity.test.tsx | Assert non-identifying selection status disappears during switching; preserve all stale-result/error assertions |
| scripts/isolated_backend.py (new) | Actual backend code with random test-only credentials and a fresh external temporary storage root |
| scripts/run_browser_acceptance.py (new) | Port checks, isolated servers, complete Windows descendant-process cleanup |
| scripts/browser_acceptance.py (new) | Chrome workflow, 32 page/viewport checks, screenshots, CSV, empty filters, retry and two-dataset switching |
| AUDIT_FIX_FE.md (new) | Initial architecture/functionality audit and integration boundaries |
| ACCEPTANCE_FIX_FE.md (new) | This execution report and final inventory |

## Page and viewport results

Every row passed at 1440, 1024, 768 and 375px using real dashboard.v1 responses from the actual backend with synthetic uploaded ZIP input (not fabricated result responses).

| Page | Verified preserved behavior |
| --- | --- |
| Overview | Backend KPI values/status charts/class summary; correct result after dataset switch |
| Image Quality | Status filter, real empty response and recovery, thresholds/results/chart/export |
| Label Check | Backend audit/class/priority controls and results |
| Distribution | Backend charts, attributes, imbalance, sizes, occlusion, weather/time and threshold parameters |
| Review Images | Actual image bytes, clickable gallery, native viewer, Escape, no filename captions |
| Reports | Backend report tables and real server CSV download |
| Coverage & Slice | Observed coverage and explicit target input; no invented targets or recommendations |
| Scene Finder | Server filters/search and actual image gallery/viewer |

1440px: desktop sidebar and three-column image gallery. 1024px: narrower sidebar and two-column gallery. 768px: collapsible navigation and two-column gallery. 375px: collapsible navigation, one-column gallery, touch controls, scoped horizontal table scrolling and fitting modal. All 32 checks found no page-wide horizontal overflow. Screenshots were visually reviewed at all four widths; screenshot review additionally drove fixes for squeezed mobile table text and false redaction of QA/QC headings.

No backend-returned fixture image names, uploaded source names, storage paths, or internal IDs were found in rendered page text or tested alt/title/aria-label values. References remain in API data only. Image src uses authenticated returned blobs rather than guessed storage URLs. Filename/path-like free text and private nested fields are suppressed; business metrics remain backend values. The native file picker is user input, not a backend-provided label.

## Visual QA and Investigation Pass (Priorities 1 - 6)

### Priority 1: Gray and Noisy Image Previews Root Cause
- **Source Fixture Trace:** Traced to backend synthetic fixture generator `tests/fixtures.py` in `make_zip()`.
  - `flat.png` is constructed as: `cv2.imencode(".png", np.full((64, 64, 3), 100, dtype=np.uint8))[1].tobytes()`, producing a 64x64 solid gray square.
  - `texture.png` is constructed as: `cv2.imencode(".png", rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))[1].tobytes()` using `np.random.default_rng(20261016)`, producing 64x64 uniform pseudo-random RGB noise.
- **Transport & Decoding:** The backend endpoint `/api/browser/v1/preview` streams these exact PNG bytes with `Content-Type: image/png`. The frontend fetches the binary blob via authenticated `fetch()` and passes it to `URL.createObjectURL(blob)`, rendered in standard HTML `<img>` elements.
- **Root Cause & Conclusion:** The previews are not corrupted, misdecoded, or defectively rendered. They accurately render the exact pixel content of the backend fixture generator. In `ImagePreview.tsx`, the hook dependencies and abort controller handling were hardened to avoid false image load errors or abort warnings during rapid renders.

### Priority 2: Coordinate `-1` in QA/QC Table
- **Source Fixture Trace:** In `tests/fixtures.py`, `XML` includes the annotation box: `<box label="person" xtl="-1" ytl="2" xbr="20" ybr="30"/>`.
- **API Contract Verification:** The backend parser records `xtl: -1.0`, and the backend QA/QC check flags this annotation with the issue `OUT_OF_BOUNDS`. In `frontend/src/api/verifiedResults.ts`, `auditRowSchema` specifies `xtl: metric` (`z.number().nullable().optional()`).
- **Root Cause & Conclusion:** `-1` is a legitimate, authentic annotation coordinate value from the dataset, intentionally placed outside image bounds to exercise the backend QA/QC engine. It is not a UI bug, placeholder, or sentinel value fabricated by frontend code. Preserving this backend metric verbatim complies strictly with the contract.

### Priority 3: Visual Hierarchy and Chart Polish
- **Chart Card & Grid System:** Wrapped charts in responsive `.chart-card` containers within `.dashboard-charts-grid`, laying out side-by-side on wide screens and stacking on mobile.
- **Series Labels & Legends:** Replaced generic "Giá trị" tooltip names with contextual labels (`seriesLabel` / `seriesName`, e.g., "Số lượng", "Số lượng box", "Số lượng ảnh").
- **Bar Sizing & Grids:** Applied `maxBarSize={48}` and `radius={[4, 4, 0, 0]}` to prevent single bars from over-stretching across wide desktop viewports. Added subtle horizontal grid lines (`#e2e8f0`).
- **Dynamic Label Angling:** Implemented dynamic angle calculation (`-40°` when category count is high, `-20°` for long labels) with `textAnchor="end"` and dynamic bottom margins, preventing long issue labels (e.g. `WEAK_BOUNDARY_SUPPORT`) from overlapping or clipping.
- **Semantic Color Mapping:** Rendered individual bar cells with semantic fills (PASS/LOW in blue, REVIEW/MEDIUM in amber `#976300`, ERROR/HIGH in red `#842b2b`).

### Priority 4: Mobile Table Readability at 375px
- **Cell Alignment:** Applied `vertical-align: top` to all table headers and cells (`.table-scroll th, .table-scroll td`), ensuring multi-line wrapped text (such as long issue lists) aligns neatly at the top rather than centering adjacent numbers vertically in empty space.
- **Tabular Numerics:** Added `font-variant-numeric: tabular-nums` to preserve vertical alignment of digits.
- **Mobile Density:** Under `@media (max-width: 600px)`, reduced cell padding to `8px 10px`, font size to `13px`, and adjusted min-width to `3.5rem` for short numeric/metric columns (`xtl`, `occluded`, `boxes`, `share_pct`).
- **Overflow Protection:** Compact tables (3-4 columns) fit neatly within 375px with zero horizontal scroll. Wide multi-column tables scroll smoothly within their local `.table-scroll` container without causing page-wide horizontal overflow.

### Priority 5: 32 Page/Viewport Combinations Rechecked
- Verified all eight pages across 1440px, 1024px, 768px, and 375px viewports with real backend execution, synthetic dataset upload, modal preview interactions, and dataset switching.

### Priority 6: Privacy and Sensitive Identifier Suppression
- Verified across all rendered views and automated privacy test suites that filenames, internal paths, storage keys, and sensitive IDs remain strictly suppressed from page text, headings, and accessibility attributes.

## Executed verification

Working directory for these commands: D:/CVAT_Dashboard_Scene_Finder_FE/frontend.

```powershell
npm run typecheck
npm run lint
npm test -- --reporter=dot
npm run build
$env:N205B_BACKEND_SOURCE='D:\CVAT_Dashboard_Scene_Finder'
& 'D:\CVAT_Dashboard_Scene_Finder\.venv\Scripts\python.exe' -B scripts/run_browser_acceptance.py
```

Final results: typecheck exit 0; lint exit 0; Vitest 26 files / 344 tests passed, 28.89 seconds; production build exit 0, 3.52 seconds. Browser runner exit 0: 32 real-backend page/viewport checks, preview decode/modal/Escape, real CSV download, empty-filter response, injected HTTP 503 with no old metrics and successful retry, and switching between actual uploaded datasets of 2 and 1 image. Zero browser page errors.

The 503 response was deliberately injected only for the retry check. Main viewport/workflow checks used unmodified real backend responses and real previews. Session/dataset races and image decode failures also have unit coverage. This is small synthetic-dataset integration evidence, not large-file/load testing or production release evidence.

Earlier checks caught and corrected lint/type/test-stub issues, obsolete ID/mode UI assertions, a StrictMode native-dialog lifecycle issue, a test runner worker-import setup problem, and insufficient SPA waiting/accessible filter names in the browser harness. These were corrected and the final commands rerun; no failed check is counted as passed. The bundled with_server helper was initially attempted, but its Windows cleanup left test descendants alive. Only identified test-owned processes were stopped; the retained runner now owns and cleans their complete process trees.

Build warnings remain: two dependency annotation warnings from Zod/Rollup and a JavaScript chunk of about 775 kB (about 235 kB gzip). No dependencies, package manifest or lockfile changed. Optional future code splitting is outside this targeted fix.

Evidence: .cache/acceptance/browser-results.json records checks and API statuses; .cache/acceptance/{page}-{width}.png contains 32 screenshots; overview/review viewport captures are included. Files are intentionally ignored local review artifacts, not committed assets.

## API limitations / integration blockers

No missing endpoint blocks the existing eight-page workflow tested here. The contract still lacks persisted QA/review decisions, persisted coverage targets, per-section composite capability metadata, and a configured immutable browser analytics provider. These remain explicit unavailable/unsupported states, not simulated features. Box size and occlusion are server statistics/tables; no client-derived histogram bins were invented. Preview transport takes an exact backend reference internally; an opaque media-reference API would require a separately approved backend contract change. Backend CSV content remains backend-owned and is not rewritten by this presentation task.

## Safety and review status

Only frontend files in this frontend worktree were edited. The seven-line pre-existing backend/integrated_api.py CORS/Bearer diff is unchanged and must not be confused with this task. The separate backend repository source, runtime databases, uploads, caches, datasets, backups and historical hash manifests were not modified or opened as data. The browser server used fresh external temporary storage; all test listeners were cleaned up. No stage, commit, push, merge, deployment or destructive Git command occurred. Branch remains fix_fe and work is left for owner review.

Final git diff --check: passed. No staged files.

## Final git status --short

```text
 M backend/integrated_api.py
 M frontend/src/App.tsx
 M frontend/src/api/browserSession.ts
 M frontend/src/components/BackendDashboard.tsx
 M frontend/src/components/BrowserDatasetPanel.tsx
 M frontend/src/components/BrowserDatasetUploader.tsx
 M frontend/src/components/Charts.tsx
 M frontend/src/components/ImagePreview.tsx
 M frontend/src/components/ResultStatus.tsx
 M frontend/src/components/States.tsx
 M frontend/src/styles.css
 M frontend/src/tests/app.test.tsx
 M frontend/src/tests/browser-session-api.test.ts
 M frontend/src/tests/browser-session-races.test.tsx
 M frontend/src/tests/dataset-integrity.test.tsx
?? frontend/ACCEPTANCE_FIX_FE.md
?? frontend/AUDIT_FIX_FE.md
?? frontend/scripts/
?? frontend/src/components/ImageGallery.tsx
?? frontend/src/components/presentation.ts
?? frontend/src/tests/presentation-privacy.test.tsx
```
