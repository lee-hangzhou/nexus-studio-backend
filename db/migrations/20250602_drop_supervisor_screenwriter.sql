-- P末：拆除编剧 / Supervisor 遗留表（需在授权环境执行，执行前备份）
-- psql "$DATABASE_URL" -f db/migrations/20250602_drop_supervisor_screenwriter.sql

DROP TABLE IF EXISTS model_calls CASCADE;
DROP TABLE IF EXISTS agent_runs CASCADE;
DROP TABLE IF EXISTS artifact_dependencies CASCADE;
DROP TABLE IF EXISTS artifact_versions CASCADE;
DROP TABLE IF EXISTS artifacts CASCADE;
DROP TABLE IF EXISTS execution_progress CASCADE;
DROP TABLE IF EXISTS plans CASCADE;
DROP TABLE IF EXISTS runs CASCADE;
DROP TABLE IF EXISTS assets CASCADE;
DROP TABLE IF EXISTS session_messages CASCADE;
DROP TABLE IF EXISTS sessions CASCADE;

ALTER TABLE projects DROP COLUMN IF EXISTS current_session_id;
ALTER TABLE projects DROP COLUMN IF EXISTS current_plan_id;
