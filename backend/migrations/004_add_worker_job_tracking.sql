ALTER TABLE queued_jobs ADD COLUMN IF NOT EXISTS assigned_worker VARCHAR(80);
ALTER TABLE queued_jobs ADD COLUMN IF NOT EXISTS attempt_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE queued_jobs ADD COLUMN IF NOT EXISTS progress_percent INTEGER NOT NULL DEFAULT 0;
ALTER TABLE queued_jobs ADD COLUMN IF NOT EXISTS progress_message VARCHAR(160);
ALTER TABLE queued_jobs ADD COLUMN IF NOT EXISTS updated_at TEXT;
CREATE INDEX IF NOT EXISTS ix_queued_jobs_assigned_worker ON queued_jobs (assigned_worker);