# CVAT Frontend — N2-05B

## Current dashboard contract

Production routes for all eight groups use `BackendDashboard` and `dashboard.v1` projections from the main backend repository. The default mode is `backend`, using `VITE_API_BASE_URL` (default port 8011) and `VITE_ANALYTICS_BASE_URL` (default port 8012). These defaults do not prove a service is running.

Direct projections require the backend analysis fingerprint; immutable projections require the backend result ID and payload hash. Missing capability, API errors, missing identity and timeouts display explicit states without sample fallback. Coverage requires explicit user targets; targets are not persisted. Authorization by role and persisted review decisions are not implemented.

`src/reference` and the historical fixture tests preserve earlier implementations for comparison only. They are not imported by production pages. Older milestone notes below describe that historical harness, not the current production data path. TestClient integration is not browser-to-backend verification.

### Composite-page capability gating

Overview, Review and Reports require a ready summary for the active dataset. All require `image_quality`; datasets with annotations also require `label_audit`, and Overview/Reports additionally require `distribution`. Disabled or unconfirmed prerequisites block the entire composite page, including exports and preview, without requesting a projection or deriving replacements from raw payloads. Unrelated capabilities (`coverage`, `scene_finder`) do not block these pages. Images-only datasets do not require annotation capabilities.

This is conservative UI gating, not authorization. The current backend contract lacks per-section capability metadata, so FE cannot safely render a partially supported composite page. Backend `state=unsupported` also suppresses every product/control regardless of contradictory fields in that response.

### Prerequisites for a real browser check

- Use `http://127.0.0.1:5173` (Vite strict port), matching the analytics intermediary's allowed origin. Its current CORS policy does not accept `localhost` or preview port 4173. The integrated backend separately permits local 5173/4173 origins.
- Supply reachable `VITE_API_BASE_URL` and `VITE_ANALYTICS_BASE_URL` before starting/building Vite; these public URLs are not credentials. Default URLs are loopback 8011 and 8012. On a remote browser, loopback refers to the browser's machine.
- Backend services must already be configured with private storage and existing credentials. Do not put owner keys or grants in Vite environment variables. Direct access needs an authorized upload capability, ready dataset summary and analysis fingerprint. Immutable access needs an authorized result grant and analytics service composed with `ResultStore` and the result projection route.
- Verify actual browser preflight and POST responses, `dashboard.v1` identity, preview and CSV after dataset switches. Successful mock/TestClient tests or a successful build do not establish browser-to-backend connectivity. No production service or new secret is needed for these unit checks.

Independent React + TypeScript + Vite application. All frontend configuration lives here; root manifests and Python sources are unchanged.

## Environment and commands

Node.js >=22.12 and npm are required (implemented with Node 24.21.0 / npm 11.19.0).
Run from `D:\CVAT_Dashboard_Scene_Finder_FE\frontend`:

```powershell
npm ci --cache .cache/npm --no-audit --no-fund --ignore-scripts
npm run dev
npm run typecheck
npm run lint
npm test
npm run build
npm run preview -- --host 127.0.0.1 --port 4173 --strictPort
```

Development uses http://127.0.0.1:5173 with strict port selection. Deployments must route unknown frontend paths to index.html for BrowserRouter deep links. Production preview is not a production server.

## Explicit data modes

Default: `backend`. Historical sample scenarios are restricted to the test harness; production dashboard pages request backend projections and do not substitute samples on failure.

Set variables for the current PowerShell process before starting Vite:

```powershell
$env:VITE_DATA_MODE = 'backend'
$env:VITE_API_BASE_URL = 'http://127.0.0.1:8011'
npm run dev
```

Use `VITE_DATA_MODE=mock` to return to mock mode. Invalid modes fail explicitly. These public settings may also be supplied in frontend/.env.local (see .env.example). Restart Vite after changing settings. Never put secrets, owner keys, or credentials in VITE variables; they are bundled into browser code.

Backend mode displays a manually triggered GET /health check only. This contract is confirmed in backend/integrated_api.py: status=ok, stage:string, ready_datasets:boolean. No datasets are retrieved. Real errors never fall back to mock data. Response messages are deliberately generic to avoid leaking untrusted server contents. HTTP status, invalid response, network, timeout, and browser cancellation have separate API error kinds.

The current backend checks loopback host/client and allowed Origin. Its default origins do not include port 5173. Backend ownership must approve an allowed-origin configuration for real local integration. M1 does not change CORS or install a proxy that bypasses origin policy. A failed check can therefore indicate CORS as well as service availability.

## Structure and routes

- src/App.tsx: semantic layout, navigation, route placeholders, route-change focus, Not Found.
- src/pages/Dashboard.tsx: explicit sample states or manual backend health check.
- src/api/: typed fetch boundary, runtime Zod validation for the confirmed health response.
- src/mocks/: deterministic UI-only fixtures.
- src/components/: shared loading/error/empty states.
- src/tests/: route, mock-state, API failure, cancellation, and configuration tests.

Routes: /, /distribution, /quality, /labels, /review, /reports, /coverage, /scene-finder. All analytical destinations are explicitly unfinished. Scene Finder is separately addressable in the shell; no new workflow or endpoint is implemented.

Accessibility: skip link, semantic landmarks, labeled selector, native keyboard controls, visible focus, active route indication, route-change focus, live loading/health feedback, alert errors, responsive navigation. Browser-level accessibility and visual QA remain separate from jsdom tests.

## Deliberately deferred contracts

- Trusted upload-session bootstrap and ownership; no authentication implementation.
- Dataset selection and exact result/threshold identity.
- Coverage execution: backend endpoint or an explicitly approved fixture-tested port.
- Scene Finder endpoint/filter contracts; no endpoint invented.
- Long-running task status, cancellation semantics, retry/reconciliation.
- Preview canonical identity, MIME correctness, limits, priority overlays.
- Attempt IDs and cleanup policy; analytics pagination and attribute imbalance rules.

No Python calculations, thresholds, status meanings, null conventions, or CSV formatting have been reimplemented. Streamlit remains the reference. No chart/server-state/table dependency is installed until a feature needs it. The single health action uses local request state; TanStack Query can be introduced with dataset integration.

## Verification scope

Vitest uses jsdom, React Testing Library and user-event. Tests cover all placeholder routes, navigation/focus, Not Found recovery, deterministic sample states, data-mode configuration, URL restrictions, HTTP/network/response errors, timeout and request cancellation. These tests do not establish backend integration, browser layout, or analytics parity. No live backend calls are required by the tests.
## M1.1 error handling contract

`readHealth` always returns a promise, including when URL construction or validation fails. The dashboard's existing rejection handler shows the generic configuration guidance without exposing the configured URL or credentials. URL security restrictions remain unchanged.

The request deadline covers both fetching headers and consuming the JSON body. If that deadline fires, the failure is `ApiError` with kind `timeout`. An explicit caller abort observed before the deadline is kind `cancelled`. The existing deadline flag takes precedence if the deadline has fired by the time a non-ApiError is classified. Neither outcome confirms cancellation of server-side work. Both retain undefined HTTP status metadata; HTTP failures alone carry their response status. Malformed JSON and runtime schema failures remain kind `response` when the internal request signal has not been aborted. Request deadlines and caller abort listeners are cleared on completion.

M1.1 regression tests use fake timers and stalled response streams to cover aborts during body consumption. Dashboard tests use the real health client with a stubbed fetch to verify invalid configuration, schema rejection, successful retry and no mock fallback. No live backend is called.
## M2.1 Overview (fixture-backed; real dataset integration blocked)

Open /overview or use Tổng quan dữ liệu in the existing navigation. The M1 landing page and its health check remain at /. Image Quality and Label Check are also fixture-backed; other feature routes remain placeholders.

Confirmed source contracts: GET /api/v1/datasets/{dataset_id}/summary and GET /api/v1/datasets/{dataset_id}/results in backend/integrated_api.py. Overview validates only consumed fields, not an invented endpoint. It never calls analyze or any live endpoint. Real dataset selection/capability handoff is not approved; Backend mode stops with an explanatory empty state and never loads mock data. No dataset credential fields, storage, URL tokens, or authentication workaround were added.

Mock mode uses src/mocks/overview-baseline.json: quality, audit and classes copied from tests/baseline/expected.json. Fixture identity and ready-summary projection are local UI scaffolding. images=2 is confirmed by test_integrated_api.py; the XML contains a third, missing image, so class image counts must not be summed into archive-image totals. Images-only removes audit/classes; empty/error variants are synthetic UI cases, not claims that validation accepts empty archives. Switching scenarios discards old results; stale promises are ignored. Retry repeats the selected mock scenario, so the deliberate error scenario remains an error until another scenario is chosen.

Totals follow app.py Overview: image count from quality rows, REVIEW exact match for image reviews, all audit rows for box totals, REVIEW prefix for box reviews, unique labels for class count. No geometry filtering or score recalculation. Class counts/images/share_pct come unchanged from Python class_distribution. Accessible native meters and tables represent distributions without adding chart dependencies. Status and priority tables preserve source values; missing annotations retain quality counts and explicitly suppress label distribution.

Tests compare the local fixture against the read-only Python baseline, validate schemas/identity, test totals including A_ONLY/NO_SIGNAL, no annotations, loading/empty/error/retry, stale selection and Backend-mode isolation. These fixture tests do not establish real API integration or browser visual/keyboard QA.
## M2.2 Image Quality (fixture-backed)

/quality now renders Image Quality. Other placeholder routes remain unchanged. Uses only quality_rows fields confirmed in core/image_quality.py and the integrated API results bundle. Runtime validation rejects malformed rows, unknown quality statuses, nonfinite metrics and invalid types; null signals remain null, not zero. Empty results are a synthetic UI scenario, with no average rather than a manufactured 0.

Provenance: reuses overview-baseline.json quality rows, copied from tests/baseline/expected.json. That baseline was captured using tests/baseline/capture.py IMAGE_CFG. Defaults are displayed read-only and compared with core/image_quality.py by tests: blur=100, dark=40, bright=215, contrast=25, noise=10, minimum width=320 and height=240, black=20, white=245. No frontend scoring, threshold re-evaluation or noise calculations are implemented. Scores 50 and 85, mean 67.5, REVIEW=2, LOW_RESOLUTION=2, BLUR=1, LOW_CONTRAST=1. Original values and nullable noise remain intact. READ_ERROR is source-confirmed; a source-derived error row is used only in tests, not presented as a captured baseline result.

The page shows status counts, mean (one decimal, matching the reference presentation for the baseline), per-image score meters, brightness/contrast scatter, issue counts and all confirmed row columns. Detailed values are not rounded; nulls use an em dash. Internal error text is retained in decoded data but not rendered. SVG points omit only null metric pairs. Plotly histogram binning and box-plot rendering have not been recreated; their numerical/visual parity is not claimed. No charting dependency was added.

All fixture snapshots carry a local configuration key pinned to these exact defaults. It is not a backend request_fingerprint. Other configuration keys are rejected; changing fixture scenarios clears visible results and late prior completions are ignored. Threshold editing is deliberately unavailable: there are no approved alternate-configuration results, and displaying recomputed or stale scores would misrepresent Python output.

Backend mode remains blocked, without loading fixtures or calling results/analyze. Session handoff, exact request-fingerprint/configuration mapping, and any real analysis timing/cancellation behavior remain unresolved contracts. Real failures are never replaced with sample results. Deliberate error/malformed scenarios remain errors on retry until a different scenario is chosen. No live backend calls or browser visual/keyboard QA were performed.
## M2.3 Label Check A+B (fixture-backed)

/labels renders baseline-backed Label Check. Inspected app.py Label Check, core/label_audit.py run_label_audit/DEFAULT_LABEL_CFG, core/cvat_parser.py, backend/integrated_api.py bundle serialization, ui/backend_responses.py and baseline/capture.py. Reuses overview-baseline.json audit rows, copied from tests/baseline/expected.json; tests compare the entire decoded baseline and all default thresholds to read-only Python sources. No calculation, score, flag, threshold or priority rule has been reimplemented.

Geometry A and Visual B issues remain separate columns. Statuses are source-defined REVIEW, REVIEW_A_ONLY, NO_SIGNAL and A_ONLY; image_available does not guarantee B metrics: invalid boxes or small ROIs can have null signals even when an image exists. Nulls are never replaced by zero. Internal image_error strings remain in decoded data but are not rendered. Baseline totals without filters: boxes=5, reviews=4, HIGH reviews=4, image_available=4, issue occurrences=17. Classes: bus=1 (0 review, 1 image), car=2 (2 review, 1 image), person=2 (2 review, 2 images). Baseline contains A_ONLY; NO_SIGNAL/REVIEW_A_ONLY parser tests exercise source enums only and are not claimed as captured numeric fixtures.

Filter scope is an explicit change requested for M2.3: Streamlit applies filters only to its detail table, keeping metrics/charts/class summary dataset-wide. This frontend applies class, priority and review-only filters consistently to metrics, issue counts, class/status tables, scatter and detail rows. OR within selected values, AND across fields, no selection means no matches; review-only uses status.startswith('REVIEW'). Default review-only=true yields 4 boxes, 4 reviews, 4 HIGH reviews, 4 image_available. The separate sentence stating dataset size is clearly unfiltered context. Reset restores all classes/priorities and review-only=true. Class review percentages match pandas round(1), including half-even ties; tests cover 1/16=6.2% and 3/16=18.8%, while baseline ratios are exactly 0/100.

Mock scenarios for missing annotation, no boxes, empty, malformed and errors are synthetic UI cases. annotations=false establishes missing annotations; annotations=true with empty audit_rows cannot distinguish an XML without boxes from another empty audit result. The UI documents that uncertainty rather than inventing an API reason field. Retry repeats the selected scenario. Late prior completions are ignored and snapshots with another local configuration key are rejected. Defaults are read-only and keys are local fixture identities, not server fingerprints.

Native SVG represents filtered edge density versus foreground contrast, skipping null pairs; exact area/aspect/visual values remain in the table. Plotly histogram and aspect-ratio box-plot parity is not implemented or claimed. Backend mode remains blocked pending session handoff and exact configuration/results contracts, and never invokes fixtures or endpoints. No live backend, upload, preview or later-milestone work is included. Browser visual QA and real keyboard behavior remain unverified.
Vitest uses at most two workers to avoid jsdom resource contention as the suite grows; default test timeouts are unchanged.

## Integration preparation while BE contract is under review

No finalized contract document was found in the inspected tree. Existing backend source is evidence for current fields only, not approval of a proposed integration/security contract. All three pages retain their existing fixture-only behavior and backend-mode blocking. No new routes or selection controls were introduced. Mock modules remain statically imported; backend mode does not invoke their loaders or render sample results.

Independent work completed:

- api/datasets.ts validates observed lifecycle states and ready flags. not_requested permits no dataset_id; later states require it. Ready summaries validate counts/hash/capabilities; error_code and xml_records are optional only where current responses omit them. Null metrics remain null in existing page schemas, and missing required metrics are rejected rather than fabricated.
- api/errors.ts normalizes existing error.code and FastAPI detail envelopes. Only explicitly allowlisted codes become ApiError metadata; arbitrary details, unknown code strings and raw response bodies are never shown or retained as messages. Malformed error bodies still produce HTTP errors; body-read aborts retain timeout/cancellation semantics.
- api/analytics.ts is a transport-injected read-only adapter with no concrete network/auth implementation. It validates a ready summary, dataset identity, a required 64-character request_fingerprint and the relevant page projection before returning data. Verification means matched shape/identity, not cryptographic authenticity or correctness of the analysis. Legacy results missing fingerprints are not verified. Expected fingerprints must come from an independently trusted selection, not be copied out of the response being checked. Expectation values are snapshotted before awaits.
- Separate configurationId/resultId in adapter options are frontend verification requirements only. They are not implemented wire fields. Any such requirement produces ContractPendingError before transport is called, including when mock payloads contain proposed configuration_id/result_id. These IDs are never treated as aliases for request_fingerprint; mismatch verification awaits an approved contract.
- api/latestSelection.ts is a tested preparation utility for future selection wiring. It clears prior data via loading events, cancels superseded requests and discards late success/failure even when transport ignores cancellation. Dataset/config changes and same-selection retries use a new generation. Empty results stay empty. Existing pages keep their current stale-request guards; this utility is not a hidden real-backend integration.
- All current page loader outputs are validated again immediately before rendering, with decoder failures routed into existing visible error states. Baseline calculations, filtering and presentation are unchanged.

Still blocked:

| Decision / work | Owner |
| --- | --- |
| Secure dataset/session/capability handoff, scope and lifetime | Backend/security owner with FE agreement |
| Exact canonical configuration-to-fingerprint mapping; separate configuration/result IDs and version rules | Backend contract owner |
| Approved disposable dataset environment, origins/CORS and test authorization | Backend/test-environment owner and user |
| Concrete authenticated transport, selection controls and adapter wiring after approval | Frontend owner |
| Browser visual, responsive, keyboard and real integration QA | Frontend/QA after authorization |

No live backend was called. No proposed endpoints or authentication flow were added. Charts and broader payload/performance limitations from M2 remain. The new mocked checks prepare integration boundaries; they do not prove real API compatibility, security approval or live dataset access.
## Approved Track A frontend boundary — analytics.v1

Track A is approved for the grant/intermediary boundary only. Track B remains unapproved/unimplemented. No deployed intermediary route is assumed and no live API was contacted. Legacy adapters, fixture pages, row decoders and their regressions are unchanged.

The separate src/api/analyticsV1 entry point exports createAnalyticsV1Session. Construct it explicitly from the future approved handoff integration. Construction does not call a service. The caller supplies a one-time exchange code and independently expected dataset ID to exchange(code, expectedDatasetId); there is no public issuance UI or route. readSummary(datasetId) uses only the current grant. Neither method retries automatically. A lost/timed-out exchange requires a new trusted handoff, not replaying the same code. Summary retry is explicit and does not re-exchange.

Transport is fixed to http://127.0.0.1:8012 and only POST /analytics/v1/grants/exchange and GET /analytics/v1/datasets/{dataset_id}/summary are implemented. Attempts to use backend port 8011, another origin or a path-bearing base URL are rejected before credentials can be transmitted. Dot-only path IDs are rejected locally to prevent URL normalization changing the route. Browser Origin is left to the browser; it is never forged or rewritten. Cookies are omitted, redirects rejected, browser caching disabled, and bearer/code values are not logged. Bearers go only in the intermediary Authorization header. There is no backend credential forwarding, generic proxy, local/session storage, query token or persistence code.

The token is private in-memory session state. Public snapshots/subscriptions contain only status, selection, typed summary and sanitized error metadata, and are defensively cloned. Snapshot reads recheck expiry; subscribers must render that state rather than retain an independent protected-data cache. Replacement, invalidation and dispose clear the token, selection and displayed summary, invalidate request generations and abort pending requests. A failing subscriber cannot block state clearing. dispose removes lifecycle listeners and timers.

Grant validation requires analytics.v1, Bearer, a header-safe nonempty token, exact UTC second timestamps, a ten-minute non-sliding lifetime and valid scope intersection. results:read requires summary:read, summary-only grants have no result IDs, and both scopes with an empty result set are accepted. This does not enable any result operation. Invalid scopes/schema/dataset identity reject the grant without silently expanding permissions.

Expiry uses lifetime minus the full exchange dispatch-to-validated-body duration. A monotonic deadline is additionally capped by a wall-clock deadline; neither can extend the grant. Clock rollback/nonfinite clock readings invalidate. Expiry is checked before requests, before responses are accepted (including failures), and when snapshots are read. Timers clear state at the deadline but are not the only enforcement. Page hiding, pagehide, freeze and persisted pageshow/BFCache restoration invalidate the generation and require a new handoff. Visibility restoration does not resurrect a grant. Server checks remain authoritative; revocation/restart are observed through invalid-grant/401 responses, not an invented push mechanism. Late responses from prior grants or superseded reads never overwrite or clear a newer selection.

Summary decoding requires analytics.v1 and error_code:string|null in both ready and nonready shapes. not_requested remains upload-only. Current feature flags and nullable lifecycle semantics are preserved. Pending the complete numeric inventory, counts/byte sizes outside JavaScript safe-integer range are rejected rather than presented with lost precision. This is a fail-closed frontend limitation, not a change to backend acceptance domains or an A2 encoding implementation.

The current pages are intentionally not wired to the session: no new code-entry page, login flow or real-backend selection UI was introduced. The session owns its protected summary/selection state for future binding. Track B result lists, immutable-result transport, verified-result UI, typed configuration encodings and provenance verification remain unavailable. Approval of Track A does not approve a Track B provider, inferred legacy provenance, storage/publication changes or any Option B policy change.

Mock-only Track A tests cover expiry, scope/schema rejection, private credential handling, exchange failure/no replay, summary errors/retry, lifecycle clearing, defensive snapshots and stale success/failure after grant replacement. Browser event tests are jsdom simulations; real browser lifecycle/keyboard/visual/CORS behavior and deployed security remain unverified.
### Application-level Track A summary handoff

In `VITE_DATA_MODE=backend`, the shared shell now exposes a manual trusted-handoff panel. Supply an exchange code **and its expected dataset ID** from the approved external handoff. There is no issuance/login endpoint or automatic URL parsing. Submitting clears the code field, exchanges once, and reads the authorized summary. Summary refresh is manual; an ambiguous exchange failure requires a fresh trusted handoff, not automatic exchange replay. Protected summary/selection state is owned by the memory-only Track A session and clears on expiry, invalidation, suspension or BFCache restoration. No bearer enters React public state, browser storage or URLs. Mock mode leaves the legacy fixture pages unchanged and does not mount this panel.

`VITE_ANALYTICS_BASE_URL=http://127.0.0.1:8012` configures only the intermediary. The approved client rejects other origins/paths. `VITE_API_BASE_URL` still independently controls legacy traffic (default port 8011). No proxy or implicit redirection is introduced. Use the existing `npm run dev` command at the exact approved browser origin `http://127.0.0.1:5173`; restart Vite after environment changes. No backend service is started by this frontend.

A real end-to-end run remains blocked on deployed/approved Track A routes, exact-origin CORS/security configuration, an externally issued single-use code and matching dataset ID, and a disposable environment explicitly approved for live calls. Verified results/Track B and legacy analytics integration remain blocked. No fixture fallback, polling, analysis jobs or authentication issuance has been added. Browser visual/responsive/real keyboard QA has not been performed.
