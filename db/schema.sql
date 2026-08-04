-- Nexus Studio 权威 DDL（绿场）。
-- 本地建库 / 重置：make db-schema 或 make db-reset

CREATE EXTENSION IF NOT EXISTS vector;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ── users ──────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
  id BIGSERIAL PRIMARY KEY,
  username VARCHAR(50) NOT NULL,
  email VARCHAR(255) NOT NULL,
  hashed_password VARCHAR(255) NOT NULL,
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_users_username UNIQUE (username),
  CONSTRAINT uk_users_email UNIQUE (email)
);

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ── projects / episodes ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS projects (
  id BIGSERIAL PRIMARY KEY,
  owner_user_id VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  status INTEGER NOT NULL,
  cover_asset_id BIGINT,
  tone_constraint JSONB NOT NULL,
  style_constraint JSONB NOT NULL,
  config JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_projects_owner_status
  ON projects (owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_projects_owner_updated
  ON projects (owner_user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_projects_cover_asset
  ON projects (cover_asset_id)
  WHERE cover_asset_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_projects_updated_at ON projects;
CREATE TRIGGER trg_projects_updated_at
BEFORE UPDATE ON projects
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS project_episodes (
  id BIGSERIAL PRIMARY KEY,
  project_id BIGINT NOT NULL,
  creator_id BIGINT NOT NULL,
  episode_no INTEGER NOT NULL,
  name VARCHAR(255) NOT NULL,
  cover_asset_id BIGINT,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 软删后允许复用同一 episode_no
CREATE UNIQUE INDEX IF NOT EXISTS uk_project_episodes_project_no_alive
  ON project_episodes (project_id, episode_no)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_episodes_project_updated_alive
  ON project_episodes (project_id, updated_at DESC, id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_episodes_creator_alive
  ON project_episodes (creator_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_project_episodes_cover_asset
  ON project_episodes (cover_asset_id)
  WHERE cover_asset_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_project_episodes_updated_at ON project_episodes;
CREATE TRIGGER trg_project_episodes_updated_at
BEFORE UPDATE ON project_episodes
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ── generate_task ──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS generate_task (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  union_task_id BIGINT,
  kind VARCHAR(10) NOT NULL,
  status SMALLINT NOT NULL DEFAULT 1,
  prompt TEXT NOT NULL,
  model_id VARCHAR(128) NOT NULL,
  voice_id VARCHAR(128),
  ratio VARCHAR(10),
  resolution VARCHAR(10),
  max_images SMALLINT DEFAULT 1,
  duration SMALLINT,
  reference_mode SMALLINT,
  ref_asset_ids JSONB,
  result_keys JSONB,
  result_asset_ids JSONB,
  error_code INTEGER,
  error_message TEXT,
  is_favorited BOOLEAN NOT NULL DEFAULT FALSE,
  callback_sent BOOLEAN NOT NULL DEFAULT FALSE,
  deleted_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_generate_task_user_created
  ON generate_task (user_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_generate_task_active_user_created
  ON generate_task (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_generate_task_user_status
  ON generate_task (user_id, status, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_generate_task_union_id
  ON generate_task (union_task_id);

DROP TRIGGER IF EXISTS trg_generate_task_updated_at ON generate_task;
CREATE TRIGGER trg_generate_task_updated_at
BEFORE UPDATE ON generate_task
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ── assets ─────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS assets (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  project_id BIGINT,
  storage_key VARCHAR(1024) NOT NULL,
  filename VARCHAR(512) NOT NULL DEFAULT '',
  mime_type VARCHAR(128) NOT NULL,
  asset_type VARCHAR(16) NOT NULL,
  source_type VARCHAR(32) NOT NULL,
  source_id VARCHAR(128),
  metadata JSONB NOT NULL DEFAULT '{}', -- 可选 file_sha256 等 dedup 字段
  status VARCHAR(16) NOT NULL DEFAULT 'ready',
  favorite BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_assets_user_type
  ON assets (user_id, asset_type)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_user_created
  ON assets (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_project_type
  ON assets (project_id, asset_type)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_source
  ON assets (source_type, source_id)
  WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS trg_assets_updated_at ON assets;
CREATE TRIGGER trg_assets_updated_at
BEFORE UPDATE ON assets
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- ── chat ───────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS chat_conversations (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  title VARCHAR(255) NOT NULL,
  default_model VARCHAR(128) NOT NULL,
  status INTEGER NOT NULL,
  kind VARCHAR(32) NOT NULL DEFAULT 'chat',
  active_turn_id VARCHAR(64),
  active_turn_started_at TIMESTAMPTZ,
  selected_expert_key VARCHAR(128),
  upgrade_invite_declined BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_conversations_id_user
  ON chat_conversations (id, user_id);

CREATE INDEX IF NOT EXISTS idx_chat_conversations_user_updated
  ON chat_conversations (user_id, updated_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_conversations_user_prompt_assistant
  ON chat_conversations (user_id)
  WHERE kind = 'prompt_assistant' AND status = 1;

DROP TRIGGER IF EXISTS trg_chat_conversations_updated_at ON chat_conversations;
CREATE TRIGGER trg_chat_conversations_updated_at
BEFORE UPDATE ON chat_conversations
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS chat_messages (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role INTEGER NOT NULL,
  content TEXT NOT NULL,
  payload JSONB NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
  ON chat_messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS chat_attachments (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  message_id BIGINT,
  asset_id BIGINT,
  user_id BIGINT NOT NULL,
  filename VARCHAR(512) NOT NULL,
  mime_type VARCHAR(128) NOT NULL,
  storage_key VARCHAR(1024) NOT NULL,
  size BIGINT NOT NULL,
  status INTEGER NOT NULL DEFAULT 1,
  is_attached BOOLEAN NOT NULL DEFAULT TRUE,
  detached_at TIMESTAMPTZ,
  parse_error TEXT,
  file_sha256 VARCHAR(64),
  source VARCHAR(32) NOT NULL DEFAULT 'user_upload',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_attachments_conversation
  ON chat_attachments (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_active
  ON chat_attachments (conversation_id, is_attached, status);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_message_id
  ON chat_attachments (message_id);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_asset_id
  ON chat_attachments (asset_id);

-- ── canvas（按集）──────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS canvas_episode_meta (
  episode_id BIGINT PRIMARY KEY,
  node_count INTEGER NOT NULL DEFAULT 0,
  edge_count INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_nodes (
  id UUID PRIMARY KEY,
  episode_id BIGINT NOT NULL,
  kind VARCHAR(16) NOT NULL,
  revision BIGINT NOT NULL DEFAULT 1,
  position_x DOUBLE PRECISION NOT NULL,
  position_y DOUBLE PRECISION NOT NULL,
  width DOUBLE PRECISION,
  height DOUBLE PRECISION,
  data JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS canvas_edges (
  id UUID PRIMARY KEY,
  episode_id BIGINT NOT NULL,
  revision BIGINT NOT NULL DEFAULT 1,
  source_node_id UUID NOT NULL,
  target_node_id UUID NOT NULL,
  source_port VARCHAR(64) NOT NULL,
  target_port VARCHAR(64) NOT NULL,
  edge_type VARCHAR(32) NOT NULL DEFAULT 'dependency',
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS canvas_sessions (
  id BIGSERIAL PRIMARY KEY,
  episode_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  title VARCHAR(255) NOT NULL,
  status SMALLINT NOT NULL,
  is_default BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_messages (
  id BIGSERIAL PRIMARY KEY,
  episode_id BIGINT NOT NULL,
  session_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  role SMALLINT NOT NULL,
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_operations (
  op_id UUID PRIMARY KEY,
  episode_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  turn_id VARCHAR(64),
  op_type VARCHAR(32) NOT NULL,
  payload JSONB NOT NULL,
  status VARCHAR(16) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_canvas_nodes_episode_alive
  ON canvas_nodes (episode_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_nodes_episode_status_alive
  ON canvas_nodes (episode_id, (data->>'status'))
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_nodes_task_alive
  ON canvas_nodes (episode_id, ((data->>'generate_task_id')::bigint))
  WHERE deleted_at IS NULL AND data->>'generate_task_id' IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_canvas_edges_episode_alive
  ON canvas_edges (episode_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_edges_source_alive
  ON canvas_edges (episode_id, source_node_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_edges_target_alive
  ON canvas_edges (episode_id, target_node_id)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_canvas_sessions_episode_user_updated
  ON canvas_sessions (episode_id, user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_canvas_sessions_episode_user_status
  ON canvas_sessions (episode_id, user_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uk_canvas_sessions_default_alive
  ON canvas_sessions (episode_id, user_id)
  WHERE status = 1 AND is_default = TRUE;

CREATE INDEX IF NOT EXISTS idx_canvas_messages_session
  ON canvas_messages (session_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_canvas_messages_episode
  ON canvas_messages (episode_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_canvas_operations_episode
  ON canvas_operations (episode_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_canvas_sessions_updated_at ON canvas_sessions;
CREATE TRIGGER trg_canvas_sessions_updated_at
BEFORE UPDATE ON canvas_sessions
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_canvas_episode_meta_updated_at ON canvas_episode_meta;
CREATE TRIGGER trg_canvas_episode_meta_updated_at
BEFORE UPDATE ON canvas_episode_meta
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_canvas_nodes_updated_at ON canvas_nodes;
CREATE TRIGGER trg_canvas_nodes_updated_at
BEFORE UPDATE ON canvas_nodes
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS user_skill_entries (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  surface VARCHAR(16) NOT NULL,
  scope VARCHAR(16) NOT NULL,
  biz_key BIGINT NOT NULL,
  path VARCHAR(1024) NOT NULL,
  is_dir BOOLEAN NOT NULL,
  content TEXT NULL,
  name VARCHAR(256) NULL,
  description VARCHAR(512) NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  revision BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE (surface, scope, biz_key, path)
);

CREATE INDEX IF NOT EXISTS idx_user_skill_entries_surface_scope_biz_key
  ON user_skill_entries (surface, scope, biz_key);

DROP TRIGGER IF EXISTS trg_user_skill_entries_updated_at ON user_skill_entries;
CREATE TRIGGER trg_user_skill_entries_updated_at
BEFORE UPDATE ON user_skill_entries
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_projects (
  id VARCHAR(64) PRIMARY KEY,
  user_id BIGINT NOT NULL,
  name VARCHAR(255) NOT NULL,
  group_chat_id BIGINT NOT NULL,
  brief JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_projects_group_chat UNIQUE (group_chat_id),
  CONSTRAINT fk_workshop_projects_group_chat_user
    FOREIGN KEY (group_chat_id, user_id)
    REFERENCES chat_conversations(id, user_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_chat_conversations_id_user
  ON chat_conversations (id, user_id);

ALTER TABLE workshop_projects
  DROP CONSTRAINT IF EXISTS fk_workshop_projects_group_chat;
ALTER TABLE workshop_projects
  DROP CONSTRAINT IF EXISTS fk_workshop_projects_group_chat_user;
ALTER TABLE workshop_projects
  ADD CONSTRAINT fk_workshop_projects_group_chat_user
  FOREIGN KEY (group_chat_id, user_id)
  REFERENCES chat_conversations(id, user_id) ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_workshop_projects_user_updated
  ON workshop_projects (user_id, updated_at DESC, id DESC);

DROP TRIGGER IF EXISTS trg_workshop_projects_updated_at ON workshop_projects;
CREATE TRIGGER trg_workshop_projects_updated_at
BEFORE UPDATE ON workshop_projects
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_experts (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  preset_key VARCHAR(64),
  source_preset_key VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_experts_kind CHECK (kind IN ('advisor', 'executor')),
  CONSTRAINT fk_workshop_experts_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_experts_project_preset
  ON workshop_experts (project_id, preset_key)
  WHERE preset_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_workshop_experts_project_created
  ON workshop_experts (project_id, created_at, id);

DROP TRIGGER IF EXISTS trg_workshop_experts_updated_at ON workshop_experts;
CREATE TRIGGER trg_workshop_experts_updated_at
BEFORE UPDATE ON workshop_experts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_task_proposals (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  goals JSONB NOT NULL,
  required_artifacts JSONB NOT NULL DEFAULT '[]',
  status VARCHAR(16) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_task_proposals_status
    CHECK (status IN ('pending', 'confirmed', 'declined')),
  CONSTRAINT fk_workshop_task_proposals_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE
);

ALTER TABLE workshop_task_proposals
  ADD COLUMN IF NOT EXISTS required_artifacts JSONB NOT NULL DEFAULT '[]';

CREATE INDEX IF NOT EXISTS idx_workshop_task_proposals_project_status
  ON workshop_task_proposals (project_id, status, created_at);

DROP TRIGGER IF EXISTS trg_workshop_task_proposals_updated_at
  ON workshop_task_proposals;
CREATE TRIGGER trg_workshop_task_proposals_updated_at
BEFORE UPDATE ON workshop_task_proposals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_expert_proposals (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  kind VARCHAR(16) NOT NULL,
  source_preset_key VARCHAR(64),
  status VARCHAR(16) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_expert_proposals_kind
    CHECK (kind IN ('advisor', 'executor')),
  CONSTRAINT ck_workshop_expert_proposals_status
    CHECK (status IN ('pending', 'confirmed', 'declined')),
  CONSTRAINT fk_workshop_expert_proposals_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workshop_expert_proposals_project_status
  ON workshop_expert_proposals (project_id, status, created_at);

DROP TRIGGER IF EXISTS trg_workshop_expert_proposals_updated_at
  ON workshop_expert_proposals;
CREATE TRIGGER trg_workshop_expert_proposals_updated_at
BEFORE UPDATE ON workshop_expert_proposals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_tasks (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  goals JSONB NOT NULL,
  required_artifacts JSONB NOT NULL DEFAULT '[]',
  status VARCHAR(32) NOT NULL,
  schedule_id VARCHAR(64),
  schedule_authorized BOOLEAN NOT NULL DEFAULT FALSE,
  external_auth JSONB NOT NULL DEFAULT '[]',
  revision BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_tasks_status CHECK (
    status IN (
      'aligning', 'awaiting_go', 'authorized', 'executing',
      'blocked', 're_aligning', 'reviewing', 'done', 'failed', 'cancelled'
    )
  ),
  CONSTRAINT ck_workshop_tasks_revision CHECK (revision >= 1),
  CONSTRAINT fk_workshop_tasks_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE
);

ALTER TABLE workshop_tasks
  ADD COLUMN IF NOT EXISTS required_artifacts JSONB NOT NULL DEFAULT '[]';

ALTER TABLE workshop_tasks DROP CONSTRAINT IF EXISTS ck_workshop_tasks_status;
ALTER TABLE workshop_tasks ADD CONSTRAINT ck_workshop_tasks_status CHECK (
  status IN (
    'aligning', 'awaiting_go', 'authorized', 'executing',
    'blocked', 're_aligning', 'reviewing', 'done', 'failed', 'cancelled'
  )
);

CREATE INDEX IF NOT EXISTS idx_workshop_tasks_project_status_updated
  ON workshop_tasks (project_id, status, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workshop_tasks_project_created
  ON workshop_tasks (project_id, created_at, id);

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_tasks_project_id
  ON workshop_tasks (project_id, id);

DROP TRIGGER IF EXISTS trg_workshop_tasks_updated_at ON workshop_tasks;
CREATE TRIGGER trg_workshop_tasks_updated_at
BEFORE UPDATE ON workshop_tasks
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_task_experts (
  id BIGSERIAL PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  expert_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_task_experts_task_expert UNIQUE (task_id, expert_id),
  CONSTRAINT fk_workshop_task_experts_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_task_experts_task
    FOREIGN KEY (task_id) REFERENCES workshop_tasks(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_task_experts_expert
    FOREIGN KEY (expert_id) REFERENCES workshop_experts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workshop_task_experts_project_task
  ON workshop_task_experts (project_id, task_id);

CREATE TABLE IF NOT EXISTS workshop_workflows (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  name VARCHAR(255) NOT NULL,
  steps JSONB NOT NULL,
  status VARCHAR(16) NOT NULL,
  source VARCHAR(16) NOT NULL DEFAULT 'user',
  revision BIGINT NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_workflows_status CHECK (status IN ('draft', 'saved')),
  CONSTRAINT ck_workshop_workflows_source CHECK (source IN ('agent', 'user')),
  CONSTRAINT ck_workshop_workflows_revision CHECK (revision >= 1),
  CONSTRAINT fk_workshop_workflows_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE
);

ALTER TABLE workshop_workflows
  ADD COLUMN IF NOT EXISTS source VARCHAR(16) NOT NULL DEFAULT 'user';
ALTER TABLE workshop_workflows DROP CONSTRAINT IF EXISTS ck_workshop_workflows_source;
ALTER TABLE workshop_workflows
  ADD CONSTRAINT ck_workshop_workflows_source CHECK (source IN ('agent', 'user'));

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_workflows_project_id
  ON workshop_workflows (project_id, id);

CREATE INDEX IF NOT EXISTS idx_workshop_workflows_project_status_updated
  ON workshop_workflows (project_id, status, updated_at DESC, id DESC);

DROP TRIGGER IF EXISTS trg_workshop_workflows_updated_at ON workshop_workflows;
CREATE TRIGGER trg_workshop_workflows_updated_at
BEFORE UPDATE ON workshop_workflows
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_schedules (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  workflow_id VARCHAR(64) NOT NULL,
  cron VARCHAR(128) NOT NULL,
  timezone VARCHAR(64) NOT NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  authorized_at TIMESTAMPTZ NOT NULL,
  authorized_external_capabilities JSONB NOT NULL DEFAULT '[]',
  next_run_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_workshop_schedules_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_schedules_workflow_project
    FOREIGN KEY (project_id, workflow_id)
    REFERENCES workshop_workflows(project_id, id) ON DELETE CASCADE
);

ALTER TABLE workshop_schedules
  ADD COLUMN IF NOT EXISTS authorized_external_capabilities JSONB NOT NULL DEFAULT '[]';

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_workflows_project_id
  ON workshop_workflows (project_id, id);

ALTER TABLE workshop_schedules
  DROP CONSTRAINT IF EXISTS fk_workshop_schedules_workflow;
ALTER TABLE workshop_schedules
  DROP CONSTRAINT IF EXISTS fk_workshop_schedules_workflow_project;
ALTER TABLE workshop_schedules
  ADD CONSTRAINT fk_workshop_schedules_workflow_project
  FOREIGN KEY (project_id, workflow_id)
  REFERENCES workshop_workflows(project_id, id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_schedules_project_id
  ON workshop_schedules (project_id, id);

ALTER TABLE workshop_tasks
  DROP CONSTRAINT IF EXISTS fk_workshop_tasks_schedule_project;
ALTER TABLE workshop_tasks
  ADD CONSTRAINT fk_workshop_tasks_schedule_project
  FOREIGN KEY (project_id, schedule_id)
  REFERENCES workshop_schedules(project_id, id)
  ON DELETE RESTRICT;

CREATE INDEX IF NOT EXISTS idx_workshop_schedules_enabled_next_run
  ON workshop_schedules (enabled, next_run_at)
  WHERE enabled = TRUE;
CREATE INDEX IF NOT EXISTS idx_workshop_schedules_project_updated
  ON workshop_schedules (project_id, updated_at DESC, id DESC);

DROP TRIGGER IF EXISTS trg_workshop_schedules_updated_at ON workshop_schedules;
CREATE TRIGGER trg_workshop_schedules_updated_at
BEFORE UPDATE ON workshop_schedules
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_artifacts (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64),
  name VARCHAR(512) NOT NULL,
  storage_type VARCHAR(16) NOT NULL,
  storage_key VARCHAR(1024) NOT NULL,
  size_bytes BIGINT,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT ck_workshop_artifacts_storage_type
    CHECK (storage_type IN ('db', 'oss', 'filesystem')),
  CONSTRAINT ck_workshop_artifacts_size_bytes
    CHECK (size_bytes IS NULL OR size_bytes > 0),
  CONSTRAINT fk_workshop_artifacts_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_artifacts_task_project
    FOREIGN KEY (project_id, task_id)
    REFERENCES workshop_tasks(project_id, id) ON DELETE CASCADE
);

ALTER TABLE workshop_artifacts
  ADD COLUMN IF NOT EXISTS size_bytes BIGINT;
ALTER TABLE workshop_artifacts DROP CONSTRAINT IF EXISTS ck_workshop_artifacts_size_bytes;
ALTER TABLE workshop_artifacts
  ADD CONSTRAINT ck_workshop_artifacts_size_bytes
  CHECK (size_bytes IS NULL OR size_bytes > 0);

ALTER TABLE workshop_artifacts
  DROP CONSTRAINT IF EXISTS fk_workshop_artifacts_task;
ALTER TABLE workshop_artifacts
  DROP CONSTRAINT IF EXISTS fk_workshop_artifacts_task_project;
ALTER TABLE workshop_artifacts
  ADD CONSTRAINT fk_workshop_artifacts_task_project
  FOREIGN KEY (project_id, task_id)
  REFERENCES workshop_tasks(project_id, id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uk_workshop_artifacts_task_name
  ON workshop_artifacts (task_id, name)
  WHERE task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_workshop_artifacts_project_created
  ON workshop_artifacts (project_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_workshop_artifacts_task_created
  ON workshop_artifacts (task_id, created_at DESC, id DESC)
  WHERE task_id IS NOT NULL;

DROP TRIGGER IF EXISTS trg_workshop_artifacts_updated_at ON workshop_artifacts;
CREATE TRIGGER trg_workshop_artifacts_updated_at
BEFORE UPDATE ON workshop_artifacts
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS workshop_events (
  id BIGSERIAL PRIMARY KEY,
  event_key VARCHAR(64) NOT NULL,
  project_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64),
  kind VARCHAR(64) NOT NULL,
  payload JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_events_event_key UNIQUE (event_key),
  CONSTRAINT fk_workshop_events_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_events_task_project
    FOREIGN KEY (project_id, task_id)
    REFERENCES workshop_tasks(project_id, id) ON DELETE CASCADE
);

ALTER TABLE workshop_events
  DROP CONSTRAINT IF EXISTS fk_workshop_events_task;
ALTER TABLE workshop_events
  DROP CONSTRAINT IF EXISTS fk_workshop_events_task_project;
ALTER TABLE workshop_events
  ADD CONSTRAINT fk_workshop_events_task_project
  FOREIGN KEY (project_id, task_id)
  REFERENCES workshop_tasks(project_id, id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_workshop_events_project_created
  ON workshop_events (project_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_workshop_events_task_created
  ON workshop_events (task_id, created_at, id)
  WHERE task_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS workshop_room_members (
  id BIGSERIAL PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  expert_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_room_members_project_expert UNIQUE (project_id, expert_id),
  CONSTRAINT fk_workshop_room_members_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_room_members_expert
    FOREIGN KEY (expert_id) REFERENCES workshop_experts(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workshop_room_members_project
  ON workshop_room_members (project_id, created_at, id);

CREATE TABLE IF NOT EXISTS workshop_task_capability_uses (
  id BIGSERIAL PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  capability VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_task_capability_uses_task_cap UNIQUE (task_id, capability),
  CONSTRAINT fk_workshop_task_capability_uses_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_task_capability_uses_task
    FOREIGN KEY (task_id) REFERENCES workshop_tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workshop_task_capability_uses_task
  ON workshop_task_capability_uses (task_id, created_at, id);

CREATE TABLE IF NOT EXISTS workshop_schedule_runs (
  id VARCHAR(64) PRIMARY KEY,
  project_id VARCHAR(64) NOT NULL,
  schedule_id VARCHAR(64) NOT NULL,
  trigger_key VARCHAR(128) NOT NULL,
  task_id VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_workshop_schedule_runs_schedule_trigger
    UNIQUE (schedule_id, trigger_key),
  CONSTRAINT fk_workshop_schedule_runs_project
    FOREIGN KEY (project_id) REFERENCES workshop_projects(id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_schedule_runs_schedule_project
    FOREIGN KEY (project_id, schedule_id)
    REFERENCES workshop_schedules(project_id, id) ON DELETE CASCADE,
  CONSTRAINT fk_workshop_schedule_runs_task_project
    FOREIGN KEY (project_id, task_id)
    REFERENCES workshop_tasks(project_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_workshop_schedule_runs_project_created
  ON workshop_schedule_runs (project_id, created_at, id);

ALTER TABLE chat_conversations
  ADD COLUMN IF NOT EXISTS selected_expert_key VARCHAR(128);

ALTER TABLE chat_conversations
  ADD COLUMN IF NOT EXISTS upgrade_invite_declined BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS chat_upgrade_invite_proposals (
  id BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  user_id BIGINT NOT NULL,
  expert_keys JSONB NOT NULL,
  primary_expert_key VARCHAR(128) NOT NULL,
  rationale TEXT NOT NULL,
  host_narration TEXT NOT NULL DEFAULT '',
  source_user_text TEXT NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  turn_id VARCHAR(64),
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_upgrade_invite_proposals_conversation_user
    FOREIGN KEY (conversation_id, user_id)
    REFERENCES chat_conversations(id, user_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_upgrade_invite_proposals_conversation_status
  ON chat_upgrade_invite_proposals (conversation_id, status);

ALTER TABLE chat_upgrade_invite_proposals
  ADD COLUMN IF NOT EXISTS host_narration TEXT NOT NULL DEFAULT '';

DROP TRIGGER IF EXISTS trg_chat_upgrade_invite_proposals_updated_at ON chat_upgrade_invite_proposals;
CREATE TRIGGER trg_chat_upgrade_invite_proposals_updated_at
BEFORE UPDATE ON chat_upgrade_invite_proposals
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- billing: Creem one-time credit packs

CREATE TABLE IF NOT EXISTS billing_orders (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  pack_key VARCHAR(32) NOT NULL,
  credits INTEGER NOT NULL,
  price_usd_cents INTEGER NOT NULL,
  status VARCHAR(16) NOT NULL DEFAULT 'pending',
  request_id VARCHAR(128) NOT NULL,
  creem_product_id VARCHAR(128) NOT NULL,
  creem_checkout_id VARCHAR(128),
  creem_order_id VARCHAR(128),
  paid_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_billing_orders_request_id UNIQUE (request_id),
  CONSTRAINT ck_billing_orders_credits_positive CHECK (credits > 0),
  CONSTRAINT ck_billing_orders_price_positive CHECK (price_usd_cents > 0),
  CONSTRAINT ck_billing_orders_status CHECK (status IN ('pending', 'paid'))
);

DROP TRIGGER IF EXISTS trg_billing_orders_updated_at ON billing_orders;
CREATE TRIGGER trg_billing_orders_updated_at
BEFORE UPDATE ON billing_orders
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE INDEX IF NOT EXISTS idx_billing_orders_user_status
  ON billing_orders (user_id, status);
CREATE INDEX IF NOT EXISTS idx_billing_orders_creem_checkout
  ON billing_orders (creem_checkout_id);

CREATE TABLE IF NOT EXISTS billing_credit_balances (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  balance BIGINT NOT NULL DEFAULT 0,
  version INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_billing_credit_balances_user UNIQUE (user_id),
  CONSTRAINT ck_billing_credit_balances_nonneg CHECK (balance >= 0)
);

DROP TRIGGER IF EXISTS trg_billing_credit_balances_updated_at ON billing_credit_balances;
CREATE TRIGGER trg_billing_credit_balances_updated_at
BEFORE UPDATE ON billing_credit_balances
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS billing_credit_ledger (
  id BIGSERIAL PRIMARY KEY,
  user_id BIGINT NOT NULL,
  order_id BIGINT NOT NULL,
  event_id VARCHAR(128) NOT NULL,
  delta INTEGER NOT NULL,
  balance_after BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_billing_credit_ledger_event UNIQUE (event_id),
  CONSTRAINT ck_billing_credit_ledger_delta_positive CHECK (delta > 0),
  CONSTRAINT fk_billing_credit_ledger_order
    FOREIGN KEY (order_id) REFERENCES billing_orders(id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_billing_credit_ledger_user_created
  ON billing_credit_ledger (user_id, created_at, id);

CREATE TABLE IF NOT EXISTS billing_webhook_events (
  id BIGSERIAL PRIMARY KEY,
  event_id VARCHAR(128) NOT NULL,
  event_type VARCHAR(64) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT uk_billing_webhook_events_event UNIQUE (event_id)
);

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

DROP TRIGGER IF EXISTS trg_workshop_workflow_runs_updated_at ON workshop_workflow_runs;
CREATE TRIGGER trg_workshop_workflow_runs_updated_at
BEFORE UPDATE ON workshop_workflow_runs
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
