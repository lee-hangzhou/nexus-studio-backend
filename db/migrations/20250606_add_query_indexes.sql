-- Query indexes for project lists, asset lists, and canvas generation callbacks.
-- Production-safe invocation:
--   psql "$DATABASE_URL" -f db/migrations/20250606_add_query_indexes.sql

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_projects_owner_updated
  ON projects (owner_user_id, updated_at DESC, id DESC);

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_assets_user_created
  ON assets (user_id, created_at DESC, id DESC)
  WHERE deleted_at IS NULL;

CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_canvas_nodes_task_alive
  ON canvas_nodes (task_id)
  WHERE deleted_at IS NULL AND task_id IS NOT NULL;
