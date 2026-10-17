CREATE TABLE IF NOT EXISTS validation_meta (version INTEGER NOT NULL CHECK(version=1));
INSERT INTO validation_meta SELECT 1 WHERE NOT EXISTS(SELECT 1 FROM validation_meta);
CREATE TABLE IF NOT EXISTS validation_datasets (
    upload_id TEXT PRIMARY KEY REFERENCES uploads(id),
    dataset_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK(state IN ('validating','indexing','publishing','ready','failed','cancelled')),
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error_code TEXT,
    manifest_sha TEXT,
    index_sha TEXT,
    payload_sha TEXT
);
CREATE TABLE IF NOT EXISTS validation_attempts (
    id TEXT PRIMARY KEY,
    upload_id TEXT NOT NULL REFERENCES uploads(id),
    state TEXT NOT NULL CHECK(state IN ('active','published','failed','cleaned')),
    reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes>=0)
);
CREATE TABLE IF NOT EXISTS validation_leases (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL REFERENCES validation_datasets(dataset_id)
);
-- Even an unextended Stage 3 caller may not cancel/free a referenced payload.
CREATE TRIGGER IF NOT EXISTS validation_payload_pin BEFORE UPDATE ON uploads
WHEN EXISTS(SELECT 1 FROM validation_datasets WHERE upload_id=OLD.id)
 AND (NEW.state IN ('cancelled','expired') OR NEW.cleaned_at IS NOT NULL OR NEW.size_bytes!=OLD.size_bytes)
BEGIN SELECT RAISE(ABORT,'DATASET_PAYLOAD_PINNED'); END;
CREATE TRIGGER IF NOT EXISTS validation_no_payload_delete BEFORE DELETE ON uploads
WHEN EXISTS(SELECT 1 FROM validation_datasets WHERE upload_id=OLD.id)
BEGIN SELECT RAISE(ABORT,'DATASET_PAYLOAD_PINNED'); END;
-- Serialize new admission against validation workspace reservations, including
-- failed diagnostic workspace until explicit safe temporary cleanup.
CREATE TRIGGER IF NOT EXISTS validation_admission_guard BEFORE INSERT ON uploads
WHEN EXISTS(SELECT 1 FROM validation_attempts WHERE reserved_bytes>0)
BEGIN SELECT RAISE(ABORT,'VALIDATION_WORKSPACE_RESERVED'); END;
