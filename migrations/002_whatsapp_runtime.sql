-- New WhatsApp runtime tables; no changes to the existing inbox/outbox design.
BEGIN;
CREATE TABLE IF NOT EXISTS rahbar.wa_jobs (
    job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES rahbar.learner_accounts(principal_id) ON DELETE CASCADE,
    business_phone_id text NOT NULL,
    sender text NOT NULL CHECK (sender ~ '^[1-9][0-9]{6,14}$'),
    provider_message_id text NOT NULL,
    source_timestamp bigint NOT NULL,
    message_kind text NOT NULL CHECK (message_kind IN ('text', 'audio')),
    input_content text,
    quoted_message_id text,
    question_text text,
    answer_text text,
    reply_media_id text,
    text_message_id text,
    audio_message_id text,
    text_state text NOT NULL DEFAULT 'pending' CHECK (text_state IN ('pending','sending','sent')),
    audio_state text NOT NULL DEFAULT 'pending' CHECK (audio_state IN ('pending','sending','sent')),
    status text NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','processing','ready','done','failed','unknown')),
    attempts integer NOT NULL DEFAULT 0,
    available_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (business_phone_id, provider_message_id)
);
CREATE INDEX IF NOT EXISTS wa_jobs_pending ON rahbar.wa_jobs (available_at, created_at)
    WHERE status IN ('queued','processing','ready');
CREATE TABLE IF NOT EXISTS rahbar.wa_history (
    job_id uuid PRIMARY KEY,
    principal_id uuid NOT NULL REFERENCES rahbar.learner_accounts(principal_id) ON DELETE CASCADE,
    business_phone_id text NOT NULL,
    question_message_id text NOT NULL,
    text_message_id text,
    audio_message_id text,
    question text NOT NULL CHECK (char_length(question) BETWEEN 1 AND 4000),
    answer text NOT NULL CHECK (char_length(answer) BETWEEN 1 AND 4000),
    completed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS wa_history_student ON rahbar.wa_history
    (principal_id, business_phone_id, completed_at DESC, job_id DESC);
CREATE OR REPLACE FUNCTION rahbar.wa_history_lock() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtextextended('wa-history:' || NEW.principal_id::text, 0));
    RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION rahbar.wa_history_trim() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    DELETE FROM rahbar.wa_history WHERE job_id IN (
        SELECT job_id FROM rahbar.wa_history WHERE principal_id=NEW.principal_id
        ORDER BY completed_at DESC, job_id DESC OFFSET 5
    );
    RETURN NULL;
END $$;
DROP TRIGGER IF EXISTS wa_history_serialized ON rahbar.wa_history;
CREATE TRIGGER wa_history_serialized BEFORE INSERT OR UPDATE ON rahbar.wa_history
    FOR EACH ROW EXECUTE FUNCTION rahbar.wa_history_lock();
DROP TRIGGER IF EXISTS wa_history_bounded ON rahbar.wa_history;
CREATE TRIGGER wa_history_bounded AFTER INSERT OR UPDATE ON rahbar.wa_history
    FOR EACH ROW EXECUTE FUNCTION rahbar.wa_history_trim();
REVOKE ALL ON rahbar.wa_jobs, rahbar.wa_history FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('REVOKE ALL ON rahbar.wa_jobs, rahbar.wa_history FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
COMMIT;
