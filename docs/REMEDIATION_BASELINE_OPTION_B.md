# N2-05B remediation baseline — Option B

Record date: 2026-10-17 (Asia/Saigon).

The project owner approved retaining the current cancellation hardening and
creating this separate record. This is a source-review and behavioral-validation
baseline, not a replacement for the historical Stage 5 baseline or a release
approval. API Contract 0.1.0-draft remains unapproved.

## Historical versus remediation evidence

The original `docs/CORE_PIPELINE_STAGE5.md`,
`tests/stage5/protected_sources.json`, and
`tests/test_stage5_preservation.py` remain unchanged. Their requirement is
whole-file, bit-for-bit preservation. Current manager/runner bytes do not meet
that historical requirement; this record does not make that check pass.

| Source | Historical protected SHA-256 | Current remediation SHA-256 |
| --- | --- | --- |
| `datasets/manager.py` | `3ccb1db5042a21ed3c6f37182a62f01b7dac18176aa65217bc8f3a61069adeea` | `3757d6971ec390e04d953e9489a8bf05f9e0296377f9623b00043c28892d9bde` |
| `datasets/runner.py` | `e95ad2ea408e27826bf228aa2d46240d9b60c2c52d4289976b516cf1e12ace94` | `8fd40fda163e0866631929b0ea0f6d1e289fc5ffe2175edc3364cac3f5b5f896` |

Current sources belong to committed snapshot
`55fae0b2eb096dba5f2e55f2f55995292a76cae6`. That commit is a remediation
snapshot, not proof of pre-remediation history.

Historical evidence was separately exported with owner approval, as exact raw
bytes, and verified against the original manifest:

| Source | Git blob | Bytes | External artifact filename |
| --- | --- | ---: | --- |
| manager | `aeb0f1718c40e2b7761a00b1831639c2ddcd4695` | 15912 | `manager.aeb0f1718c40e2b7761a00b1831639c2ddcd4695.py` |
| runner | `6c2f814a3d1a402721beda58a726a1352aca3bbf` | 6960 | `runner.6c2f814a3d1a402721beda58a726a1352aca3bbf.py` |

Artifact directory:
`C:\Users\Tien Nguyen\.codex\visualizations\2026\10\16\01a146cf-9294-72e1-8365-ec8871f1ac7e\historical-evidence`.
Their SHA-256 values are the historical column above. They authenticate source
bytes, not an independently established historical commit or algorithm provenance.

## Reviewed source inventory

Block IDs refer to the historical-versus-current review; current line numbers
are descriptive, not a new executable checksum manifest.

| Manager block | Current lines | Reviewed behavior |
| --- | --- | --- |
| M1 | 14 | Import uncertain-worker signal |
| M2 | 23–24 | Registry/worker uncertainty state |
| M3 | 69–70 | Quarantined registry access |
| M4 | 72–88 | Settle pending transaction before reconciliation |
| M5 | 111–112 | Prevent uncertain worker terminal transition |
| M6 | 114–130 | Atomic dataset/attempt terminal update and read-back |
| M7 | 186–190 | Retain uncertain worker; settle registry failures |
| M8 | 192–199 | Preserve valid committed ready; preserve DB classification |
| M9 | 203–204 | Reject invalid ready publication |
| M10 | 223–225 | Guard index connection initialization |
| M11 | 230–231 | Distinguish index errors from authoritative registry errors |
| M12 | 233–237 | Guard/classify index close |
| M13 | 239–255 | Ready publication integrity predicate |
| M14 | 287 | Persist cancellation intent through startup recovery |
| M15 | 293 | Reuse stronger recovery predicate |
| M16 | 295–304 | Reconcile recovery against committed state |
| M17 | 312–318 | Reconcile cancellation acknowledgement |

| Runner block | Current lines | Reviewed behavior |
| --- | --- | --- |
| R1 | 12–15 | Distinct unconfirmed-stop exception |
| R2 | 89–118 | Shared protocol parser and retained worker errors |
| R3 | 135–142 | Drain queued errors before cancellation |
| R4 | 147–150 | Preserve timeout/failed-exit precedence |
| R5 | 153 | Reuse shared parser |
| R6 | 159–160 | Retain original exception for cleanup reconciliation |
| R7 | 162–203 | Guard stop/wait/drain and confirm termination |
| R8 | deletion before 205 | Replace former unconditional cleanup sequence |
| R9 | 206–209 | Avoid closing stdout while reader remains alive |
| R10 | 211–226 | Cleanup precedence, causal evidence and process retention |

Associated existing tests remain unchanged: `test_cancellation_faults.py`,
`test_runner_cancellation_faults.py`, `test_validation_cancellation.py`,
`test_dataset_manager.py`, `test_phase1_remediation.py`,
`test_phase2a_validation.py`, `test_adapter_security_boundary.py`, and
`test_core_pipeline.py` under `tests/`.

## Required behavioral guarantees

- Cancellation intent can commit while validation is running, but a request alone
  does not prove worker termination or override an observed worker failure.
- Timeout, confirmed cancellation, worker error, cleanup failure and unconfirmed
  stop remain distinguishable; unresolved execution retains safe reservation state.
- Dataset/attempt terminal transitions are atomic. Lost acknowledgements are
  reconciled against committed state; unavailable read-back must not be called rollback.
- Publication is not visible before valid commit/seals. Valid committed ready
  results survive post-commit errors; recovery rejects inconsistent publication.
- Authoritative registry errors retain their policy classification. Index errors
  must not be mistaken for an authoritative registry outage.
- Retries do not duplicate attempts, release reservations early, or weaken global
  admission, authentication, lease or existing pipeline-parity behavior.
- Test doubles/temporary roots are not evidence of live service verification.

## Additional isolated coverage

`tests/test_remediation_runner_gaps.py` exercises malformed protocol messages in
normal/drain paths, bounded synthetic stuck-reader cleanup, process-stop failure
combinations, and original/cleanup-error precedence. No OS worker is launched.

`tests/test_remediation_manager_gaps.py` independently invalidates ready predicates
in temporary synthetic registries, verifies recovery refusal before reader
admission, and tests committed cancellation with lost acknowledgement and
unavailable read-back followed by isolated reopen/recovery. A test-only corruption
case disables FK checking only on its temporary connection. No production trigger
or policy is changed. Archive-size mismatch uses a read double to retain payload pins.

These predicate tests exercise recovery admission, not a promise that every
ordinary read rechecks every registry field. OS-level orphan survival across parent
death/restart, real buffered-reader lock behavior, crash durability/fsync failure,
and exhaustive protocol/exception combinations remain outside this evidence.

## Validation and acceptance policy

Run new gap tests, existing targeted behavioral suites, and historical preservation
separately. Record commands, exit codes, counts and environment errors; never
skip/xfail or change historical hashes to hide failures. No full-suite or release
claim follows from targeted tests. A baseline change requires separate owner review.

No authentication, intermediary, storage/publication, origin policy or verified
result work is approved by this record. Those integration decisions remain gates.

## Executed targeted validation — 2026-10-17

Every command below used the exact prefix:

```powershell
.venv\Scripts\python.exe -B -m pytest --disable-plugin-autoload -p no:cacheprovider --assert=plain
```

| Arguments appended to prefix | Exit | Actual result |
| --- | ---: | --- |
| `tests/test_remediation_runner_gaps.py tests/test_remediation_manager_gaps.py -q --tb=short` | 0 | 41 passed, 1 warning, 11.49s |
| `tests/test_runner_cancellation_faults.py -q --tb=short` | 0 | 8 passed, 0.04s |
| `tests/test_cancellation_faults.py -q --tb=short` | 0 | 27 passed, 1 warning, 14.67s |
| `tests/test_validation_cancellation.py -q --tb=short` | 0 | 15 passed, 1 warning, 9.61s |
| `tests/test_dataset_manager.py tests/test_phase1_remediation.py tests/test_phase2a_validation.py tests/test_adapter_security_boundary.py tests/test_core_pipeline.py -q --tb=short` | 0 | 343 passed, 1 warning, 8.38s |
| `tests/test_stage5_preservation.py -q --tb=short` | 1 | 1 failed, 0.19s; assertion at line 9 names `datasets/manager.py` |

Behavioral total: **434 passed, zero failed/errors** in the listed groups.
Historical preservation: **1 failed**, separately classified and unsuppressed.
A direct inventory confirmed 9 historical matches and 2 mismatches (manager and
runner); the assertion stops at manager. This is not a full-suite run.

The temporary-root suites used approved elevated isolated execution to avoid the
previously diagnosed sandbox temporary-directory problem. No setup errors occurred
in this milestone. Runner-only existing tests ran in the ordinary sandbox. Repeated
warnings concern Starlette's deprecated HTTPX TestClient integration; no dependency
was changed. In-process TestClient requests and a test-owned worker are not live
backend-service checks.

Post-run SHA-256 checks confirmed both protected sources, original manifest,
original preservation assertion and both external historical artifacts unchanged.
Tracked Git differences were empty. Only this document and the two new test files
were added, without commit or push.

Recommendation: the specified targeted behavioral gate is satisfied. Proceed only
to the next owner-approved integration design/test stage; this does not approve
API Contract 0.1.0-draft, implement integration, satisfy historical preservation,
establish full-suite success, or establish release readiness.
