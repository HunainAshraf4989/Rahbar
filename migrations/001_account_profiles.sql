-- Additive migration. Existing accounts remain usable with NULL profile fields.
BEGIN;
ALTER TABLE rahbar.learner_accounts
    ADD COLUMN IF NOT EXISTS full_name text,
    ADD COLUMN IF NOT EXISTS phone_number text;

DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'rahbar.learner_accounts'::regclass
                     AND conname = 'learner_accounts_full_name_valid') THEN
        ALTER TABLE rahbar.learner_accounts ADD CONSTRAINT learner_accounts_full_name_valid
            CHECK (full_name IS NULL OR (char_length(btrim(full_name)) BETWEEN 1 AND 120));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint
                   WHERE conrelid = 'rahbar.learner_accounts'::regclass
                     AND conname = 'learner_accounts_phone_e164') THEN
        ALTER TABLE rahbar.learner_accounts ADD CONSTRAINT learner_accounts_phone_e164
            CHECK (phone_number IS NULL OR phone_number ~ '^\+[1-9][0-9]{6,14}$');
    END IF;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS learner_accounts_phone_unique
    ON rahbar.learner_accounts (phone_number) WHERE phone_number IS NOT NULL;
COMMIT;
