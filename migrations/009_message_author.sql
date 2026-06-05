-- 009: author_name on messages (clinician interventions)
ALTER TABLE messages ADD COLUMN IF NOT EXISTS author_name VARCHAR;
