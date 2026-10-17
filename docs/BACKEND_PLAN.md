# Backend implementation status

Stage 3 checkpoint (2026-10-16): durable byte-upload storage/API and a separate browser protocol client are implemented. See `DURABLE_UPLOAD_STAGE3.md` and `DURABLE_UPLOAD_STAGE3_TEST_RESULTS.md` for schema, ordering, guarantees, tests and limitations. The accepted prototype and original dashboard remain unchanged. Stage 4 is not started; the earlier Stage 1/2 status below is retained as historical context.

The approved architecture is in `LARGE_ZIP_UPLOAD_PLAN.md`; current runnable work and evidence are in `UPLOAD_PROTOTYPE_CHECKPOINT.md` and `UPLOAD_PROTOTYPE_TEST_RESULTS.md`.

Scope at this checkpoint: establish unchanged business baselines and prove direct browser-to-FastAPI chunk transport in a separate Streamlit prototype. Production `app.py` and core algorithms remain unchanged. `prototype/api.py` must not be promoted as the production backend: sessions are volatile, files unvalidated and never ready.

Next stage: small shared SQLite/filesystem layer and production upload contract with bounded raw streaming, durable receipts/reservations, fsync/transaction recovery, idempotent retries, owner access, status/resume/cancel and cleanup. Keep core independent of Streamlit and HTTP; no backend import of app.py or UI modules. Validation/indexing run outside completion requests in a bounded durable worker. Only validated manifests may publish datasets.

After upload protocol passes crash/error tests, adapt readers using the baseline outputs, one bounded image at a time, with path-backed previews and scalar results. Preserve thresholds/statuses/reports and all existing Streamlit workflows. The full later analysis API/React migration is deferred. Stop before durable implementation at the user's requested checkpoint.
