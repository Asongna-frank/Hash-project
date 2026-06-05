-- 005: doctor_name on appointments + daily_wellness table (Home cards)
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS doctor_name VARCHAR;

-- daily_wellness is also created automatically by SQLAlchemy create_all;
-- kept here so the migration history stays complete.
CREATE TABLE IF NOT EXISTS daily_wellness (
    id UUID PRIMARY KEY,
    patient_id UUID NOT NULL REFERENCES patients(id),
    date DATE NOT NULL,
    hydration_glasses INTEGER,
    sleep_hours DOUBLE PRECISION,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_wellness_patient_date UNIQUE (patient_id, date)
);
