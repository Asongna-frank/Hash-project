-- 007: optional unique email on hospitals (second login identifier)
ALTER TABLE hospitals ADD COLUMN IF NOT EXISTS email VARCHAR;
CREATE UNIQUE INDEX IF NOT EXISTS ix_hospitals_email ON hospitals (email);
