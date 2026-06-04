-- migrations/003_personnel_table.sql
-- Run in Supabase SQL Editor (or any psql client) when upgrading an EXISTING database.
-- Safe to run multiple times: all statements use IF NOT EXISTS / DO $$ guards.
-- Fresh databases: Base.metadata.create_all handles this automatically at startup.

-- ── 1. Create personnel table ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS personnel (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    hospital_id UUID NOT NULL REFERENCES hospitals(id) ON DELETE CASCADE,
    name        VARCHAR NOT NULL,
    phone       VARCHAR NOT NULL,
    email       VARCHAR,
    role        VARCHAR NOT NULL DEFAULT 'admin',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_personnel_hospital_id ON personnel(hospital_id);

-- ── 2. Migrate existing personnel_name / personnel_contact → personnel rows ──
-- Runs only if the old columns still exist on hospitals.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'hospitals' AND column_name = 'personnel_name'
    ) THEN
        INSERT INTO personnel (hospital_id, name, phone, role)
        SELECT id, personnel_name, personnel_contact, 'admin'
        FROM   hospitals
        WHERE  personnel_name IS NOT NULL
          AND  id NOT IN (SELECT DISTINCT hospital_id FROM personnel);
    END IF;
END $$;

-- ── 3. Add is_active to hospitals ────────────────────────────────────────────
ALTER TABLE hospitals
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- ── 4. Add is_active to patients ─────────────────────────────────────────────
ALTER TABLE patients
    ADD COLUMN IF NOT EXISTS is_active BOOLEAN NOT NULL DEFAULT TRUE;

-- ── 5. Drop old flat columns AFTER data migration ────────────────────────────
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'hospitals' AND column_name = 'personnel_name'
    ) THEN
        ALTER TABLE hospitals DROP COLUMN personnel_name;
        ALTER TABLE hospitals DROP COLUMN personnel_contact;
    END IF;
END $$;

-- ── Verify (run manually to confirm) ─────────────────────────────────────────
-- SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_name IN ('hospitals', 'patients', 'personnel')
--   ORDER BY table_name, ordinal_position;
