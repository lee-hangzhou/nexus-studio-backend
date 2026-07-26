from tortoise import fields

from app.server.persistence.model_base import BaseModel


class ProjectEpisodes(BaseModel):
    project_id = fields.BigIntField(null=False)
    creator_id = fields.BigIntField(null=False)
    episode_no = fields.IntField(null=False)
    name = fields.CharField(max_length=255, null=False)
    cover_asset_id = fields.BigIntField(null=True)
    deleted_at = fields.DatetimeField(null=True)

    class Meta(BaseModel.Meta):
        table = "project_episodes"
        abstract = False
        indexes = [
            ("project_id", "episode_no", "id"),
            ("project_id", "updated_at", "id"),
            ("creator_id", "created_at", "id"),
            ("cover_asset_id",),
        ]
