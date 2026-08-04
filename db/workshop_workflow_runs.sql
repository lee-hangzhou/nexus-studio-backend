CREATE TABLE IF NOT EXISTS workshop_workflow_runs (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  workflow_id VARCHAR(64) NOT NULL,
  workflow_revision BIGINT NOT NULL,
  schedule_id VARCHAR(64),
  trigger VARCHAR(16) NOT NULL,
  status VARCHAR(16) NOT NULL,
  current_node_id VARCHAR(128),
  error_message TEXT,
  started_at TIMESTAMPTZ,
  finished_at TIMESTAMPTZ,
  revision BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_workflow_runs_trigger
    CHECK (trigger IN ('manual', 'schedule', 'trial')),
  CONSTRAINT ck_workshop_workflow_runs_status
    CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'blocked', 'cancelled')),
  CONSTRAINT ck_workshop_workflow_runs_revision CHECK (revision >= 1),
  CONSTRAINT fk_workshop_workflow_runs_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_workflow_runs_workflow_project
    FOREIGN KEY (project_id, workflow_id)
    REFERENCES workshop_workflows(project_id, id) ON DELETE CASCADE
);

ALTER TABLE workshop_workflow_runs
  DROP CONSTRAINT IF EXISTS ck_workshop_workflow_runs_status;
ALTER TABLE workshop_workflow_runs
  ADD CONSTRAINT ck_workshop_workflow_runs_status
  CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'blocked', 'cancelled'));

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_workflow_runs_project_id
  ON workshop_workflow_runs (project_id, id);

CREATE INDEX IF NOT EXISTS idx_workshop_workflow_runs_project_created
  ON workshop_workflow_runs (project_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_workshop_workflow_runs_workflow_created
  ON workshop_workflow_runs (project_id, workflow_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_workshop_workflow_runs_status_created
  ON workshop_workflow_runs (status, created_at DESC, id);
