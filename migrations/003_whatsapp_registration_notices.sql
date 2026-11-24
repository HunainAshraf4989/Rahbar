-- Registration reminders are metadata-only and never part of tutor Q&A history.
BEGIN;
CREATE TABLE IF NOT EXISTS rahbar.wa_registration_notices (
    notice_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_phone_id text NOT NULL,
    sender text NOT NULL CHECK (sender ~ '^[1-9][0-9]{6,14}$'),
    provider_message_id text NOT NULL,
    source_timestamp bigint NOT NULL,
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','sending','done','failed','unknown','skipped')),
    created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (business_phone_id, provider_message_id)
);
CREATE INDEX IF NOT EXISTS wa_registration_notices_pending
    ON rahbar.wa_registration_notices (business_phone_id, created_at)
    WHERE status='queued';
CREATE INDEX IF NOT EXISTS wa_registration_notices_sender
    ON rahbar.wa_registration_notices (business_phone_id, sender, created_at DESC);
REVOKE ALL ON rahbar.wa_registration_notices FROM PUBLIC;
DO $$ DECLARE role_name text; BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon','authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname=role_name) THEN
            EXECUTE format('REVOKE ALL ON rahbar.wa_registration_notices FROM %I', role_name);
        END IF;
    END LOOP;
END $$;
COMMIT;
