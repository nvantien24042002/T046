# N2-05B — Bước 2: tích hợp same-origin và anonymous session

Ngày nghiệm thu: 2026-10-18. Đây là kết quả kiểm thử local cô lập, không phải phê duyệt merge/deploy/release.

## Trạng thái và bảo toàn

- Làm trực tiếp tại `D:\CVAT_Dashboard_Scene_Finder` và `D:\CVAT_Dashboard_Scene_Finder_FE`.
- Giữ `backend-dev` / `frontend-dev`, cùng HEAD `55fae0b2eb096dba5f2e55f2f55995292a76cae6`; staging không thay đổi. Hai thư mục thực tế là linked worktrees chung Git common directory, không phải hai repository độc lập. Không chuyển đổi cấu trúc Git.
- Snapshot hash trước sửa và đối chiếu sau sửa ở `.diagnostics/step2-20261018/before.json` và `preservation-verification.json`. Không mất file có sẵn. Chỉ các file được liệt kê dưới đây thay đổi; bản `backend/integrated_api.py` có sẵn trong FE giữ nguyên.
- `app.py`, `datasets/manager.py`, `datasets/runner.py`, manifests, assertion và tài liệu Option B không thay đổi trong task này. Hai checksum failure vẫn hiện rõ.
- Không đọc/ghi dataset runtime để chạy integration. Hai test đọc staging thật được deselect có tên rõ ràng. Không thao tác `D:\CVATPrivateUploads\stage3-demo`.
- Không merge, commit, push hoặc deploy; không reset/clean/stash. Các root synthetic, ZIP, logs và bằng chứng của những lần thử được giữ lại.

## Thay đổi theo Git root

BE:

| File | Thay đổi trong task |
|---|---|
| `backend/browser_api.py` (mới) | Session registry trong process; cookie HttpOnly; CSRF/Origin; association và kiểm quyền từng operation; public DTO; CSV tại BE; static React boundary. |
| `backend/integrated_api.py` | Thêm injection `browser_sessions`/`react_dist`; đăng ký browser routes dùng handler hiện có; tắt legacy `/client` khi phục vụ React; đọc optional `CVAT_REACT_DIST`. Giữ các hunk summary/projection/preview có trước task. |
| `tests/test_browser_session_api.py` (mới) | 35 cases: session isolation, từng upload operation, idempotency, pipeline thật với fixture, preview/export, fingerprint, CSRF, expiry/restart, cookie HTTPS, static boundary, CSV và unhealthy guard. |
| Tài liệu này (mới) | Acceptance và giới hạn thực tế. |

FE, các file mới dưới `frontend/`:

- `src/api/browserSession.ts`, `src/api/browserDashboard.ts`: cookie-only cùng origin, decoder, lost-ack retries, chunk hashing có giới hạn bộ nhớ, fingerprint và timeout.
- `src/context/BrowserSessionProvider.tsx`: selection generation/abort guards; không nhận credential nội bộ.
- `src/components/BrowserDatasetPanel.tsx`, `BrowserDatasetUploader.tsx`, `BrowserExportButton.tsx`: catalog riêng session, upload không Owner Key form, explicit cancellation và CSV từ BE.
- `src/tests/browser-session-api.test.ts`, `browser-session-races.test.tsx`: 9 tests transport, sanitization, lost acknowledgement, identity, timeout, selection/clear race và catalog refresh.

FE, các file có sẵn được chỉnh hunk:

- `src/App.tsx`, `src/pages/Dashboard.tsx`, `src/pages/Upload.tsx`: nối product vào session provider/uploader mới.
- `src/components/BackendDashboard.tsx`, `ImagePreview.tsx`: dùng transport được inject và session authorization; CSV qua BE.
- `src/context/VerifiedResultsContext.tsx`, `src/api/dashboard.ts`: giữ tương thích harness/transport legacy; product không mount legacy provider.
- `src/config.ts`: product dùng `readBrowserConfig()` với URL tương đối cố định. `readConfig()` legacy được giữ cho component/test cũ; không điều khiển bootstrap product.
- `src/api/client.ts`: hỗ trợ base rỗng cùng origin, tiếp tục từ chối URL cấu hình không hợp lệ.
- `vite.config.ts`: proxy prefix cố định `/api/browser/v1`, `/health` tới loopback BE; không chèn credential.

Không sửa assertion của test có sẵn. Các source legacy có Owner Key/capability/grant còn được giữ để bảo toàn harness, nhưng không thuộc luồng product và đã bị tree-shake khỏi bundle JS kiểm tra.

## API và luồng đã nối

Namespace public: `/api/browser/v1`.

| Route | Quyền/semantics |
|---|---|
| `POST/GET/DELETE /session` | Tạo/kiểm tra/revoke opaque app session; không cấp Owner Key/capability/grant. |
| `POST /uploads` | Principal riêng session; `Idempotency-Key`, filename và size được store hiện có kiểm tra; DTO dùng field thật `chunk_size_bytes`. |
| `GET /uploads/{id}`, `GET .../receipts`, `PUT .../chunks/{index}`, `POST .../complete`, `DELETE /uploads/{id}` | Kiểm session và ownership trước mỗi operation; dùng store/idempotency/concurrency hiện có. |
| `POST /uploads/{id}/validate?retry=true`, `GET .../dataset`, `POST .../dataset/cancel` | Giữ validate đồng bộ và cancellation semantics hiện tại; retry chỉ explicit. |
| `GET /datasets` | Chỉ catalog thuộc principal của session. Không adopt legacy dataset. |
| `GET /datasets/{id}/summary`, `POST .../analyze`, `GET .../results` | Ownership và lifecycle qua handler/reader hiện có. Analysis response được allowlist schema, không gửi effective configuration/provenance giả. |
| `GET .../configurations` | Trả default thực tế của BE. Không phải immutable config/result registry. |
| `POST .../dashboard` | Render projection BE; yêu cầu `expected_fingerprint`, mismatch 409; FE không tính KPI thay thế. |
| `GET .../preview?image_path=...&annotated=...` | Archive-relative image, kiểm ownership trước reader/preview. |
| `POST .../export` | Projection request + `table_key`, cùng fingerprint/filter; BE tạo CSV, neutralize formula strings; giới hạn 100000 rows/8 MiB, reject thay vì truncate. |

Mọi public resource route kiểm session, Origin và service-health guard. Unknown/cross-session resource trả 404; thiếu session 401; CSRF/credential header cấm 403; các lỗi domain giữ envelope `{"error":{"code":"..."}}`. Global `ONE_ACTIVE_UPLOAD` không đổi.

Cookie `n205b_session`: HttpOnly, SameSite=Strict, path `/api/browser/v1`, Secure khi request HTTPS. Mặc định absolute TTL 1 giờ, không sliding. Registry session chỉ trong một process; restart/expiry mất quyền truy cập, không auto cancel/delete/adopt tài nguyên. Dùng một worker.

Public mutations yêu cầu `X-Requested-With: N2-05B` và Origin khớp session. Production cùng origin; dev qua Vite proxy, không dùng direct cross-origin cookie calls để vượt CORS.

## Lệnh và kết quả thực tế

BE cwd: `D:\CVAT_Dashboard_Scene_Finder`; Python `.venv\Scripts\python.exe`.

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_baseline.py::test_original_sources_unchanged tests/test_stage5_preservation.py -q --tb=short
```

Baseline trước sửa: exit 1, **2 failed**, `app.py` và `datasets/manager.py`.

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_browser_session_api.py tests/test_upload_recovery.py tests/test_validation_cancellation.py tests/test_core_pipeline.py tests/test_dashboard_capability_gating.py tests/test_integrated_api.py -q --tb=short
```

Lần targeted ban đầu: exit 0, **106 passed, 1 warning**. Standalone browser-API sau sửa wire field: exit 0, **34 passed, 1 warning**; health-guard case bổ sung sau đó cũng đã chạy trong regression cuối.

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests --ignore=tests/e2e --ignore=tests/test_durable_network.py --deselect=tests/test_analytics_cache_adapter.py::test_staging_real_cache_compatibility_with_explicit_map --deselect=tests/test_analytics_cache_adapter.py::test_staging_tamper_rejection_in_temporary_storage -q --tb=short
```

Regression cuối trên host: exit 1, **925 passed, 2 failed, 2 deselected, 2 warnings**, 128.36 s. Fail đúng hai preservation assertions nêu trên. Không phải full-suite pass: `tests/e2e`, durable-network file không chạy; hai test staging thật không chạy. Log: `.diagnostics/step2-20261018/be-acceptance-tests.log`.

Sandbox broad attempt trước đó có setup/ACL errors và bị ngắt; không coi là kết quả behavioral. Một standalone attempt trong sandbox có **14 passed, 20 setup errors**, trước test body của các fixture private-root. Host rerun thành công theo kết quả ở trên; không sửa assertion/ACL policy để che lỗi.

FE cwd: `D:\CVAT_Dashboard_Scene_Finder_FE\frontend`:

```powershell
npm.cmd test -- --reporter=dot
npm.cmd test -- --reporter=dot src/tests/browser-session-api.test.ts src/tests/browser-session-races.test.tsx
npm.cmd run typecheck
npm.cmd run lint
npm.cmd run build -- --outDir D:\CVAT_Dashboard_Scene_Finder\.diagnostics\step2-20261018\react-dist-final-catalog --sourcemap
```

- Toàn bộ FE unit suite: exit 0, **326 passed / 25 files**, 29.24 s; log `fe-final-326.log`.
- Sau chỉnh literal type của fixture mới (không đổi runtime/assertion): new suites chạy lại exit 0, **9 passed / 2 files**.
- Typecheck exit 0; lint exit 0; build exit 0. Một lần typecheck/build trước đó đã phát hiện fixture mới bị widen `state`/`ready`; sửa bằng literal types rồi xác minh lại thành công.
- Build còn cảnh báo chunk >500 kB và Zod annotation/outDir; không phải lỗi build. Không cài dependency.

## Browser integration thật — PASS trong môi trường cô lập

```powershell
.venv\Scripts\python.exe -B -u .diagnostics\step2-20261018\isolated_server.py
.venv\Scripts\python.exe -B .diagnostics\step2-20261018\browser_e2e.py
```

Server test bind duy nhất `127.0.0.1:18011`, `proxy_headers=False`, không access log. Script từ chối tái sử dụng root tồn tại, dùng `synthetic-runtime-v5`, factory integrated thật, build `react-dist-final-catalog`, cấu hình/sentinel chỉ dành cho fixture. Startup store/recovery/maintenance/cache chỉ tác động root mới này. Không dùng môi trường private của dataset runtime. Edge headless thực tế, không mock response hoặc KPI.

Kết quả cuối exit 0:

- `/health` 200; upload → chunk → complete → validate → analyze và catalog refresh thành công.
- Tám trang overview/distribution/quality/labels/review/reports/coverage/scene-finder render từ BE. Coverage trả `no_results` khi không có targets: đúng contract, không giả tạo coverage mục tiêu.
- Preview gốc và annotated 200; trình duyệt decode ảnh thật (`naturalWidth > 0`).
- Filter `quality_status=REVIEW` gửi BE; CSV tải từ endpoint BE trả 200.
- Browser context thứ hai: summary, results, preview, dashboard, export, analyze đối với dataset thứ nhất đều 404.
- Không có page errors. Browser storage mới: 0 localStorage, 0 sessionStorage; cookie session không đọc được qua JS. Cookie HTTP loopback có Secure=false; HTTPS cookie attribute được unit test, chưa chạy TLS E2E.
- 25 Network records chỉ method/path/status; không lưu HAR/raw headers/cookie/capability.
- Registry cuối: **1 upload uploaded, 1 receipt, 1 dataset ready, 1 validation attempt published, 0 validation leases**. Đây là một run mới; các run độc lập trước không phải replay trùng session.
- `/client`, unknown `/api/*` và sourcemap asset thật đều 404.

Evidence trong `.diagnostics/step2-20261018/`:

- `browser-evidence-catalog-acceptance.json`: run cuối với Network đã lọc.
- `synthetic-registry-final.json`: số đếm/state authoritative của root thử cuối.
- `overview-render.png`, `annotated-preview-render.png` và ảnh của từng trang.
- `synthetic-export.csv`, `final-static-health.json`, `final-bundle-scan.json`.

Các lần harness trước có timeout ở synchronous Playwright callbacks, selector heading/combobox quá chặt và giả định thiếu trạng thái `no_results`; bằng chứng FAIL/interrupted được giữ. Browser cũng phát hiện lỗi wire `chunk_bytes`/`chunk_size_bytes` trong adapter mới và stale catalog; cả hai được sửa, thêm regression và chạy lại thực tế. Không suy ra đây là root cause của ConnectError upload lịch sử.

## Zero-secret và ranh giới còn lại

- Bundle product không chứa các marker Owner Key/capability/grant exchange/sessionStorage, URL intermediary legacy hoặc sentinel server fixture. Request không có các internal credential headers; JSON response không có sentinel/capability/owner association.
- Sourcemap chẩn đoán còn source legacy/marker tên header, **không có sentinel credential**; BE đã xác minh không phục vụ `.map`. Không publish các source map chẩn đoán ở static server khác.
- Legacy API/Streamlit authentication vẫn nguyên; public browser không được cấp credential của các API đó. Quyền internal được BE suy ra sau kiểm tra association, không chấp nhận identity/credential do browser tự khai.
- Test retry/lost acknowledgement, cancellation/faults, expiry/restart và selection races chủ yếu là isolated unit/subprocess tests. Không tuyên bố đã chạy browser cancellation/worker-kill/restart/timeout fault E2E. Browser timeout có regression fake-clock, không phải live network-fault test.
- Resume chỉ an toàn cho cùng File object/idempotency key trong phiên trang hiện tại; không tự adopt theo owner/filename/size. Chưa có UI khôi phục upload sau reload/reselection/restart hoặc UI explicit validation retry đầy đủ.
- Session hết hạn/restart không nhận lại legacy hoặc upload cũ; session 1 giờ có thể không phù hợp upload rất lâu. Abandoned upload vẫn chịu policy/expiry hiện có và có thể chặn mọi session qua global `ONE_ACTIVE_UPLOAD`; không tự cleanup để vượt policy.
- Chỉ current-cache result và request fingerprint được nối. Chưa có immutable result/config selection được public cho anonymous session; không tạo result ID/provenance giả, không nối ResultStore hoặc triển khai Track B.
- Chưa kiểm thử HTTPS/reverse proxy/multi-worker/deployment. Thiết kế hiện tại local-only, một process; không kết luận sẵn sàng public Internet.

## Khởi động local sau review (không đã triển khai lên môi trường hiện hữu)

Giữ cấu hình server private hiện có: `CVAT_UPLOAD_DIR`, `CVAT_UPLOAD_SECRET`, `CVAT_UPLOAD_OWNER_KEYS_JSON` và limits theo runbook. Không đưa các giá trị vào VITE hoặc browser. Trước khi dùng root có dữ liệu, cần xác nhận quyền và tác động recovery/maintenance/cache; task chỉ kiểm chứng root mới.

`CVAT_REACT_DIST` trỏ **chỉ build output đã duyệt**, không trỏ repository/private config. Startup:

```powershell
.venv\Scripts\python.exe -m uvicorn backend.integrated_api:create_integrated_from_env --factory --host 127.0.0.1 --port 8011 --workers 1 --no-access-log --no-proxy-headers
```

Product React và API cùng origin BE. Dev dùng `npm.cmd run dev` tại FE với proxy prefix cố định về BE loopback; không cần process BFF/intermediary. Không tự khởi động lại BE runtime trong task này.

Kết luận: thông luồng local cô lập có bằng chứng PASS; chưa đạt release gate vì hai preservation failures và các giới hạn nêu trên. Owner vẫn cần quyết định riêng cho preservation `app.py`; Option B hiện có không tự mở rộng sang file này.

Sau kiểm thử, đã dừng đúng process của script test trên `18011` sau khi xác minh command line. Không xóa root synthetic. Kiểm tra host cuối: `8011` và `18011` không listen; dịch vụ có sẵn tại `127.0.0.1:5173` (PID 11316) và `127.0.0.1:8012` (PID 14992) vẫn giữ nguyên. Vì vậy health 200 nêu trên là evidence trong run cô lập, không phải khẳng định BE runtime đang chạy sau bàn giao.
