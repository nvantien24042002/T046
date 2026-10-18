# Frontend audit — fix_fe

Initial branch: frontend-dev; commit: 6926b46f05817a12bdef3b79059dedae399fb561.
Created fix_fe without discarding the existing backend/integrated_api.py CORS/auth edit. That edit is outside this task and remains untouched.

## Functionality inspected before implementation

All eight production pages are wrappers around BackendDashboard; historical reference pages/mocks are test-only. React Router supplies navigation, BrowserSessionProvider owns the selected dataset/result and request generations, Zod verifies API responses, Recharts renders backend chart series. Upload is an additional preserved route.

| Page | Existing backend products/interactions preserved |
| --- | --- |
| Overview | Summary capability gating, backend KPIs, status/priority charts, class table |
| Image Quality | Status filter, image scores/issues, effective thresholds, status/issue charts, export |
| Label Check | Class/priority/review filters, audit results, class review statistics, export |
| Distribution | Classes/attributes/imbalance, box sizes/occlusion/weather/time of day, backend threshold parameters, charts/export |
| Review Images | Backend preview references, image and annotation preview, quality/audit tables |
| Reports | Backend quality/label/class/box report tables and server CSV exports |
| Coverage & Slice | Observed coverage, explicit target JSON, backend evaluation/recommendations |
| Scene Finder | Backend class/weather/time filters, keyword search, matching image references, export |

Confirmed defects: raw filename/path/ID columns and nested values rendered by generic tables; filename select/alt text in previews; dataset IDs in catalog/result/success UI; raw dynamic notes/errors; no image decode error handler; catalog prematurely claimed empty before loading; tables can expand the page; navigation consumes small screens; mock mode was mislabeled as backend; file drop accepted while upload was busy; session bootstrap promise stayed resolved after session expiry.

Design: preserve Segoe UI, navy #173b67, blue #175ba0, canvas #f3f6fa, ink #23364b and white panels. Keep the existing shell, pages, chart products, filters and backend meaning. Add collapsible mobile navigation, scoped table scrolling, flexible controls, image gallery and modal. No new business calculations or dependencies.

## Confirmed contract and integration boundaries

Product uses same-origin /api/browser/v1 (cookie session + X-Requested-With: N2-05B). Confirmed in the current backend: session, datasets/catalog, chunked ZIP upload, receipts/status, complete/cancel, validate, analyze, summary/results, dashboard.v1, authenticated preview and server CSV export. The Vite proxy targets port 8011. Preview uses the exact server-provided image reference as an API parameter; object URLs render returned image bytes. It does not construct an image resource URL from a filename.

Dataset/result fingerprint validation and request generations remain authoritative. Current API exposes internal IDs/references but no user-facing image/dataset names: use local catalog order only as navigation labels and keep references private. Filename keyword search remains a server capability; matching filenames are never displayed. Preview rows lacking a resolvable server reference show an explicit unavailable state.

Limits: current browser contract provides projection tables rather than histogram bins for box sizes/occlusion; preserve those statistics without manufacturing bins. Validation is a synchronous request with lifecycle status; no invented percentage of server work. Targets are not persisted by the backend; keep explicit user input and unsupported states. Immutable analytics provider is not configured for product browser sessions. No role-based access or persisted review decisions are added. Exports remain backend-owned artifacts; frontend does not rewrite their business content.

Unit/mocked tests are not real integration evidence. Browser evidence and actual command results are reported separately after execution.
