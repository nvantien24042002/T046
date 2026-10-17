CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE uploads (
    id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    capability_hash TEXT NOT NULL,
    request_key TEXT NOT NULL,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL CHECK(size_bytes > 0),
    chunk_bytes INTEGER NOT NULL CHECK(chunk_bytes > 0),
    state TEXT NOT NULL CHECK(state IN ('created','uploading','uploaded','cancelled','expired','failed')),
    committed_bytes INTEGER NOT NULL DEFAULT 0 CHECK(committed_bytes >= 0 AND committed_bytes <= size_bytes),
    reserved_bytes INTEGER NOT NULL CHECK(reserved_bytes >= 0),
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    absolute_expires_at REAL NOT NULL,
    error_code TEXT,
    cleanup_pending INTEGER NOT NULL DEFAULT 0 CHECK(cleanup_pending IN (0,1)),
    cleaned_at REAL,
    UNIQUE(owner_id, request_key)
);
CREATE TABLE receipts (
    upload_id TEXT NOT NULL REFERENCES uploads(id),
    chunk_index INTEGER NOT NULL CHECK(chunk_index >= 0),
    offset_bytes INTEGER NOT NULL CHECK(offset_bytes >= 0),
    length_bytes INTEGER NOT NULL CHECK(length_bytes > 0),
    sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
    committed_at REAL NOT NULL,
    PRIMARY KEY(upload_id, chunk_index),
    UNIQUE(upload_id, offset_bytes)
);
CREATE INDEX upload_state_idx ON uploads(state, cleaned_at);
PRAGMA user_version=1;
