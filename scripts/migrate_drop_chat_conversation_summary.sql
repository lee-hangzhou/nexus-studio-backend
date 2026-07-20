-- Drop unused rolling-summary columns from chat_conversations.
ALTER TABLE chat_conversations
  DROP COLUMN IF EXISTS summary,
  DROP COLUMN IF EXISTS summary_until_message_id,
  DROP COLUMN IF EXISTS summary_updated_at;
