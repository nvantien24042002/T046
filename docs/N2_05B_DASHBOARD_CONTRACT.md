# N2-05B server-owned dashboard.v1

## Read-only projection APIs

- POST /api/v1/datasets/{dataset_id}/dashboard: Upload-Capability, page, query, expected_fingerprint (required). Reads the existing analysis cache under the analysis lock. Never runs analyze. Missing analysis -> 409 ANALYSIS_NOT_RUN; changed fingerprint -> 409 RESULT_CHANGED. Responses use Cache-Control: no-store.
- POST /analytics/v1/datasets/{dataset_id}/results/{result_id}/dashboard: Bearer grant with results:read and the specific result_id. Requires expected_payload_sha256. Reads and verifies immutable ResultStore data, reauthorizes after projection. Normal factory must be composed with a ResultStore to expose this route.

Both APIs return schema_version=dashboard.v1, dataset_id, page, source (the supplied producer fingerprint or server result ID/hash), state, reason, metrics, tables, charts, controls, notes. source is identity binding, not a new verification claim. No result IDs are manufactured for the integrated API.

States: ready (available result), empty (valid query with no matching rows), unsupported (dataset capability unavailable; no metrics/tables/charts), no_results (coverage targets not supplied; observed data may still be present). Unknown fields, invalid thresholds, invalid targets -> 422. Inconsistent stored analysis -> 502. No fixture fallback.

## Page contracts

- overview: archive image count is distinct from analyzed-row count. Review/class/status/priority aggregates are server computed.
- quality: query.quality_status; counts/mean/issues and rows share the filter. No matching rows -> mean null. Effective image thresholds shown only when the analysis cache contains producer-recorded effective_config; legacy and immutable payloads without it remain explicitly unavailable.
- labels: query.class_label, priority, review_scope=review. Aggregates and issue frequencies are computed over the same rows. image availability metric counts boxes and is labeled accordingly.
- distribution: classes/attributes from analysis; area stats over valid geometry, occlusion, scene counts and imbalance computed on server. min_count/min_share/max_share default to the existing Streamlit policy (30/1/60), not a frontend policy. Inputs are validated server-side.
- review: preview paths come from analysis image inventory. Ambiguous/unresolved paths stay null. Overlay is geometry only, not QA priority coloring. Decision persistence and annotation editing are not supported.
- reports: server-generated quality, label, class review and box tables. Browser only serializes those tables to CSV. No role/export authorization was added in this iteration.
- coverage: uses core.coverage_dynamic; no default targets. query.targets accepts up to 250 explicit targets (filters, unit, target, weight). Alternative query.generate accepts explicit dimensions/unit/target/weight/include_unobserved and calls the existing server generator. No client evaluation or recommendation. Targets are request-local, not persisted. Coverage uses XML image records and valid objects; no ground truth accuracy claim.
- scene-finder: query.weather, timeofday, keyword. Normalization, total/matched/unresolved metrics, filtering and canonical preview paths are server-owned. Scene tags come from CVAT, not image inference.

## Boundaries

Backend and frontend repositories remain independent. No roles were implemented. Existing owner/capability and grant scope checks still apply; they are not assignment/role authorization. Integrated local CORS permits 5173 and 4173; intermediary retains its existing strict 127.0.0.1:5173 origin.

Existing caches are not rewritten or reanalyzed on dashboard reads. New analyses record effective_config. Older caches that lack configuration do not receive fabricated defaults. Cache-to-ResultStore producer/config provenance migration remains separate.

Tests use isolated stores, fixtures and TestClient; these are not live browser/network E2E evidence.
