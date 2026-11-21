-- Additive development text-session migration; apply explicitly before serving.
BEGIN;
CREATE SCHEMA IF NOT EXISTS rahbar;
CREATE TABLE IF NOT EXISTS rahbar.rag_text_sessions (
    id text PRIMARY KEY,
    version text NOT NULL,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    expires double precision NOT NULL,
    history jsonb NOT NULL DEFAULT '[]'::jsonb
      CHECK (jsonb_typeof(history) = 'array' AND jsonb_array_length(history) <= 4)
);
CREATE INDEX IF NOT EXISTS rag_text_sessions_expiry ON rahbar.rag_text_sessions(expires);
REVOKE ALL ON rahbar.rag_text_sessions FROM PUBLIC;
COMMIT;
