-- chat_attachments 增加 message_id 单列索引
-- 用于 message/list 批量按 message_id 加载附件（消除 N+1 后的 message_id__in 查询）
-- 运行方式: psql -d dream_drama -f scripts/migrate_chat_attachments_message_id.sql

CREATE INDEX IF NOT EXISTS idx_chat_attachments_message_id
    ON chat_attachments (message_id);
