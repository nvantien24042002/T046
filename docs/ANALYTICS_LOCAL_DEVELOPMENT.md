# Track A local-development package — conditional on a trusted issuer

This runbook is not evidence of a running service. No live calls, dataset access,
provisioning or service startup were performed to prepare it. Track B remains disabled.

## What is runnable and what is missing

`backend.analytics_dev` is a launch wrapper around the existing normal
`create_analytics_app(issuer=...)` factory. It does not implement an issuer or login.
The repository currently has only the `TrustedIssuer` protocol and a fake test issuer.
Therefore an actual authorized exchange cannot currently be performed from this
package alone. Do not substitute `tests.analytics_support.FakeIssuer` for real authority.

The normal application exposes exactly:

- POST `/analytics/v1/grants/exchange`
- GET `/analytics/v1/datasets/{dataset_id}/summary`

There is no health/docs/issuance/revocation HTTP endpoint. The explicitly named
`create_synthetic_analytics_app` is for injected test orchestration only. It must
not be deployed and is never imported by the launch wrapper. Verified result routes
remain absent from the normal application, even when a grant has results scope.

## Required private issuer integration

The owner/security owner must supply and review a zero-argument callable in an
approved importable private module. It returns an object implementing:

```python
consume(code: str) -> IssuerRecord | None
```

Consumption must be atomic, globally single-use across intermediary restarts, and
must never reissue a code value. The factory/import itself is trusted executable code;
the wrapper's interface check does not certify its security or persistence behavior.
Issuance/authentication and secure operator handoff occur outside this HTTP application.
No public issuance endpoint is implied.

For the first summary-only check, the authorized issuer record must contain:

| Field | Required value |
| --- | --- |
| `issued_at` | Actual server UTC Unix second at issuance; not in the future |
| `audience` | `http://127.0.0.1:8012` |
| `origin` | `http://127.0.0.1:5173` |
| `dataset_id` | Exact already-authorized synthetic dataset ID |
| `scopes` | `frozenset({'summary:read'})` |
| `result_ids` | `frozenset()` |
| `upstream_capability` | Existing valid upload capability for that dataset; server-side only |

The issuer record is not a browser configuration file. Codes must be unpredictable,
URL-safe and 1–512 characters. Code lifetime is 60 seconds; obtain the code only after
the client and service are ready. Grant lifetime is 600 seconds, absolute, zero grace.
Lost exchange response requires a new authorized handoff, not replay or dataset creation.

## Configuration template

No new intermediary environment variables are read by the factory or wrapper.
The following is an operator checklist, **not a loadable environment file**:

```text
working_directory = D:\CVAT_Dashboard_Scene_Finder
python = .venv\Scripts\python.exe
issuer_factory = <owner-approved-importable-module>:<zero-argument-callable>
listen = http://127.0.0.1:8012                 # fixed
browser_origin = http://127.0.0.1:5173         # fixed
upstream = http://127.0.0.1:8011               # fixed
workers = 1; reload = false; proxy_headers = false; access_log = false
grant_capacity = 10000                       # existing default, not a capacity claim
```

Any issuer-specific secrets/settings belong to its separately approved private
configuration. Do not put them in arguments, VITE variables, source files, shell
history, URLs or this template. If a private module needs an additional import path,
have the owner configure that trusted path locally; no path was installed here.

The intermediary does not need `CVAT_UPLOAD_SECRET`, owner keys, or a storage root.
The existing backend retains its own private `CVAT_UPLOAD_DIR`, `CVAT_UPLOAD_SECRET`,
`CVAT_UPLOAD_OWNER_KEYS_JSON` and other settings. Do not copy those into the browser
or change them for this test.

Backend default Origins omit 5173. Because the intermediary forwards the validated
Origin unchanged, its existing configuration must explicitly permit:

```text
CVAT_ALLOWED_ORIGINS=http://127.0.0.1:5173
```

This value is a required entry, not an instruction to overwrite other approved entries.
A backend configuration change/restart requires separate authorization; none was done.
No origin stripping or proxy bypass is supported. Host must be `127.0.0.1:8012`,
Origin exactly 5173 above, and the peer loopback. `localhost` is not interchangeable.
CORS permits GET/POST/preflight and Authorization/Content-Type without cookies.

## Safe startup checks — future execution only

1. Confirm the issuer module and its private configuration are approved. Confirm the
   capability/dataset binding through authorized metadata, not a new upload or guess.
2. Confirm the authorized synthetic backend is already available at fixed port 8011,
   with the correct Origin configuration and no production/protected dataset target.
3. Inspect listeners without terminating or replacing anything:

```powershell
Get-NetTCPConnection -State Listen -LocalPort 8011,8012,5173 -ErrorAction SilentlyContinue |
  Select-Object LocalAddress,LocalPort,OwningProcess
```

If 8012 is occupied, stop and identify the owner; do not kill it or choose another
port. Confirm no wildcard service binding is being introduced. Recheck server UTC
clock accuracy. Check installed dependencies without installing/upgrading anything.

4. Launch only after approval, from the repository root:

```powershell
Set-Location -LiteralPath 'D:\CVAT_Dashboard_Scene_Finder'
.venv\Scripts\python.exe -B -m backend.analytics_dev --issuer-factory APPROVED_MODULE:APPROVED_CALLABLE
```

Replace the capitalized placeholder with the reviewed private factory. It does not
exist in this repository. The wrapper fixes loopback binding, one worker, no reload,
no proxy-header trust and no access log. It does not stop/restart another service.
Do not launch the intermediary's raw factory with Uvicorn `--factory`: it requires
the explicit issuer keyword argument and has no environment-based issuer discovery.

There is no intermediary GET `/health`. Use an authorized CORS preflight and then
exchange/summary checks instead. A GET health request would not validate this app.

## Disposable read-only integration procedure — not executed

Prerequisites: approved already-provisioned synthetic dataset and existing capability;
trusted expected archive SHA-256/dataset ID supplied independently; approved issuer and
short-lived code; explicit permission to run this procedure. No provisioning/analysis
is included. Existing ready summary reads may insert/remove transient reader leases.

1. Record the authorized dataset/archive inventory through the test environment's
   approved mechanism. Do not read the legacy root or another registry for convenience.
2. Launch the approved normal intermediary as above. Keep any frontend pages in their
   current gated mode; current FE code has no approved live grant transport.
3. Use an operator-controlled local Python client with `httpx.Client(trust_env=False,
   follow_redirects=False, timeout=10)`. Set Origin to the exact 5173 value. Never log
   request/response bodies, authorization headers, capabilities or exchange codes.
4. OPTIONS the summary URL with Access-Control-Request-Method GET and
   Access-Control-Request-Headers Authorization. Require 200 and exact allow-origin;
   no credentials permission. This preflight does not read the dataset.
5. Securely receive the code from the authorized issuer (for example an approved
   password-style prompt in a private operator process). POST JSON `{"exchange_code":
   <in-memory-code>}` to port 8012 `/analytics/v1/grants/exchange`.
6. Require 200 and validate `GrantResponse`. Compare its dataset/scopes/results against
   the independently authorized selection. Retain token only in memory; do not print it.
7. GET the matching summary with `Authorization: Bearer <in-memory-token>`. Require
   200, `analytics.v1`, matching dataset/archive, ready=true and the existing count/feature
   schema. Use `Summary` validation; do not merely check HTTP status.
8. Authorized negatives: missing bearer =>401; wrong Origin =>403; another dataset
   outside scope =>403 before lookup; replay of the consumed code =>401. Repeat the
   same summary read without exchange to confirm reuse within the absolute grant life.
   After expiry =>401. Server-side revocation requires a separately authorized trusted
   control operation; no HTTP revocation endpoint exists. No restart is required here.
9. GET result-list/detail on the normal app must remain 404. No cached-result/analysis
   verification is possible through deployed Track A; do not switch to the synthetic
   factory to claim success. No Track B provider exists.
10. Recheck the approved dataset/archive inventory; require no data/result changes and
    no remaining reader lease. Discard local token/code; let grants expire. Do not call
    upload/cancel/delete endpoints as cleanup. Report only safe status/code/schema
    outcomes, not raw bodies. Stop on a mismatch rather than repairing dataset state.

## Present readiness

Unavailable: real issuer implementation/wiring, issued authorized code, and an explicit
live execution authorization. Unverified: synthetic dataset/capability availability,
backend Origin configuration, listener state and clock readiness. These were not read.
Verified-result serving is intentionally unavailable. Current FE remains gated.

The wrapper/help/interface tests can run without those prerequisites. A real HTTP
exchange-to-summary run is **not currently possible from the supplied package alone**.
No live end-to-end success is claimed; Track B and full analytics remain blocked.

## Actual package validation — 2026-10-17

Commands executed (no service launch):

```powershell
.venv\Scripts\python.exe -B -m backend.analytics_dev --help
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_analytics_dev.py -q --tb=short
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_analytics_contracts.py tests/test_analytics_grants.py tests/test_analytics_upstream.py tests/test_analytics_intermediary.py tests/test_analytics_dev.py -q --tb=short
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain tests/test_stage5_preservation.py -q --tb=short
```

Results respectively: help exit 0; 10 passed/exit 0 (0.42s); 119 passed, 1 warning/
exit 0 (0.83s); historical 1 failed/exit 1 (0.51s), assertion names manager.py.
The combined suite includes the 10 wrapper tests; do not add them twice. It used
approved elevated isolated execution, not live IO. The warning is Starlette's
HTTPX TestClient deprecation. No dependencies were installed.

Wrapper tests inject a module/issuer and mock `uvicorn.run`; they verify fixed
binding/options, normal-only routes, interface rejection, required argument and
sanitized initialization failures. They do not certify a production issuer or
socket-level startup. No listener query, private configuration load or live request
was performed. Protected source/manifest/assertion and historical artifact hashes
remain unchanged. Existing tracked/staged Git diffs are empty; no commit/push.
