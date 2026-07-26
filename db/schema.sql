CREATE EXTENSION IF NOT EXISTS vector;

CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = CURRENT_TIMESTAMP;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

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

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_username ON users (username);
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email);

COMMENT ON TABLE users IS '用户表，保存系统登录用户的认证基础信息';
COMMENT ON COLUMN users.id IS '用户自增主键';
COMMENT ON COLUMN users.username IS '用户名';
COMMENT ON COLUMN users.email IS '用户邮箱';
COMMENT ON COLUMN users.hashed_password IS '哈希后的登录密码';
COMMENT ON COLUMN users.is_active IS '用户是否处于启用状态';
COMMENT ON COLUMN users.created_at IS '创建时间';
COMMENT ON COLUMN users.updated_at IS '更新时间';

DROP TRIGGER IF EXISTS trg_users_updated_at ON users;
CREATE TRIGGER trg_users_updated_at
BEFORE UPDATE ON users
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

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

CREATE INDEX IF NOT EXISTS idx_projects_owner_status ON projects (owner_user_id, status);
CREATE INDEX IF NOT EXISTS idx_projects_owner_updated
  ON projects (owner_user_id, updated_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_projects_cover_asset
  ON projects (cover_asset_id)
  WHERE cover_asset_id IS NOT NULL;

COMMENT ON TABLE projects IS '短剧项目表，保存项目基本信息、全局约束和当前会话状态';
COMMENT ON COLUMN projects.id IS '项目自增主键';
COMMENT ON COLUMN projects.owner_user_id IS '项目所属用户标识';
COMMENT ON COLUMN projects.name IS '项目名称';
COMMENT ON COLUMN projects.status IS '项目状态枚举值';
COMMENT ON COLUMN projects.cover_asset_id IS '项目封面资产 ID，代码层关联 assets.id';
COMMENT ON COLUMN projects.tone_constraint IS '项目级基调约束，包含情绪和调性要求';
COMMENT ON COLUMN projects.style_constraint IS '项目级视觉风格约束';
COMMENT ON COLUMN projects.config IS '项目配置，包含集数、题材和生产参数';
COMMENT ON COLUMN projects.created_at IS '创建时间';
COMMENT ON COLUMN projects.updated_at IS '更新时间';

DROP TRIGGER IF EXISTS trg_projects_updated_at ON projects;
CREATE TRIGGER trg_projects_updated_at
BEFORE UPDATE ON projects
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS project_episodes (
  id              BIGSERIAL PRIMARY KEY,
  project_id      BIGINT NOT NULL,
  creator_id      BIGINT NOT NULL,
  episode_no      INTEGER NOT NULL,
  name            VARCHAR(255) NOT NULL,
  cover_asset_id  BIGINT,
  deleted_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS uk_project_episodes_project_no
  ON project_episodes (project_id, episode_no);
CREATE INDEX IF NOT EXISTS idx_project_episodes_project_alive
  ON project_episodes (project_id, episode_no, id)
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


-- 创作页生成任务表
-- 运行方式: psql -d dream_drama -f scripts/migrate_generate_task.sql

CREATE TABLE IF NOT EXISTS generate_task (
    id             BIGSERIAL    PRIMARY KEY,
    user_id        BIGINT       NOT NULL,
    union_task_id  BIGINT       NULL,
    kind           VARCHAR(10)  NOT NULL,
    status         SMALLINT     NOT NULL DEFAULT 1,
    prompt         TEXT         NOT NULL,
    model_id       VARCHAR(128) NOT NULL,
    ratio          VARCHAR(10)  NULL,
    resolution     VARCHAR(10)  NULL,
    max_images     SMALLINT     NULL DEFAULT 1,
    duration       SMALLINT     NULL,
    reference_mode SMALLINT     NULL,
    ref_attachment_ids JSONB    NULL,
    result_keys    JSONB        NULL,
    error_code     INTEGER      NULL,
    error_message  TEXT         NULL,
    is_favorited   BOOLEAN      NOT NULL DEFAULT FALSE,
    callback_sent  BOOLEAN      NOT NULL DEFAULT FALSE,
    deleted_at     TIMESTAMPTZ  NULL,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 兼容已存在的表：补充引用素材列
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS ref_attachment_ids JSONB NULL;
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS ref_asset_ids JSONB NULL;
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS result_asset_ids JSONB NULL;
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS voice_id VARCHAR(128) NULL;
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMPTZ NULL;

CREATE TABLE IF NOT EXISTS assets (
  id               BIGSERIAL PRIMARY KEY,
  user_id          BIGINT NOT NULL,
  project_id       BIGINT NULL,
  storage_key      VARCHAR(1024) NOT NULL,
  filename         VARCHAR(512) NOT NULL DEFAULT '',
  mime_type        VARCHAR(128) NOT NULL,
  asset_type       VARCHAR(16) NOT NULL,
  source_type      VARCHAR(32) NOT NULL,
  source_id        VARCHAR(128),
  metadata         JSONB NOT NULL DEFAULT '{}',
  status           VARCHAR(16) NOT NULL DEFAULT 'ready',
  favorite         BOOLEAN NOT NULL DEFAULT FALSE,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_assets_user_type
  ON assets (user_id, asset_type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_user_created
  ON assets (user_id, created_at DESC, id DESC) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_project_type
  ON assets (project_id, asset_type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_source
  ON assets (source_type, source_id) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS trg_assets_updated_at ON assets;
CREATE TRIGGER trg_assets_updated_at
BEFORE UPDATE ON assets
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

-- 通用 Chat 会话、消息与附件

CREATE TABLE IF NOT EXISTS chat_conversations (
  id                     BIGSERIAL PRIMARY KEY,
  user_id                BIGINT NOT NULL,
  title                  VARCHAR(255) NOT NULL,
  default_model          VARCHAR(128) NOT NULL,
  status                 INTEGER NOT NULL,
  active_turn_id         VARCHAR(64),
  active_turn_started_at TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at             TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_conversations_user_updated
  ON chat_conversations (user_id, updated_at DESC);

DROP TRIGGER IF EXISTS trg_chat_conversations_updated_at ON chat_conversations;
CREATE TRIGGER trg_chat_conversations_updated_at
BEFORE UPDATE ON chat_conversations
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

CREATE TABLE IF NOT EXISTS chat_messages (
  id              BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  user_id         BIGINT NOT NULL,
  role            INTEGER NOT NULL,
  content         TEXT NOT NULL,
  payload         JSONB NOT NULL,
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_conversation
  ON chat_messages (conversation_id, created_at);

CREATE TABLE IF NOT EXISTS chat_attachments (
  id              BIGSERIAL PRIMARY KEY,
  conversation_id BIGINT NOT NULL,
  message_id      BIGINT,
  asset_id        BIGINT,
  user_id         BIGINT NOT NULL,
  filename        VARCHAR(512) NOT NULL,
  mime_type       VARCHAR(128) NOT NULL,
  storage_key     VARCHAR(1024) NOT NULL,
  size            BIGINT NOT NULL,
  status          INTEGER NOT NULL DEFAULT 1,
  is_attached     BOOLEAN NOT NULL DEFAULT TRUE,
  detached_at     TIMESTAMPTZ,
  parse_error     TEXT,
  file_sha256     VARCHAR(64),
  source          VARCHAR(32) NOT NULL DEFAULT 'user_upload',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE chat_attachments ADD COLUMN IF NOT EXISTS asset_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_chat_attachments_conversation
  ON chat_attachments (conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_active
  ON chat_attachments (conversation_id, is_attached, status);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_message_id
  ON chat_attachments (message_id);
CREATE INDEX IF NOT EXISTS idx_chat_attachments_asset_id
  ON chat_attachments (asset_id);

-- 主翻页索引：匹配 status='all' 的默认 cursor 翻页（ORDER BY created_at DESC, id DESC）
CREATE INDEX IF NOT EXISTS idx_generate_task_user_created
    ON generate_task (user_id, created_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS idx_generate_task_active_user_created
    ON generate_task (user_id, created_at DESC, id DESC)
    WHERE deleted_at IS NULL;

-- 状态过滤索引：匹配带 status 过滤的翻页（in_progress / success / failed）
CREATE INDEX IF NOT EXISTS idx_generate_task_user_status
    ON generate_task (user_id, status, created_at DESC, id DESC);

-- 回调路径查询：按 union_task_id 查本地记录
CREATE INDEX IF NOT EXISTS idx_generate_task_union_id
    ON generate_task (union_task_id);

-- updated_at 自动更新触发器
DROP TRIGGER IF EXISTS trg_generate_task_updated_at ON generate_task;
CREATE TRIGGER trg_generate_task_updated_at
BEFORE UPDATE ON generate_task
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE generate_task IS '创作页生成任务，代理 union_lm 网关的图片/视频生成请求';
COMMENT ON COLUMN generate_task.status IS '1=pending 2=queued 3=waiting 4=running 5=success 6=failed 7=cancelled';
COMMENT ON COLUMN generate_task.result_keys IS '裸 TOS object key 数组，含宽高等元数据，加签后响应给前端';
COMMENT ON COLUMN generate_task.callback_sent IS '回调路径幂等标记，成功写入终态后置 true';



CREATE TABLE IF NOT EXISTS canvas_episode_meta (
  episode_id  BIGINT PRIMARY KEY,
  revision    BIGINT NOT NULL DEFAULT 0,
  node_count  INTEGER NOT NULL DEFAULT 0,
  edge_count  INTEGER NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_nodes (
  id                UUID PRIMARY KEY,
  episode_id        BIGINT NOT NULL,
  kind              VARCHAR(16) NOT NULL,
  position_x        DOUBLE PRECISION NOT NULL,
  position_y        DOUBLE PRECISION NOT NULL,
  title             VARCHAR(512) NOT NULL DEFAULT '',
  input_prompt      TEXT NOT NULL DEFAULT '',
  output_text       TEXT NOT NULL DEFAULT '',
  status            VARCHAR(16) NOT NULL DEFAULT 'idle',
  model_id          VARCHAR(128),
  voice_id          VARCHAR(128),
  ratio             VARCHAR(16),
  duration_sec      INTEGER,
  resolution        VARCHAR(16),
  task_id           BIGINT,
  output_asset_ids  JSONB,
  error_message     TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at        TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS canvas_edges (
  id              UUID PRIMARY KEY,
  episode_id      BIGINT NOT NULL,
  source_node_id  UUID NOT NULL,
  target_node_id  UUID NOT NULL,
  source_port     VARCHAR(64) NOT NULL,
  target_port     VARCHAR(64) NOT NULL,
  edge_type       VARCHAR(32) NOT NULL DEFAULT 'dependency',
  metadata        JSONB NOT NULL DEFAULT '{}',
  created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS canvas_messages (
  id          BIGSERIAL PRIMARY KEY,
  episode_id  BIGINT NOT NULL,
  user_id     BIGINT NOT NULL,
  role        SMALLINT NOT NULL,
  content     TEXT NOT NULL,
  metadata    JSONB NOT NULL DEFAULT '{}',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_operations (
  op_id            UUID PRIMARY KEY,
  episode_id       BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  turn_id          VARCHAR(64),
  op_type          VARCHAR(32) NOT NULL,
  payload          JSONB NOT NULL,
  status           VARCHAR(16) NOT NULL,
  revision_before  BIGINT,
  revision_after   BIGINT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_canvas_nodes_episode_alive
  ON canvas_nodes (episode_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_nodes_episode_status_alive
  ON canvas_nodes (episode_id, status)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_nodes_task_alive
  ON canvas_nodes (task_id)
  WHERE deleted_at IS NULL AND task_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_canvas_edges_episode_alive
  ON canvas_edges (episode_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_edges_source_alive
  ON canvas_edges (episode_id, source_node_id)
  WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_edges_target_alive
  ON canvas_edges (episode_id, target_node_id)
  WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_canvas_messages_episode
  ON canvas_messages (episode_id, created_at, id);
CREATE INDEX IF NOT EXISTS idx_canvas_operations_episode
  ON canvas_operations (episode_id, created_at DESC);

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
