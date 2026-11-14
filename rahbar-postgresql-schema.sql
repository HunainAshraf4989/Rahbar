-- Rahbar PostgreSQL design draft for team review.
-- Apply to an empty test database. Convert approved DDL into reviewed Alembic migrations.
BEGIN;

CREATE SCHEMA rahbar;
SET LOCAL search_path = rahbar, public;

-- A subject key is opaque within its host. The web app uses its own host key.
CREATE TABLE learning_principals (
    principal_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    host_key text NOT NULL CHECK (host_key <> ''),
    subject_key text NOT NULL CHECK (subject_key <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (host_key, subject_key)
);

CREATE TABLE learner_accounts (
    principal_id uuid PRIMARY KEY REFERENCES learning_principals,
    email text NOT NULL CHECK (email = btrim(email) AND email <> ''),
    password_hash text NOT NULL CHECK (password_hash <> ''),
    created_at timestamptz NOT NULL DEFAULT now(),
    disabled_at timestamptz
);

CREATE UNIQUE INDEX learner_accounts_email_ci
    ON learner_accounts (lower(email));

CREATE TABLE auth_sessions (
    auth_session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES learner_accounts ON DELETE CASCADE,
    token_digest bytea NOT NULL UNIQUE CHECK (octet_length(token_digest) = 32),
    created_at timestamptz NOT NULL DEFAULT now(),
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    CHECK (expires_at > created_at),
    CHECK (revoked_at >= created_at)
);

CREATE INDEX auth_sessions_by_principal ON auth_sessions (principal_id);

-- Register keys from the published curriculum manifest. Teaching content and assets
-- stay in the versioned content build. Publish new keys for changed learning material.
CREATE TABLE course_releases (
    release_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    course_key text NOT NULL,
    content_version text NOT NULL,
    manifest_ref text NOT NULL,
    manifest_sha256 bytea NOT NULL CHECK (octet_length(manifest_sha256) = 32),
    UNIQUE (course_key, content_version)
);

CREATE TABLE unit_versions (
    unit_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    release_id uuid NOT NULL REFERENCES course_releases,
    unit_key text NOT NULL,
    UNIQUE (release_id, unit_key)
);

CREATE TABLE lesson_versions (
    lesson_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_version_id uuid NOT NULL REFERENCES unit_versions,
    lesson_key text NOT NULL,
    lesson_version text NOT NULL,
    UNIQUE (unit_version_id, lesson_key),
    UNIQUE (lesson_version_id, unit_version_id)
);

CREATE TABLE lesson_segments (
    lesson_version_id uuid NOT NULL REFERENCES lesson_versions,
    segment_key text NOT NULL,
    position integer NOT NULL CHECK (position > 0),
    PRIMARY KEY (lesson_version_id, segment_key),
    UNIQUE (lesson_version_id, position)
);

CREATE TABLE lesson_cursors (
    cursor_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES learning_principals,
    lesson_version_id uuid NOT NULL REFERENCES lesson_versions,
    last_completed_segment_key text,
    restart_segment_key text,
    revision bigint NOT NULL DEFAULT 0 CHECK (revision >= 0),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (principal_id, lesson_version_id),
    UNIQUE (cursor_id, principal_id, lesson_version_id),
    FOREIGN KEY (lesson_version_id, last_completed_segment_key)
        REFERENCES lesson_segments (lesson_version_id, segment_key),
    FOREIGN KEY (lesson_version_id, restart_segment_key)
        REFERENCES lesson_segments (lesson_version_id, segment_key),
    CHECK (last_completed_segment_key IS NOT NULL OR restart_segment_key IS NOT NULL)
);

CREATE TABLE assessment_versions (
    assessment_version_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_version_id uuid NOT NULL REFERENCES unit_versions,
    kind text NOT NULL CHECK (kind IN ('quiz', 'viva')),
    assessment_key text NOT NULL,
    assessment_version text NOT NULL,
    UNIQUE (unit_version_id, kind),
    UNIQUE (assessment_version_id, unit_version_id, kind)
);

CREATE TABLE assessment_attempts (
    attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES learning_principals,
    unit_version_id uuid NOT NULL,
    assessment_version_id uuid NOT NULL,
    kind text NOT NULL CHECK (kind IN ('quiz', 'viva')),
    idempotency_key text NOT NULL CHECK (idempotency_key <> ''),
    request_digest bytea NOT NULL CHECK (octet_length(request_digest) = 32),
    started_at timestamptz NOT NULL DEFAULT now(),
    quiz_choices jsonb CHECK (jsonb_typeof(quiz_choices) = 'object'),
    viva_prompt_position integer CHECK (viva_prompt_position >= 0),
    viva_findings jsonb CHECK (jsonb_typeof(viva_findings) = 'object'),
    UNIQUE (principal_id, kind, idempotency_key),
    UNIQUE (attempt_id, principal_id, unit_version_id, kind),
    FOREIGN KEY (assessment_version_id, unit_version_id, kind)
        REFERENCES assessment_versions (assessment_version_id, unit_version_id, kind),
    CHECK ((kind = 'quiz') = (quiz_choices IS NOT NULL)),
    CHECK (kind = 'viva' OR (viva_prompt_position IS NULL AND viva_findings IS NULL))
);

CREATE INDEX assessment_attempts_by_owner_unit
    ON assessment_attempts (principal_id, unit_version_id, kind);

-- Append one terminal result per attempt. An unfinished resumable viva has no
-- terminal result. A terminal incomplete result is retained and never passes.
CREATE TABLE assessment_results (
    attempt_id uuid PRIMARY KEY REFERENCES assessment_attempts,
    outcome text NOT NULL CHECK (outcome IN ('passed', 'not_yet_passed', 'incomplete')),
    feedback text NOT NULL CHECK (feedback <> ''),
    findings jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(findings) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now()
);

-- Derive completion from pass evidence instead of maintaining a second writable flag.
CREATE VIEW unit_assessment_progress AS
WITH passes AS (
    SELECT a.principal_id, a.unit_version_id,
           bool_or(a.kind = 'quiz' AND r.outcome = 'passed') AS quiz_passed,
           bool_or(a.kind = 'viva' AND r.outcome = 'passed') AS viva_passed
    FROM assessment_attempts AS a
    LEFT JOIN assessment_results AS r ON r.attempt_id = a.attempt_id
    GROUP BY a.principal_id, a.unit_version_id
)
SELECT principal_id, unit_version_id,
       coalesce(quiz_passed, false) AS quiz_passed,
       coalesce(viva_passed, false) AS viva_passed,
       coalesce(quiz_passed AND viva_passed, false) AS is_complete
FROM passes;

CREATE TABLE engine_sessions (
    session_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    principal_id uuid NOT NULL REFERENCES learning_principals,
    unit_version_id uuid NOT NULL REFERENCES unit_versions,
    lesson_version_id uuid NOT NULL,
    cursor_id uuid NOT NULL,
    confirmed_segment_key text,
    mode text NOT NULL CHECK (mode IN ('tutor', 'viva')),
    active_viva_attempt_id uuid,
    viva_attempt_kind text GENERATED ALWAYS AS ('viva'::text) STORED,
    idempotency_key text NOT NULL CHECK (idempotency_key <> ''),
    request_digest bytea NOT NULL CHECK (octet_length(request_digest) = 32),
    connection_epoch uuid NOT NULL DEFAULT gen_random_uuid(),
    generation bigint NOT NULL DEFAULT 0 CHECK (generation >= 0),
    paused boolean NOT NULL DEFAULT true,
    visuals_enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    ended_at timestamptz,
    UNIQUE (principal_id, idempotency_key),
    FOREIGN KEY (lesson_version_id, unit_version_id)
        REFERENCES lesson_versions (lesson_version_id, unit_version_id),
    FOREIGN KEY (cursor_id, principal_id, lesson_version_id)
        REFERENCES lesson_cursors (cursor_id, principal_id, lesson_version_id),
    FOREIGN KEY (lesson_version_id, confirmed_segment_key)
        REFERENCES lesson_segments (lesson_version_id, segment_key),
    FOREIGN KEY (active_viva_attempt_id, principal_id, unit_version_id, viva_attempt_kind)
        REFERENCES assessment_attempts (attempt_id, principal_id, unit_version_id, kind),
    CHECK ((mode = 'viva') = (active_viva_attempt_id IS NOT NULL)),
    CHECK (ended_at >= created_at)
);

CREATE UNIQUE INDEX one_open_engine_session_per_cursor
    ON engine_sessions (cursor_id)
    WHERE ended_at IS NULL;

CREATE TABLE engine_turn_metrics (
    session_id uuid NOT NULL REFERENCES engine_sessions,
    turn_id uuid NOT NULL,
    generation bigint NOT NULL CHECK (generation >= 0),
    connection_epoch uuid NOT NULL,
    outcome_code text NOT NULL,
    provider_names text[] NOT NULL DEFAULT '{}',
    evidence_keys text[] NOT NULL DEFAULT '{}',
    timings_ms jsonb NOT NULL DEFAULT '{}' CHECK (jsonb_typeof(timings_ms) = 'object'),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (session_id, turn_id)
);

-- WhatsApp is an independent identity scope. Inbox and outbox rows survive a restart.
CREATE TABLE whatsapp_conversations (
    conversation_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    business_phone_id text NOT NULL,
    sender_provider_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (business_phone_id, sender_provider_id),
    UNIQUE (conversation_id, business_phone_id)
);

CREATE TABLE whatsapp_inbox (
    inbox_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id uuid NOT NULL,
    business_phone_id text NOT NULL,
    provider_message_id text NOT NULL,
    message_kind text NOT NULL CHECK (message_kind IN ('text', 'voice')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'processing', 'done', 'failed')),
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    received_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (business_phone_id, provider_message_id),
    UNIQUE (inbox_id, conversation_id, business_phone_id),
    FOREIGN KEY (conversation_id, business_phone_id)
        REFERENCES whatsapp_conversations (conversation_id, business_phone_id),
    CHECK ((status = 'processing' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'processing' AND lease_token IS NULL AND lease_until IS NULL))
);

CREATE INDEX whatsapp_inbox_queue ON whatsapp_inbox (available_at, inbox_id)
    WHERE status = 'queued';
CREATE INDEX whatsapp_inbox_lease ON whatsapp_inbox (lease_until)
    WHERE status = 'processing';

-- A media ref points to provider media while a voice job is pending.
-- The worker deletes temporary downloads after processing.
CREATE TABLE whatsapp_inbox_context (
    inbox_id uuid PRIMARY KEY REFERENCES whatsapp_inbox ON DELETE CASCADE,
    body_text text,
    media_provider_ref text,
    quoted_provider_message_id text,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours'),
    CHECK (body_text IS NOT NULL OR media_provider_ref IS NOT NULL)
);

CREATE INDEX whatsapp_inbox_context_expiry ON whatsapp_inbox_context (expires_at);

CREATE TABLE whatsapp_outbox (
    outbox_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    inbox_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    business_phone_id text NOT NULL,
    reply_kind text NOT NULL CHECK (reply_kind IN ('text', 'voice')),
    status text NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued', 'sending', 'sent', 'delivered', 'read', 'failed', 'unknown')),
    provider_message_id text,
    attempt_count integer NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at timestamptz NOT NULL DEFAULT now(),
    lease_token uuid,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (inbox_id, reply_kind),
    UNIQUE (business_phone_id, provider_message_id),
    FOREIGN KEY (inbox_id, conversation_id, business_phone_id)
        REFERENCES whatsapp_inbox (inbox_id, conversation_id, business_phone_id),
    CHECK ((status = 'sending' AND lease_token IS NOT NULL AND lease_until IS NOT NULL)
        OR (status <> 'sending' AND lease_token IS NULL AND lease_until IS NULL)),
    CHECK (status NOT IN ('sent', 'delivered', 'read') OR provider_message_id IS NOT NULL)
);

CREATE INDEX whatsapp_outbox_queue ON whatsapp_outbox (available_at, outbox_id)
    WHERE status = 'queued';
CREATE INDEX whatsapp_outbox_lease ON whatsapp_outbox (lease_until)
    WHERE status = 'sending';

-- Approved reply text supports a text send or regeneration of the voice reply.
CREATE TABLE whatsapp_outbox_context (
    outbox_id uuid PRIMARY KEY REFERENCES whatsapp_outbox ON DELETE CASCADE,
    approved_text text NOT NULL,
    expires_at timestamptz NOT NULL DEFAULT (now() + interval '24 hours')
);

CREATE INDEX whatsapp_outbox_context_expiry ON whatsapp_outbox_context (expires_at);

COMMIT;
