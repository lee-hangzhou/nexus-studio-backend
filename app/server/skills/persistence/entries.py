from tortoise import fields

from app.server.persistence.model_base import BaseModel


class UserSkillEntries(BaseModel):
    """用户自定义技能条目表"""

    user_id = fields.BigIntField(null=False)
    surface = fields.CharField(max_length=16, null=False)
    scope = fields.CharField(max_length=16, null=False)
    biz_key = fields.BigIntField(null=False)
    path = fields.CharField(max_length=1024, null=False)
    is_dir = fields.BooleanField(null=False)
    content = fields.TextField(null=True)
    name = fields.CharField(max_length=256, null=True)
    description = fields.CharField(max_length=512, null=True)
    enabled = fields.BooleanField(null=False, default=True)
    revision = fields.BigIntField(null=False, default=1)

    class Meta(BaseModel.Meta):
        table = "user_skill_entries"
        abstract = False
        unique_together = (("surface", "scope", "biz_key", "path"),)
        indexes = [("surface", "scope", "biz_key")]
