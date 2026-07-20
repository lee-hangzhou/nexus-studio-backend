-- Drop chat attachment retrieval / extract / index pipeline (project not yet live).

DROP TABLE IF EXISTS chat_attachment_chunks;

ALTER TABLE chat_attachments DROP COLUMN IF EXISTS text_extract_status;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS index_status;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS extracted_text_storage_key;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS extract_started_at;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS index_started_at;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS index_attempts;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS chunk_count;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS token_count;
ALTER TABLE chat_attachments DROP COLUMN IF EXISTS parser_version;
