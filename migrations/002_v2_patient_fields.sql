-- migrations/002_v2_patient_fields.sql
-- Run in Supabase SQL Editor (or any psql client).
-- Base.metadata.create_all will NOT alter existing tables — this must be run manually.
-- Safe to run multiple times: each statement uses IF NOT EXISTS / DO $$ guards.

-- ── New scored field ──────────────────────────────────────────────────────────
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS previous_loss_count INTEGER NOT NULL DEFAULT 0;

-- Back-fill: patients who already have previous_loss=TRUE get count=1
-- (the true count is unknown for existing rows — 1 is the safe minimum)
UPDATE patients
SET previous_loss_count = 1
WHERE previous_loss = TRUE AND previous_loss_count = 0;

-- ── New collected-but-not-scored fields ──────────────────────────────────────
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS gravidity                INTEGER,
    ADD COLUMN IF NOT EXISTS blood_group              VARCHAR,
    ADD COLUMN IF NOT EXISTS distance_close_to_hospital BOOLEAN,
    ADD COLUMN IF NOT EXISTS rh_negative              BOOLEAN NOT NULL DEFAULT FALSE;

-- ── Missed check-in tracking ─────────────────────────────────────────────────
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS consecutive_missed_checkins INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS missed_checkin_flag          BOOLEAN NOT NULL DEFAULT FALSE;

-- ── Verify ───────────────────────────────────────────────────────────────────
-- After running, confirm with:
-- SELECT column_name, data_type FROM information_schema.columns
-- WHERE table_name = 'patients'
-- ORDER BY ordinal_position;
