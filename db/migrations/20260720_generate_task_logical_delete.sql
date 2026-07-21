ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS idx_generate_task_active_user_created
    ON generate_task (user_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;
