# Large upload design: prototype checkpoint

Stage 3 update (2026-10-16): the separate durable service now uses SQLite receipts/reservations, scratch-fsync -> payload-fsync -> SQLite EXTRA COMMIT -> acknowledgement, owner/capability checks and conservative recovery/cleanup. Details and actual evidence are in `DURABLE_UPLOAD_STAGE3.md` / `DURABLE_UPLOAD_STAGE3_TEST_RESULTS.md`. It never publishes validated/ready datasets. The Stage 1/2 prototype description below remains historical; its files and volatile behavior are unchanged.

See `LARGE_ZIP_UPLOAD_PLAN.md` for full states, security/validation requirements, limits, capacity formulas, file plan and acceptance gates. See `UPLOAD_PROTOTYPE_CHECKPOINT.md` for actual runnable code and limitations.

The browser experiment uses Streamlit 1.66 CCv2 inline JavaScript, File.slice and sequential XHR. No framework/npm package is required. Direct chunks bypass the ordinary RAM-backed Streamlit uploader; only scalar metadata crosses the component bridge. This design choice is tested on the pinned environment, not on minimum Streamlit 1.35.

Prototype states are uploading -> received_unvalidated or failed, always durable=false/ready=false. Prototype limits are 16 MiB total, 64 KiB chunks, 4 KiB initialization JSON, one active/eight total volatile sessions, local-only authenticated API. There is no dataset registration or promised restart/refresh resume. This deliberately differs from the production state machine until Stage 3/4 exists.

Production target remains created -> uploading -> uploaded -> validating -> ready/failed, with cancellation/expiry, SQLite session/receipt state, private one-volume files, ownership on all actions, bounded raw request streaming, fsync/transaction acknowledgements and deterministic recovery. Whole-file assembly copies, ZIP buffers and all-image lists are prohibited. Validate ZIP64 directory allocation limits before full parsing, stream every member for CRC/actual expansion, then safe bounded XML/indexing. No ready publication before integrity/resource validation.

Initial planning limits remain provisional: configurable 50 GiB compressed, 250 GiB expanded, 100k entries/32 MiB directory/32 MiB XML, 50 MiB encoded image/16 MP image, bounded metadata and one analysis worker. They are not available in the prototype and are not measured capacity claims. The real storage path/capacity/ACL, timeouts/proxy body buffering, owner identity and representative dataset statistics must be resolved before production-scale validation.
