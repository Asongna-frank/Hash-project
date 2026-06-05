-- 008: clinician notes (also auto-created by create_all)
CREATE TABLE IF NOT EXISTS patient_notes (
    id UUID PRIMARY KEY,
    patient_id UUID NOT NULL REFERENCES patients(id),
    hospital_id UUID NOT NULL REFERENCES hospitals(id),
    text TEXT NOT NULL,
    author_name VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
