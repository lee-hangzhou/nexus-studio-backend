-- P末：画布行级表（需在 drop 遗留表之后、应用上线前执行）
-- psql "$DATABASE_URL" -f db/migrations/20250603_create_canvas.sql

CREATE TABLE IF NOT EXISTS canvas_project_meta (
  project_id       BIGINT PRIMARY KEY REFERENCES projects(id),
  revision         BIGINT NOT NULL DEFAULT 0,
  node_count       INT NOT NULL DEFAULT 0,
  edge_count       INT NOT NULL DEFAULT 0,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS canvas_nodes (
  id               UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL REFERENCES projects(id),
  kind             VARCHAR(16) NOT NULL,
  position_x       DOUBLE PRECISION NOT NULL,
  position_y       DOUBLE PRECISION NOT NULL,
  title            VARCHAR(512) NOT NULL DEFAULT '',
  input_prompt     TEXT NOT NULL DEFAULT '',
  output_text      TEXT NOT NULL DEFAULT '',
  status           VARCHAR(16) NOT NULL DEFAULT 'idle',
  model_id         VARCHAR(128),
  ratio            VARCHAR(16),
  duration_sec     INT,
  resolution       VARCHAR(16),
  task_id          BIGINT,
  output_asset_ids JSONB,
  error_message    TEXT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_canvas_nodes_project_alive
  ON canvas_nodes (project_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_nodes_project_status
  ON canvas_nodes (project_id, status) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS canvas_edges (
  id               UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL REFERENCES projects(id),
  source_node_id   UUID NOT NULL REFERENCES canvas_nodes(id),
  target_node_id   UUID NOT NULL REFERENCES canvas_nodes(id),
  source_port      VARCHAR(64) NOT NULL,
  target_port      VARCHAR(64) NOT NULL,
  edge_type        VARCHAR(32) NOT NULL DEFAULT 'dependency',
  metadata         JSONB NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
  deleted_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_canvas_edges_project_alive
  ON canvas_edges (project_id) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_canvas_edges_source
  ON canvas_edges (project_id, source_node_id) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS canvas_messages (
  id               BIGSERIAL PRIMARY KEY,
  project_id       BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  role             SMALLINT NOT NULL,
  content          TEXT NOT NULL,
  metadata         JSONB NOT NULL DEFAULT '{}',
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_canvas_messages_project ON canvas_messages (project_id, created_at);

CREATE TABLE IF NOT EXISTS canvas_operations (
  op_id            UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  turn_id          VARCHAR(64),
  op_type          VARCHAR(32) NOT NULL,
  payload          JSONB NOT NULL,
  status           VARCHAR(16) NOT NULL,
  revision_before  BIGINT,
  revision_after   BIGINT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_canvas_operations_project ON canvas_operations (project_id, created_at DESC);

DROP TRIGGER IF EXISTS trg_canvas_project_meta_updated_at ON canvas_project_meta;
CREATE TRIGGER trg_canvas_project_meta_updated_at
BEFORE UPDATE ON canvas_project_meta
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

DROP TRIGGER IF EXISTS trg_canvas_nodes_updated_at ON canvas_nodes;
CREATE TRIGGER trg_canvas_nodes_updated_at
BEFORE UPDATE ON canvas_nodes
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();
