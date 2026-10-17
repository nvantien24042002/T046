# Stage 1 baseline and Stage 2 upload prototype checkpoint

Date: 2026-10-16, Asia/Saigon. Durable storage implementation has **not** started.

## Scope and runtime

The existing `app.py`, `core/*.py`, `ui_theme.py` and `requirements.txt` are unchanged, checked against recorded SHA-256 hashes. The experiment runs as `prototype_app.py` on a separate Streamlit port and `prototype/api.py` on a separate FastAPI port. The existing seven-tab dashboard continues to run independently. No React, production upload path, dataset registration, SQLite schema, migration, public service, commit or push was introduced.

Environment used: Windows 11 build 26200, Python 3.12.14, Streamlit 1.66.0, FastAPI 0.143.2, Starlette 1.8.0, Uvicorn 0.54.0, pandas 3.0.6, NumPy 2.5.4, OpenCV headless 5.0.0.93, Pillow 12.3.0, Plotly 7.1.0, pytest 9.1.1, Playwright 1.64.0, installed Chrome in headless mode. See `tests/baseline/runtime.json` and the complete `docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`.

The default Python 3.14 had none of the application/backend packages. An isolated `.venv` was created from the bundled Python 3.12 runtime. Bundled ensurepip was unavailable, so Python 3.14's pip installed into the 3.12 environment with `--python`. Downloading required network-enabled tool execution. Python-created private temporary directories denied access under the restricted Windows token; tests were run with approved elevated tool execution, without changing ACLs. This is a test-host limitation, not a product permission workaround.

This environment does not establish the user's deployed versions. The prototype intentionally targets a pinned modern Streamlit CCv2 API. It is **not** claimed to work on the repository's minimum Streamlit 1.35. No existing environment was upgraded or global packages replaced.

## Baseline fixtures and observations

`tests/fixtures.py` generates two deterministic 64x64 PNGs, small CVAT XML with duplicate/invalid/out-of-bounds boxes, a missing referenced image, and unknown/missing metadata. `tests/baseline/capture.py` executes only AST-selected original imports/constants/functions/classes, with a no-op progress adapter; it does not import/execute the application page. This harness is strictly for tests, not a backend dependency.

`tests/baseline/expected.json` records all scalar quality and label metrics, statuses, issues, priorities, CVAT image/box records, distribution/attributes, coverage, resolver cases and report columns. Source hashes prevent silently recapturing a changed algorithm. The capture command refuses to overwrite an existing expected file.

Observed original outputs include flat image score 50 with BLUR/LOW_CONTRAST/LOW_RESOLUTION; texture image score 85; duplicate box flags; invalid geometry; missing-image A_ONLY status; weather unknown versus timeofday missing; car slice support 1. These are observations from the original code, not new algorithm choices. Values are serialized to ten decimal places in the pinned runtime. Future platform/version changes require explained numerical tolerance decisions, not blind snapshot regeneration.

## Prototype behavior and boundaries

- Inline Streamlit **CCv2** component, plain JavaScript, no npm/bundler or React. This replaces the plan's tentative component bridge/build approach for the spike: the installed version supplies a maintained bridge directly. No packaged component scaffold is needed.
- Browser slices a local File into **64 KiB** blobs, transfers sequentially by XHR to FastAPI and displays current transfer plus acknowledged received bytes. Only a small nonsecret result object crosses into Streamlit; ZIP/image bytes and session tokens do not.
- API hard limit **16 MiB**, one active upload and eight total sessions per process. Client checks are duplicated on server. JSON initialization is capped at 4 KiB; actual chunk bytes are bounded irrespective of headers. Wrong offsets and incomplete completion are refused. Incomplete/oversized request writes are truncated to the previous in-process offset.
- Files use generated UUID `.part` names in an operator-selected private directory. No extraction or image decoding. Completion computes SHA-256 using bounded 64 KiB reads. No unbounded ZIP read or whole-ZIP buffer in the prototype.
- Result is always `ready=false`, `durable=false`, state `received_unvalidated`. A ZIP extension is not validation. No dataset ID, summary, analysis or public download route exists.
- Explicit random bootstrap key required; each session gets a random bearer capability. API restricts loopback peer/host and exact local origins. Run with proxy headers disabled, one worker, no reverse proxy/public binding. Credentials are passed in memory/config, never hard-coded or returned in component results/logs. Public deployment remains prohibited at this checkpoint.
- Stable component identity retains a live upload across a Streamlit rerun. Network failure becomes an actionable failed result, never ready. There is deliberately **no automatic retry, idempotent replay, durable acknowledgement, refresh resume, cancellation, capacity reservation, cleanup service or restart recovery** yet.
- Session metadata is memory-only; restarting makes retained `.part` files unreachable through this API. Files are not silently deleted. Operators can inspect/remove only their own prototype scratch after stopping the process. Do not use real/user datasets for this spike.

## Exact setup and commands

Run from `D:\CVAT_Dashboard_Scene_Finder` in PowerShell. For a normal installed Python 3.12 with pip:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python.exe -m pip install -r docs\PROTOTYPE_REQUIREMENTS_LOCK.txt
```

The actual available bundled runtime required this equivalent setup (do not recreate an existing environment unnecessarily):

```powershell
& 'C:\Users\Tien Nguyen\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' -m venv --without-pip .venv
python -m pip --python .venv\Scripts\python.exe install -r docs\PROTOTYPE_REQUIREMENTS_LOCK.txt
python -m pip --python .venv\Scripts\python.exe check
```

All exact transitive versions are frozen in the lock file. `requirements-prototype.txt` is the shorter direct-dependency entry point for this isolated environment; original production requirements are preserved.

Start the prototype locally in one PowerShell session; the child API inherits generated credentials, which are not printed or written to source. Set storage to a **new private scratch directory**, not static/public or a user dataset folder:

```powershell
$env:CVAT_PROTOTYPE_KEY = & .venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
$env:CVAT_PROTOTYPE_DIR = Join-Path $env:LOCALAPPDATA ('CVAT-Upload-Prototype\' + [guid]::NewGuid().ToString())
$prototypePython = Join-Path (Get-Location) '.venv\Scripts\python.exe'
$prototypeApi = Start-Process -FilePath $prototypePython -ArgumentList '-m','uvicorn','prototype.api:create_from_env','--factory','--host','127.0.0.1','--port','8010','--no-proxy-headers' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -PassThru
try {
    & $prototypePython -m streamlit run prototype_app.py --server.address 127.0.0.1 --server.port 8502 --server.headless true --browser.gatherUsageStats false
} finally {
    if (-not $prototypeApi.HasExited) { Stop-Process -Id $prototypeApi.Id }
    Remove-Item Env:CVAT_PROTOTYPE_KEY
}
```

Open `http://127.0.0.1:8502`. Ports 8010 and 8502 must be unused. This example stops only the API process it creates; it leaves its scratch files intact. Original app command remains `.venv\Scripts\python.exe -m streamlit run app.py`.

Tests:

```powershell
.venv\Scripts\python.exe -m pytest tests/test_baseline.py tests/test_prototype_api.py -q
$env:CVAT_RUN_BROWSER_TESTS = '1'
.venv\Scripts\python.exe -m pytest tests/e2e/test_browser.py -q
```

Browser tests require installed Chrome, free ports 8010/8501/8502 and permission to start headless Chrome/local child servers. They start/stop only their own processes and use synthetic private temporary files. No browser binary download is needed on this machine. With the opt-in unset, browser tests explicitly skip rather than pretending to pass. To capture original baselines only before a refactor, use `.venv\Scripts\python.exe -m tests.baseline.capture`; do not recapture to hide failures.

## Actual test results

- Baseline capture succeeded against the original source. Initial handwritten score assertion incorrectly expected 35; observed original score is 50. Only the test assertion was corrected; algorithms and captured golden output were untouched.
- Baseline/API suite: **13 passed**, one upstream Starlette warning that httpx-based TestClient is deprecated. `pip check`: **No broken requirements found**.
- Initial browser run: one passed, two failed assertions. The transport itself completed with matching SHA and survived rerun, but Chrome omitted automatic Content-Length from Playwright's simplified headers; the check now uses authoritative server byte acknowledgements. The legacy CSV download triggered a normal rerun with temporarily duplicated stale/new tabs; the test now waits for seven tabs again. No application fix was needed.
- Final browser/all-suite outcomes are recorded in `docs/UPLOAD_PROTOTYPE_TEST_RESULTS.md` after the final verification run.

The transport fixture is approximately 538 KB and uses nine chunks; exact sizes/hashes/browser version are recorded by the E2E test. The legacy fixture is approximately 14 KB. These are **not** ZIP64/large-file/resource benchmarks. No peak-memory capacity gate, 20–50 GB transfer, production proxy, durable resume or crash recovery has been verified.

## Files added and why

- `prototype/api.py`, `prototype/__init__.py`: isolated bounded local FastAPI experiment.
- `prototype_app.py`, `ui/__init__.py`, `ui/upload_component/__init__.py`: separate Streamlit page and inline browser component, leaving existing UI untouched.
- `tests/fixtures.py`, `tests/test_baseline.py`, `tests/baseline/{capture.py,expected.json,source_hashes.json,runtime.json}`, test package initializers: deterministic pre-refactor baseline and provenance.
- `tests/test_prototype_api.py`, `tests/e2e/test_browser.py`: protocol/access/resource checks and real Chrome transport/rerun/failure/dashboard tests.
- `requirements-prototype.txt`, `docs/PROTOTYPE_REQUIREMENTS_LOCK.txt`, `.gitignore`: isolated reproducible dependencies and exclusion of environments/secrets/scratch.
- This checkpoint, `docs/BACKEND_PLAN.md`, `docs/LARGE_UPLOAD_DESIGN.md`, test results: scope, next contract, exact evidence and limitations.

## Next implementation plan and checkpoint

Stop here before Stage 3. Retain the proven CCv2 File.slice/XHR interaction, but replace disposable prototype routes with production `/api/v1/uploads` and SQLite sessions/receipts/reservations. Implement fsync-before-transaction acknowledgement, atomic scratch/append recovery, idempotent replay, auth/owner binding, resume after reselection with prefix receipt verification, cancel/expiry/cleanup, then deterministic process-crash and disk-failure tests. Do not relabel prototype acknowledgements as durable.

Subsequent stages remain bounded ZIP64/CRC/safe XML validation and registration, path-backed QA/QC refactor/parity, integrated existing dashboard, then measured scale evidence. Before large-file work, confirm persistent volume path/capacity/ACLs, real dataset entry/XML/image/pixel statistics and intended access boundary. Provisional 50 GiB/expanded-size/metadata/memory figures remain planning values.
