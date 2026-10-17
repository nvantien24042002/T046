# N2-05B — Audit trước merge main

Ngày: 2026-10-18, Asia/Saigon. **Kết luận: NO-GO MERGE; không commit/push/deploy.**

## 1. Workspace thật và Git topology

Đã chạy lại `(Get-Location).Path`, `git rev-parse --show-toplevel` và
`git status --short --branch` trực tiếp tại từng thư mục sau yêu cầu xác nhận workspace:

| Mục | BE | FE |
|---|---|---|
| Working directory tuyệt đối | D:\CVAT_Dashboard_Scene_Finder | D:\CVAT_Dashboard_Scene_Finder_FE |
| Git root | D:/CVAT_Dashboard_Scene_Finder | D:/CVAT_Dashboard_Scene_Finder_FE |
| Branch | backend-dev | frontend-dev |
| HEAD | 55fae0b2eb096dba5f2e55f2f55995292a76cae6 | Cùng commit |
| Commit message | chore: establish backend baseline | Cùng commit |
| Tracked modified | backend/integrated_api.py, 78 insertions/24 deletions | backend/integrated_api.py, 7 insertions |
| Staged | Không có | Không có |
| Untracked trước audit report này | 42 files | 89 files, gộp trong frontend/ khi status mặc định |

**Hai Git root đúng trên D nhưng là linked worktrees của cùng repository**, common Git
directory D:/CVAT_Dashboard_Scene_Finder/.git. Không phải hai lịch sử Git độc lập.
`git worktree list --porcelain` xác nhận hai branch tương ứng tại hai đường dẫn.

- Chỉ có một reachable commit. Hai branch có ahead/behind 0/0 khi so với nhau.
- Không có remote được cấu hình; không có local main hoặc origin/main.
- Không có upstream; không thể tính commits chưa push theo upstream hoặc xác minh remote
  history. Không suy ra commit chưa từng được chia sẻ chỉ từ việc hiện không có remote.
- Không fetch, tạo branch, stage, commit hoặc merge. Các thay đổi product chủ yếu chưa
  commit nên không tự xuất hiện trong một merge chỉ dựa vào hai branch HEAD hiện tại.
- Nếu muốn hai main độc lập, owner cần quyết định tách repository bằng một kế hoạch riêng;
  không init/copy/move repo tự động. Nếu giữ linked worktrees, chỉ có một refs/heads/main
  chung và cần kế hoạch tích hợp một lịch sử, không hai lần merge độc lập.

## 2. Diff quan trọng cần giữ

BE integrated_api.py hiện có shared summary/capability helper, dashboard projection,
effective_config trong analysis response, preview archive-path/media mapping và local
Origins. Các analytics modules, issuer, tests/docs vẫn untracked. Không ghi đè chúng.

FE workspace chứa một hunk BE riêng: local Origins và bearer alias trong cap(). Đã đọc
diff tại chính FE root; không chép BE source qua lại. Hunk bearer là thay đổi auth surface
cần review, không xem là BFF/delegation hoặc tự động mang sang main.

Manager/runner cancellation hardening, Streamlit source và toàn bộ preservation assertions
giữ nguyên. Không sửa hash, thêm skip/xfail hoặc cập nhật baseline để đổi kết quả kiểm thử.

## 3. Secret/data audit

- Quét một reachable commit và tracked/untracked files bằng bounded patterns cho private-key
  blocks, một số token formats và named credential literals: không có candidate khớp.
  Chỉ báo path/category nếu có, không xuất giá trị. Không đọc private runtime credentials.
- Không thấy tracked/untracked filename candidate thuộc ZIP, SQLite/DB, private-key hoặc
  runtime log trong phạm vi git ls-files/--others --exclude-standard.
- .env.upload.example: secret field rỗng, owner map là empty JSON; đây là template,
  không phải credential thực. FE .env.example chỉ có public mode/URLs đã kiểm tra.
- Root .gitignore bảo vệ .env/.env.upload/.diagnostics/runtime/build outputs; FE còn ignore
  .env.local/.cache. Không coi ignore là security control đủ cho mọi tên file cấu hình tương lai.
- Không có bằng chứng credential đã push công khai; remote exposure/history ngoài reachable
  commit **NOT VERIFIED**. Không tuyên bố toàn bộ lịch sử hoặc mọi loại secret đều sạch.

**Zero-secret vẫn FAIL:** bundle/source map vừa build chứa mã credential flow gồm
cvat-upload-owner-key, X-Owner-Key, Upload-Capability, analytics exchange route và
sessionStorage. Không thấy secret literal theo patterns quét, nhưng code vẫn nhận/gửi
credential runtime. Không chỉ ẩn form rồi gọi là an toàn.

Source xác nhận DatasetUploader lưu Owner Key trong sessionStorage, upload.ts nhận capability,
dashboard/preview/analytics transport giữ bearer/capability. Vite không có same-origin BFF
proxy hiện hành; chưa tìm thấy BFF/anonymous-session/per-session BE authorization implementation.

Không đưa giá trị credential BE vào FE, bundle, VITE config, log hoặc báo cáo trong audit này.
Tests dùng fixture/temp data; không đọc/copy real private dataset cache để dựng PASS.
Browser storage/request-response của người dùng hiện hữu không được đọc; chưa chứng minh
không có credential trong những profiles/logs đó.

## 4. Test/build thực tế

Source/test execution đều ở hai working directories trên D. Artifact trên C của lượt audit
trước khi owner yêu cầu xác nhận workspace chỉ là log/JUnit/build output, không phải bản sao
repository, không phải source workspace. Báo cáo mới này được ghi trực tiếp tại BE repo D.

### 4.1 BE regression cô lập

Lệnh thực tế từ BE root:

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests --ignore=tests/e2e --ignore=tests/test_durable_network.py --deselect=tests/test_analytics_cache_adapter.py::test_staging_real_cache_compatibility_with_explicit_map --deselect=tests/test_analytics_cache_adapter.py::test_staging_tamper_rejection_in_temporary_storage --junitxml='C:\Users\Tien Nguyen\.codex\visualizations\2026\10\16\01a146cf-9294-72e1-8365-ec8871f1ac7e\n2-05b-merge-audit-20261018\be-regression.xml' -q --tb=short
```

**Exit 1: 890 passed, 2 failed, 0 skipped, 2 deselected, 2 warnings; 155.65s.**
JUnit: 892 executed test cases, zero errors. Không phải full-suite run.

| FAIL giữ nguyên | Evidence |
|---|---|
| tests/test_baseline.py::test_original_sources_unchanged | AssertionError app.py |
| tests/test_stage5_preservation.py::test_stage4_sources_preserved_during_stage5 | AssertionError datasets/manager.py |

Independent manifest comparison: original baseline 8 files chỉ app.py lệch; Stage 5 11 files
có manager.py và runner.py lệch. Stage 5 assertion dừng tại manager, không phải runner đã match.
Option B cho manager/runner không tự áp dụng cho app.py và không biến historical test thành PASS.

JUnit xác nhận executed, zero failure cho:

- test_pipeline_parity_with_baseline;
- test_captured_quality_label_distribution_coverage_and_reports;
- test_preview_and_annotated_preview.

Warnings: Starlette/httpx deprecation và duplicate ZIP-member fixture. Không sửa nghiệp vụ
hoặc test để xử lý các cảnh báo này.

Hai deselected tests đọc/copy actual stage3-data và publish vào temp ResultStore. Không chạy
chúng để giữ audit cô lập, không phải để che checksum FAIL. tests/e2e opt-in/fixed-port browser
group không được collect; không gọi đó là skipped/PASS. Hai preservation tests vẫn chạy.

### 4.2 Durable network test riêng

Sau broad regression, host metadata cho thấy 8011 không có listener, còn 8012/5173 có listener.
Đã review DurableServer: temp root, refuse occupied 8011, chỉ kill/restart child do test tạo.

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_durable_network.py --junitxml='C:\Users\Tien Nguyen\.codex\visualizations\2026\10\16\01a146cf-9294-72e1-8365-ec8871f1ac7e\n2-05b-merge-audit-20261018\be-network.xml' -q --tb=short
```

Lượt tool bị user interrupt. Kiểm tra tiếp theo thấy JUnit hoàn chỉnh: **1 test, 0 failure,
0 error, 0 skipped, 7.585s**; không còn process khớp network test/test API. **Exit code trực
tiếp NOT RECORDED** vì interruption, không suy thành exit 0 chỉ từ XML. Không chạy lại để
đổi bằng chứng. Đây không phải khôi phục service BE production/local demo.

### 4.3 FE

Working directory: D:\CVAT_Dashboard_Scene_Finder_FE\frontend.

| Lệnh | Kết quả |
|---|---|
| npm.cmd test -- --reporter=dot | Exit 0; 23 files, 317 passed, 0 failed/skipped; 80.17s |
| npm.cmd run typecheck | Exit 0 |
| npm.cmd run lint | Exit 0 |
| npm.cmd run build -- --outDir C:\Users\Tien Nguyen\.codex\visualizations\2026\10\16\01a146cf-9294-72e1-8365-ec8871f1ac7e\n2-05b-merge-audit-20261018\fe-build --sourcemap | Exit 0; Vite build 15.79s |

Trong shell thực tế outDir được truyền như một path argument giữ nguyên spaces. Không build
đè FE dist có sẵn; không cài dependencies. Warnings gồm bundle >500 kB và outDir ngoài root;
không dùng emptyOutDir để xóa folder có sẵn. JS output 792821 bytes, source map 4250177 bytes.
Patterns scan không thấy literal candidate nhưng credential flow markers vẫn có trong cả JS/map.

FE tests hiện tại chứng minh hành vi hiện tại, không chứng minh Anonymous Session + BFF chưa
triển khai. User A/B anonymous isolation, BFF request/response, cookie/CSRF và server CSV
authorization vẫn **BLOCKED/NOT VERIFIED**, không tạo mock để gọi là integration PASS.

## 5. Kiến trúc đã chọn và patch tối thiểu đề xuất

Quyết định mới là **Anonymous Session + BFF + per-session authorization ở BE**, không login.
Thiết kế OIDC/login trong artifact trước không được giữ như prerequisite của phương án mới.
Chưa triển khai patch này trong audit vì đó là thay đổi security/storage/API nhiều thành phần,
không phải một sửa nhỏ để merge; cần review contract và tests trước.

1. BFF tạo opaque anonymous app session riêng cho browser, cookie HttpOnly/Secure/SameSite
   theo approved local TLS policy; session authority/server state không nằm trong JavaScript.
   App-session cookie không chứa Owner Key, upload capability hay analytics grant.
2. BE giữ authoritative session-resource-operation mapping: session A không thể dùng ID của B
   để upload/chunk/complete/validate/analyze/read/preview/export/cancel. Public handle không
   cấp quyền. BFF không được dựa vào owner chung/capability rộng để giả lập isolation.
3. BFF→BE authentication/delegation là internal, operation/resource-bound và được BE xác minh;
   không tin X-Session/X-User do browser tự khai. Anonymous không có nghĩa BE bỏ authentication.
4. FE bỏ Owner Key form/storage, capability/grant/exchange flow; chỉ gọi same-origin BFF.
   Giữ schema/selection-race/fingerprint/result identity và unsupported gating.
5. BFF upload bounded stream + durable idempotency/recovery mapping; global ONE_ACTIVE_UPLOAD,
   cancellation/attempt/reservation semantics không đổi. Phiên browser hết hạn không tự xóa data.
6. Server preview và CSV kiểm quyền riêng, exact dataset/result/config/filter, cột allowlist,
   formula protection; FE không nhận raw payload để tự tính KPI/QA/QC.
7. Chốt session TTL/revoke/restart, private mapping storage, anonymous abuse/quota policy và
   recovery khi mất cookie. Không hứa khôi phục cross-browser khi chưa có approved mechanism;
   không dùng BE key làm recovery password.

Các quyết định còn thiếu: TLS/session-cookie/CSRF, BE-verifiable delegation, mapping persistence,
quota/rate limits, safe session expiry/recovery và public DTO/export policy. Không tạo key/store
hoặc sửa BE auth trong audit này. Giữ service/data hiện hữu; không restart để thử architecture.

## 6. Quyết định merge và bước tiếp theo

| Gate | Trạng thái |
|---|---|
| Đúng source workspace trên D, preserve dirty work | PASS audit; không chỉnh source/FE |
| Hai repository độc lập/main/origin history rõ ràng | BLOCKED: shared Git, main/origin/upstream không có |
| Snapshot thay đổi đã review và commit trên branch | Chưa: nhiều untracked và tracked modified, staging rỗng |
| No matching secret literals/data files trong bounded scan | Không thấy candidate; không phải complete secret-free proof |
| Anonymous BFF/per-session BE authorization | FAIL requirement / NOT IMPLEMENTED |
| BE behavioral/parity validation | 890 PASS trong regression đã nêu; network XML 1 PASS riêng |
| Historical preservation | 2 FAIL còn nguyên |
| FE test/typecheck/lint/build | PASS như trên; bundle chưa compliant zero-secret |
| BFF anonymous A/B Browser E2E, preview/CSV isolation | BLOCKED, chưa thực thi |

**Không merge repo nào, không tạo main, không commit/push/deploy; không có merge SHA mới.**
Không được dùng kết quả FE PASS hoặc parity PASS để bypass P0 hay historical gate.

Bước tiếp theo cần owner quyết định: (a) giữ một repository/shared main hay tách hai Git
repositories bằng kế hoạch được duyệt; cung cấp remote/history nếu thực sự cần origin/main;
(b) duyệt contract/implementation slice anonymous-session BFF và BE authorization;
(c) chốt disposition preservation app.py riêng với Option B manager/runner mà không đổi
manifest/assertion; (d) sau implementation chạy lại tests và actual anonymous A/B browser E2E.

Thay đổi duy nhất của lượt xác nhận workspace này là báo cáo docs/N2_05B_MERGE_AUDIT_20261018.md
được tạo trực tiếp ở BE repo D. Không copy/move repository, không sửa FE/backend source,
không ghi ResultStore hoặc đọc real credential values. Không sửa hash/assertion, reset/stash/
checkout/clean/stage/commit/merge/push. Source đã làm việc trực tiếp trên D xuyên audit;
artifact C trước đó chỉ là bằng chứng/build output, không phải source copy.
