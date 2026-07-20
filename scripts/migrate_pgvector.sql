-- pgvector for assets embedding (chat attachment chunks removed — see migrate_drop_chat_retrieval_pipeline.sql)
-- Apply: ssh private, then psql -h 127.0.0.1 -U dream_drama -d dream_drama -v ON_ERROR_STOP=1 -f migrate_pgvector.sql

CREATE EXTENSION IF NOT EXISTS vector;

ALTER TABLE assets ADD COLUMN IF NOT EXISTS embedding vector(3072);

-- pgvector HNSW/IVFFlat 索引维度上限为 2000；3072 维暂用顺序扫描。
