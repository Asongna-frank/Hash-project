-- 006: kick_counts table (fetal-movement counter) — also auto-created by create_all
CREATE TABLE IF NOT EXISTS kick_counts (
    id UUID PRIMARY KEY,
    patient_id UUID NOT NULL REFERENCES patients(id),
    date DATE NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    duration_minutes INTEGER,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_kicks_patient_date UNIQUE (patient_id, date)
);
