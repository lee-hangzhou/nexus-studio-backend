-- 统一系统资产 + Canvas 数据流字段
-- psql "$DATABASE_URL" -f db/migrations/20250604_canvas_assets_workflow.sql

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
CREATE INDEX IF NOT EXISTS idx_assets_project_type
  ON assets (project_id, asset_type) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_assets_source
  ON assets (source_type, source_id) WHERE deleted_at IS NULL;

DROP TRIGGER IF EXISTS trg_assets_updated_at ON assets;
CREATE TRIGGER trg_assets_updated_at
BEFORE UPDATE ON assets
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

ALTER TABLE chat_attachments ADD COLUMN IF NOT EXISTS asset_id BIGINT NULL;
CREATE INDEX IF NOT EXISTS idx_chat_attachments_asset_id ON chat_attachments (asset_id);

ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS ref_asset_ids JSONB NULL;
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS result_asset_ids JSONB NULL;

-- Canvas 最终字段直接由 db/schema.sql 创建；开发环境重建数据库，不维护旧字段兼容迁移。
