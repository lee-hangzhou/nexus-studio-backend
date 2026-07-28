from tortoise import fields

from app.server.persistence.model_base import BaseModel


class GenerateTask(BaseModel):
    """创作页生成任务记录，作为 union_lm 网关的代理层"""

    user_id = fields.BigIntField(null=False)
    union_task_id = fields.BigIntField(null=True)
    kind = fields.CharField(max_length=10, null=False)         # 'image' | 'video' | 'audio'
    status = fields.SmallIntField(null=False, default=1)       # 1-7，与网关状态对齐
    prompt = fields.TextField(null=False)
    model_id = fields.CharField(max_length=128, null=False)
    voice_id = fields.CharField(max_length=128, null=True)
    ratio = fields.CharField(max_length=10, null=True)         # '4:3' 等
    resolution = fields.CharField(max_length=10, null=True)    # '2k' | '4k'
    max_images = fields.SmallIntField(null=True, default=1)    # 图片生成张数
    duration = fields.SmallIntField(null=True)                 # 视频时长（秒）
    reference_mode = fields.SmallIntField(null=True)           # 仅视频，1-4
    ref_asset_ids = fields.JSONField(null=True)                 # 引用素材的统一资产 id 数组
    result_keys = fields.JSONField(null=True)                  # 裸 TOS key 数组（含宽高元数据）
    result_asset_ids = fields.JSONField(null=True)              # 统一资产 id 数组
    error_code = fields.IntField(null=True)
    error_message = fields.TextField(null=True)
    is_favorited = fields.BooleanField(null=False, default=False)
    callback_sent = fields.BooleanField(null=False, default=False)
    deleted_at = fields.DatetimeField(null=True)

    class Meta(BaseModel.Meta):
        table = "generate_task"
        abstract = False
        # 查询索引以 db/schema.sql 为准
        indexes = [
            ("user_id", "union_task_id"),
        ]
