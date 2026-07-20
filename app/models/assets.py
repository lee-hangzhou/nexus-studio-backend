from tortoise import fields

from app.models.base import BaseModel


class Assets(BaseModel):
    """System-wide asset registry.

    Assets are the durable resource truth for chat uploads, generate results,
    and canvas node outputs. Preview URLs are derived from storage_key at read
    time and are never stored here.
    """

    user_id = fields.BigIntField(null=False)
    project_id = fields.BigIntField(null=True)
    storage_key = fields.CharField(max_length=1024, null=False)
    filename = fields.CharField(max_length=512, null=False, default="")
    mime_type = fields.CharField(max_length=128, null=False)
    asset_type = fields.CharField(max_length=16, null=False)
    source_type = fields.CharField(max_length=32, null=False)
    source_id = fields.CharField(max_length=128, null=True)
    metadata = fields.JSONField(null=False, default=dict)
    status = fields.CharField(max_length=16, null=False, default="ready")
    favorite = fields.BooleanField(null=False, default=False)
    deleted_at = fields.DatetimeField(null=True)

    class Meta(BaseModel.Meta):
        table = "assets"
        abstract = False
        indexes = [
            ("user_id", "asset_type"),
            ("project_id", "asset_type"),
            ("source_type", "source_id"),
        ]
