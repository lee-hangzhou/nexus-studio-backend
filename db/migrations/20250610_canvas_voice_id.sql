-- canvas_nodes: TTS 音色；generate_task: TTS 提交参数
ALTER TABLE canvas_nodes ADD COLUMN IF NOT EXISTS voice_id VARCHAR(128);
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS voice_id VARCHAR(128);
