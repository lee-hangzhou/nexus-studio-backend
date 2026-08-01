from tortoise import fields

from app.server.persistence.model_base import BaseModel
from app.server.workshop.domain.upgrade_invite import UpgradeInviteProposalStatus


class ChatUpgradeInviteProposals(BaseModel):
    conversation_id = fields.BigIntField(null=False)
    user_id = fields.BigIntField(null=False)
    expert_keys = fields.JSONField(null=False)
    primary_expert_key = fields.CharField(max_length=128, null=False)
    rationale = fields.TextField(null=False)
    host_narration = fields.TextField(null=False)
    source_user_text = fields.TextField(null=False)
    status = fields.CharField(
        max_length=32,
        null=False,
        default=UpgradeInviteProposalStatus.PENDING,
    )
    turn_id = fields.CharField(max_length=64, null=True)

    class Meta(BaseModel.Meta):
        table = "chat_upgrade_invite_proposals"
        abstract = False
        indexes = [("conversation_id", "status"), ("user_id", "created_at")]
