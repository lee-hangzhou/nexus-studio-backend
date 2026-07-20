-- 创作页生成任务表
-- 运行方式: psql -d dream_drama -f scripts/migrate_generate_task.sql

CREATE TABLE IF NOT EXISTS generate_task (
    id             BIGSERIAL    PRIMARY KEY,
    user_id        BIGINT       NOT NULL,
    union_task_id  BIGINT       NULL,
    kind           VARCHAR(10)  NOT NULL,
    status         SMALLINT     NOT NULL DEFAULT 1,
    prompt         TEXT         NOT NULL,
    model_id       VARCHAR(128) NOT NULL,
    ratio          VARCHAR(10)  NULL,
    resolution     VARCHAR(10)  NULL,
    max_images     SMALLINT     NULL DEFAULT 1,
    duration       SMALLINT     NULL,
    reference_mode SMALLINT     NULL,
    ref_attachment_ids JSONB    NULL,
    result_keys    JSONB        NULL,
    error_code     INTEGER      NULL,
    error_message  TEXT         NULL,
    is_favorited   BOOLEAN      NOT NULL DEFAULT FALSE,
    callback_sent  BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMPTZ  NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 兼容已存在的表：补充引用素材列
ALTER TABLE generate_task ADD COLUMN IF NOT EXISTS ref_attachment_ids JSONB NULL;

-- 主翻页索引：匹配 status='all' 的默认 cursor 翻页（ORDER BY created_at DESC, id DESC）
CREATE INDEX IF NOT EXISTS idx_generate_task_user_created
    ON generate_task (user_id, created_at DESC, id DESC);

-- 状态过滤索引：匹配带 status 过滤的翻页（in_progress / success / failed）
CREATE INDEX IF NOT EXISTS idx_generate_task_user_status
    ON generate_task (user_id, status, created_at DESC, id DESC);

-- 回调路径查询：按 union_task_id 查本地记录
CREATE INDEX IF NOT EXISTS idx_generate_task_union_id
    ON generate_task (union_task_id);

-- updated_at 自动更新触发器
DROP TRIGGER IF EXISTS trg_generate_task_updated_at ON generate_task;
CREATE TRIGGER trg_generate_task_updated_at
BEFORE UPDATE ON generate_task
FOR EACH ROW
EXECUTE FUNCTION set_updated_at();

COMMENT ON TABLE generate_task IS '创作页生成任务，代理 union_lm 网关的图片/视频生成请求';
COMMENT ON COLUMN generate_task.status IS '1=pending 2=queued 3=waiting 4=running 5=success 6=failed 7=cancelled';
COMMENT ON COLUMN generate_task.result_keys IS '裸 TOS object key 数组，含宽高等元数据，加签后响应给前端';
COMMENT ON COLUMN generate_task.callback_sent IS '回调路径幂等标记，成功写入终态后置 true';
