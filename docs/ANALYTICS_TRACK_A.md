# N2-05B Track A implementation record

Date: 2026-10-17 (Asia/Saigon). Only Track A was authorized. This is neither
Track B approval, a full-suite result, a deployment authorization, nor release readiness.

## Additive composition

- `backend/analytics_contracts.py`: versioned summaries, grant/exchange/error schemas,
  tagged configuration numbers, injected immutable revision specifications and limits.
- `backend/analytics_grants.py`: injected trusted issuer protocol, immutable authority
  records, hashed in-memory grants, one-time exchange, frozen scopes/results, expiry
  and trusted in-process revocation. Grant capacity defaults to 10,000 records and is
  injectable; no persistent grant store or public administration endpoint exists.
- `backend/analytics_upstream.py`: exactly one upstream GET (summary), fixed to
  `http://127.0.0.1:8011`; no environment proxies, no redirects, bounded body and
  explicit per-IO timeouts (10 seconds, 3 seconds connect). Owned clients are closed.
- `backend/analytics_intermediary.py`: normal `create_analytics_app` factory requires
  issuer injection, with optional clock-bearing grant store and summary upstream.
  It exposes exchange and summary only. It is not wired into the existing application.
- `backend/analytics_result_contracts.py`: synthetic-envelope/row validation only.
  No hashing, provenance verification, canonicalization, storage or publication.

Normal construction has **no result-list/detail routes**. The separate, explicitly
named `create_synthetic_analytics_app` is only for injected synthetic tests. It must
not be deployed. There is no environment switch that enables verified results.
Its synthetic hashes/build identifiers are schema examples, never provenance evidence.

## Enforced boundary

The browser Origin must be exactly `http://127.0.0.1:5173`, Host exactly
`127.0.0.1:8012`, scheme HTTP and peer loopback. Missing/null/duplicate/other Origins
are denied. Forwarded headers do not provide an application-level bypass. CORS permits
GET/POST and preflight with Authorization/Content-Type, without cookies. Responses
are no-store. Framework responses for unregistered routes remain unavailable/404.

The upstream receives only the fake-or-trusted server-side Upload-Capability and
validated Origin, never browser bearer credentials or an owner key. Requests cannot
choose arbitrary destinations, methods or paths. Compressed responses are rejected
instead of permitting decompression expansion. Schema/resource/transport errors are
sanitized; response bodies and credentials are not logged by these modules.

New boundary IDs are bounded URL-safe tokens (letters, digits, underscore, hyphen;
1–512 characters), accommodating existing UUID IDs without changing legacy validation.
Exchange bodies are limited to 8 KiB; metadata to 64 KiB; synthetic list/detail bodies
to 64 MiB. Limits reject explicitly, never truncate. Unsafe JSON integer counts and
metrics are rejected at the new boundary; existing endpoint representations are untouched.

Configuration integer strings retain up to 309 digits and the actual finite Python
conversion boundary. Binary64 values retain exact finite bits, including signed zero.
Revision-specific domains/cross-field rules use exact rational comparisons. There is
**no production revision identifier**: the default registry is empty/fail-closed.
Only synthetic tests supply a revision and approved-version tuple.

## Issuer and lifecycle obligations

The issuer's `consume(code)` must atomically consume globally once, survive intermediary
restarts, and never reissue the same code value. No real issuer/login implementation
was added. Test issuers use fabricated authority records only.

Codes expire after 60 seconds from issuance. Grant timestamps use the server's whole
second snapshot; expiry is snapshot+600 with zero grace and no sliding renewal.
Processing latency never extends the code lifetime. Grant checks happen before lookup
and after upstream/provider work, so observed expiry/revocation discards that response.
Restarting with a fresh store invalidates grants; replay protection across restarts
depends on the trusted issuer's consume-once obligation, not persistence in this store.
No push revocation or production issuer administration is implemented.

`results:read` requires `summary:read`; invalid issuer scope records are rejected, not
expanded. Summary-only grants require empty result IDs. Synthetic lists intersect
explicit IDs; detail authorization precedes lookup. Empty lists do not mean analysis
never ran. Synthetic details compare all declared descriptor identity/config/version
fields, ignoring descriptive additions and object order. This does not verify hashes.

## Isolated validation

Every pytest command used this exact prefix:

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain
```

Track A arguments (abbreviated below as A):

```text
tests/test_analytics_contracts.py tests/test_analytics_grants.py tests/test_analytics_upstream.py tests/test_analytics_intermediary.py -q --tb=short
```

| Command arguments | Exit | Actual outcome |
| --- | ---: | --- |
| A, initial sandbox attempt | 1, interrupted | Setup errors and stalled execution; no completed count claimed |
| A plus `-x`, isolated elevated diagnostic | 1 | 54 passed, 2 setup errors, 1 warning; oversized automatic parameter identifier |
| A plus `-x`, after short parameter IDs | 1 | 84 passed, 1 failed, 1 warning; synthetic test incorrectly reissued consumed code |
| A, after unique synthetic code | 0 | 94 passed, 1 warning |
| A, expanded resource/invalidation cases | 0 | 103 passed, 1 warning |
| A, final grant-response schema cases | 0 | **109 passed, 1 warning, 0.75s** |
| `tests/test_adapter_security_boundary.py -q --tb=short` | 0 | **182 passed, 5.56s** |
| `tests/test_stage5_preservation.py -q --tb=short` | 1 | **1 failed, 1.61s**; line 9 names `datasets/manager.py` |

Final behavioral evidence is 109 new plus 182 existing tests, not the sum of repeated
development runs. The completed Track A runs used approved elevated isolated execution;
the existing credential-boundary and historical checks ran in the ordinary sandbox.
The warning is Starlette's HTTPX TestClient deprecation; dependencies were not changed.
No existing assertions were edited. No backend services or real datasets were used.

Tests cover concurrent exchange, code/grant expiry, replay after restart with the same
issuer authority, revocation during reads, scopes/results, credential containment,
actual HTTPX transport selection with environment proxies set, redirect containment,
Origins/Host/preflight, malformed JSON, lifecycle states, revision/numeric validation,
descriptor changes, nullable metrics and resource limits. All IO is in-process/fake.

## Remaining decisions / limitations

- Supply and review a real trusted issuer and secure capability custody; do not expose
  `consume` or revocation as public endpoints without separate authorization.
- Approve deployment wiring, port availability, trusted-proxy configuration, backend
  Origin configuration and operational clock/capacity controls. None were changed.
- HTTPX timeouts bound IO inactivity, not total wall time of a slow-drip response.
- The browser's conservative expiry/visibility handling remains FE work.
- Track B still needs approved production revisions, canonicalization/hash bytes,
  truthful producer/dependency evidence, immutable storage/publication and legacy policy.
- No OS restart/deployment or real issuer integration was verified. The prior 434-test
  milestone was not rerun here; no full-suite or release readiness is claimed.
- The historical Stage 5 failure remains separate and visible; this record does not
  change Option B or replace the historical preservation gate.
