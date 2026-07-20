-- Turn lock columns for streaming agent (chat_conversations)
ALTER TABLE chat_conversations
  ADD COLUMN IF NOT EXISTS active_turn_id VARCHAR(64) NULL,
  ADD COLUMN IF NOT EXISTS active_turn_started_at TIMESTAMPTZ NULL;

COMMENT ON COLUMN chat_conversations.active_turn_id IS '当前进行中的 stream turn UUID';
COMMENT ON COLUMN chat_conversations.active_turn_started_at IS '当前 turn 开始时间';
