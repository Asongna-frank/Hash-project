-- migrations/004_appointments_v2.sql
-- Replaces the 24h/2h reminder scheme with the unified alarm engine.
-- Run in Supabase SQL Editor against any EXISTING database.
-- Fresh databases: Base.metadata.create_all handles this at startup.
-- Safe to run multiple times (IF NOT EXISTS / DO $$ guards).

-- ── 1. Add new columns ────────────────────────────────────────────────────────

ALTER TABLE appointments
    ADD COLUMN IF NOT EXISTS reminder_datetime  TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS created_by         VARCHAR     NOT NULL DEFAULT 'patient',
    ADD COLUMN IF NOT EXISTS alarm_1_sent       BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS alarm_2_sent       BOOLEAN     NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS confirmation_sent  BOOLEAN     NOT NULL DEFAULT FALSE;

-- ── 2. Back-fill reminder_datetime for existing rows ─────────────────────────
-- Use appointment_datetime − 30 min as a safe universal default.
UPDATE appointments
SET reminder_datetime = appointment_datetime - INTERVAL '30 minutes'
WHERE reminder_datetime IS NULL;

-- Now make reminder_datetime NOT NULL (all rows have a value).
ALTER TABLE appointments
    ALTER COLUMN reminder_datetime SET NOT NULL;

-- ── 3. Migrate old flags → new flags ────────────────────────────────────────
-- Rows whose 24h reminder was sent → treat as alarm_1 already fired.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'appointments' AND column_name = 'reminder_24h_sent'
    ) THEN
        UPDATE appointments
        SET alarm_1_sent = reminder_24h_sent,
            alarm_2_sent = reminder_2h_sent;
    END IF;
END $$;

-- ── 4. Drop old columns ───────────────────────────────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'appointments' AND column_name = 'reminder_24h_sent'
    ) THEN
        ALTER TABLE appointments
            DROP COLUMN reminder_24h_sent,
            DROP COLUMN reminder_2h_sent;
    END IF;
END $$;

-- ── Verify (run manually) ────────────────────────────────────────────────────
-- SELECT column_name, data_type, is_nullable
-- FROM   information_schema.columns
-- WHERE  table_name = 'appointments'
-- ORDER  BY ordinal_position;
