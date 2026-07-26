-- Project -> episode -> Canvas migration. Associations remain code-owned.
-- Run with psql -v ON_ERROR_STOP=1 so any failed invariant rolls back the whole migration.

BEGIN;

SELECT pg_advisory_xact_lock(hashtext('20260725_project_episode_covers'));

LOCK TABLE projects IN SHARE ROW EXCLUSIVE MODE;

ALTER TABLE projects
  ADD COLUMN IF NOT EXISTS cover_asset_id BIGINT;

CREATE INDEX IF NOT EXISTS idx_projects_cover_asset
  ON projects (cover_asset_id)
  WHERE cover_asset_id IS NOT NULL;

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

LOCK TABLE project_episodes IN SHARE ROW EXCLUSIVE MODE;
LOCK TABLE canvas_nodes, canvas_edges, canvas_messages, canvas_operations
  IN ACCESS EXCLUSIVE MODE;

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

DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM projects AS project
    LEFT JOIN users AS owner ON owner.id::TEXT = project.owner_user_id
    WHERE owner.id IS NULL
  ) THEN
    RAISE EXCEPTION 'cannot create default episodes: a project owner does not resolve to users.id';
  END IF;
END
$$;

INSERT INTO project_episodes (
  project_id,
  creator_id,
  episode_no,
  name,
  cover_asset_id,
  created_at,
  updated_at
)
SELECT
  project.id,
  owner.id,
  1,
  '第 1 集',
  NULL,
  project.created_at,
  project.updated_at
FROM projects AS project
JOIN users AS owner ON owner.id::TEXT = project.owner_user_id
WHERE NOT EXISTS (
  SELECT 1
  FROM project_episodes AS episode
  WHERE episode.project_id = project.id
    AND episode.episode_no = 1
)
ON CONFLICT (project_id, episode_no) DO NOTHING;

-- Remove constraints left by the former project-scoped Canvas schema before
-- replacing its scope columns. This intentionally only touches Canvas tables.
DO $$
DECLARE
  constraint_row RECORD;
BEGIN
  FOR constraint_row IN
    SELECT
      namespace.nspname AS schema_name,
      relation.relname AS table_name,
      constraint_record.conname AS constraint_name
    FROM pg_constraint AS constraint_record
    JOIN pg_class AS relation ON relation.oid = constraint_record.conrelid
    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
    WHERE constraint_record.contype = 'f'
      AND namespace.nspname = current_schema()
      AND relation.relname IN (
        'canvas_project_meta',
        'canvas_episode_meta',
        'canvas_nodes',
        'canvas_edges',
        'canvas_messages',
        'canvas_operations'
      )
  LOOP
    EXECUTE format(
      'ALTER TABLE %I.%I DROP CONSTRAINT %I',
      constraint_row.schema_name,
      constraint_row.table_name,
      constraint_row.constraint_name
    );
  END LOOP;
END
$$;

CREATE TABLE IF NOT EXISTS canvas_episode_meta (
  episode_id  BIGINT PRIMARY KEY,
  revision    BIGINT NOT NULL DEFAULT 0,
  node_count  INTEGER NOT NULL DEFAULT 0,
  edge_count  INTEGER NOT NULL DEFAULT 0,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

DO $$
DECLARE
  orphan_count BIGINT;
BEGIN
  IF to_regclass(format('%I.canvas_project_meta', current_schema())) IS NOT NULL THEN
    EXECUTE $statement$
      SELECT count(*)
      FROM canvas_project_meta AS meta
      LEFT JOIN project_episodes AS episode
        ON episode.project_id = meta.project_id
       AND episode.episode_no = 1
      WHERE episode.id IS NULL
    $statement$
    INTO orphan_count;

    IF orphan_count > 0 THEN
      RAISE EXCEPTION 'cannot migrate canvas_project_meta: % rows have no default episode', orphan_count;
    END IF;

    EXECUTE $statement$
      INSERT INTO canvas_episode_meta (
        episode_id,
        revision,
        node_count,
        edge_count,
        updated_at
      )
      SELECT
        episode.id,
        meta.revision,
        meta.node_count,
        meta.edge_count,
        meta.updated_at
      FROM canvas_project_meta AS meta
      JOIN project_episodes AS episode
        ON episode.project_id = meta.project_id
       AND episode.episode_no = 1
      ON CONFLICT (episode_id) DO UPDATE
      SET revision = GREATEST(canvas_episode_meta.revision, EXCLUDED.revision),
          node_count = EXCLUDED.node_count,
          edge_count = EXCLUDED.edge_count,
          updated_at = GREATEST(canvas_episode_meta.updated_at, EXCLUDED.updated_at)
    $statement$;
  END IF;
END
$$;

DO $$
DECLARE
  canvas_table TEXT;
  orphan_count BIGINT;
  has_project_scope BOOLEAN;
  has_episode_scope BOOLEAN;
BEGIN
  FOREACH canvas_table IN ARRAY ARRAY[
    'canvas_nodes',
    'canvas_edges',
    'canvas_messages',
    'canvas_operations'
  ]
  LOOP
    IF to_regclass(format('%I.%I', current_schema(), canvas_table)) IS NULL THEN
      RAISE EXCEPTION 'required Canvas table % is missing', canvas_table;
    END IF;

    SELECT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = canvas_table
        AND column_name = 'project_id'
    )
    INTO has_project_scope;

    SELECT EXISTS (
      SELECT 1
      FROM information_schema.columns
      WHERE table_schema = current_schema()
        AND table_name = canvas_table
        AND column_name = 'episode_id'
    )
    INTO has_episode_scope;

    IF has_project_scope THEN
      IF NOT has_episode_scope THEN
        EXECUTE format('ALTER TABLE %I ADD COLUMN episode_id BIGINT', canvas_table);
      END IF;

      EXECUTE format(
        'UPDATE %I AS canvas_row '
        'SET episode_id = episode.id '
        'FROM project_episodes AS episode '
        'WHERE episode.project_id = canvas_row.project_id '
        'AND episode.episode_no = 1 '
        'AND canvas_row.episode_id IS NULL',
        canvas_table
      );

      EXECUTE format('SELECT count(*) FROM %I WHERE episode_id IS NULL', canvas_table)
      INTO orphan_count;

      IF orphan_count > 0 THEN
        RAISE EXCEPTION 'cannot migrate %: % rows have no default episode', canvas_table, orphan_count;
      END IF;

      EXECUTE format('ALTER TABLE %I ALTER COLUMN episode_id SET NOT NULL', canvas_table);
      EXECUTE format('ALTER TABLE %I DROP COLUMN project_id', canvas_table);
    ELSIF NOT has_episode_scope THEN
      RAISE EXCEPTION '% has neither project_id nor episode_id', canvas_table;
    END IF;
  END LOOP;
END
$$;

DROP INDEX IF EXISTS idx_canvas_nodes_project_alive;
DROP INDEX IF EXISTS idx_canvas_nodes_project_status;
DROP INDEX IF EXISTS idx_canvas_edges_project_alive;
DROP INDEX IF EXISTS idx_canvas_edges_source;
DROP INDEX IF EXISTS idx_canvas_edges_target;
DROP INDEX IF EXISTS idx_canvas_messages_project;
DROP INDEX IF EXISTS idx_canvas_operations_project;

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

INSERT INTO canvas_episode_meta (
  episode_id,
  revision,
  node_count,
  edge_count,
  updated_at
)
SELECT
  episode.id,
  0,
  (
    SELECT count(*)::INTEGER
    FROM canvas_nodes AS node
    WHERE node.episode_id = episode.id
      AND node.deleted_at IS NULL
  ),
  (
    SELECT count(*)::INTEGER
    FROM canvas_edges AS edge
    WHERE edge.episode_id = episode.id
      AND edge.deleted_at IS NULL
  ),
  episode.updated_at
FROM project_episodes AS episode
WHERE episode.deleted_at IS NULL
ON CONFLICT (episode_id) DO NOTHING;

DROP TABLE IF EXISTS canvas_project_meta;

DROP TRIGGER IF EXISTS trg_project_episodes_updated_at ON project_episodes;
CREATE TRIGGER trg_project_episodes_updated_at
BEFORE UPDATE ON project_episodes
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

COMMIT;
