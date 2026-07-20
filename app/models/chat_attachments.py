from tortoise import fields

from app.models.base import AppendOnlyModel


class ChatAttachments(AppendOnlyModel):
    conversation_id = fields.BigIntField(null=False)
    message_id = fields.BigIntField(null=True)
    asset_id = fields.BigIntField(null=True)
    user_id = fields.BigIntField(null=False)
    filename = fields.CharField(max_length=512, null=False)
    mime_type = fields.CharField(max_length=128, null=False)
    storage_key = fields.CharField(max_length=1024, null=False)
    size = fields.BigIntField(null=False)
    status = fields.IntField(null=False, default=1)
    is_attached = fields.BooleanField(null=False, default=True)
    detached_at = fields.DatetimeField(null=True)
    parse_error = fields.TextField(null=True)
    file_sha256 = fields.CharField(max_length=64, null=True)
    source = fields.CharField(max_length=32, null=False, default="user_upload")

    class Meta(AppendOnlyModel.Meta):
        table = "chat_attachments"
        abstract = False
        indexes = [
            ("conversation_id", "created_at"),
            ("conversation_id", "is_attached", "status"),
            ("message_id",),
        ]
