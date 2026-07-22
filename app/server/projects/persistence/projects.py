from tortoise import fields

from app.server.persistence.model_base import BaseModel


class Projects(BaseModel):
    owner_user_id = fields.CharField(max_length=255, null=False)
    name = fields.CharField(max_length=255, null=False)
    status = fields.IntField(null=False)
    tone_constraint = fields.JSONField(null=False)
    style_constraint = fields.JSONField(null=False)
    config = fields.JSONField(null=False)

    class Meta(BaseModel.Meta):
        table = "projects"
        abstract = False
        indexes = [("owner_user_id", "status")]
