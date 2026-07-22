from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ChatModelItem(BaseModel):
    key: str
    display_name: str
    family: str
    supports_vision: bool


class ConversationCreateRequest(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class ConversationListRequest(BaseModel):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class ConversationIdRequest(BaseModel):
    conversation_id: int


class AttachmentIdRequest(BaseModel):
    attachment_id: int


class ConversationAttachmentRequest(BaseModel):
    conversation_id: int


class ConversationAttachmentActionRequest(BaseModel):
    conversation_id: int
    attachment_id: int


class ConversationUpdateRequest(BaseModel):
    conversation_id: int
    title: Optional[str] = None
    model: Optional[str] = None


class MessageListRequest(BaseModel):
    conversation_id: int
    before_id: Optional[int] = None
    limit: int = Field(default=50, ge=1, le=100)


class MessageStreamRequest(BaseModel):
    conversation_id: int
    content: str = Field(min_length=1)
    model: str = Field(min_length=1)
    attachment_ids: List[int] = Field(default_factory=list)
    enable_tools: bool = True
    client_turn_id: Optional[str] = None


class ToolStepView(BaseModel):
    name: str
    args: Dict[str, Any] = Field(default_factory=dict)
    result_preview: str


class MemoryInfo(BaseModel):
    summarized: bool = False
    context_message_count: int = 0


class ChatMessageView(BaseModel):
    id: int
    role: str
    content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class MessageListResponse(BaseModel):
    items: List[ChatMessageView]
    has_more: bool = False
    next_before_id: Optional[int] = None


class TurnCancelRequest(BaseModel):
    conversation_id: int


class TurnResumeRequest(BaseModel):
    conversation_id: int
    turn_id: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)
    model: str = Field(min_length=1)
    action: str = Field(pattern="^(submit|cancel)$")
    fields: Dict[str, Any] | None = None


class GateAssetRefreshRequest(BaseModel):
    conversation_id: int
    gate_id: str = Field(min_length=1)


class BridgeCreateRequest(BaseModel):
    conversation_id: int
    gate_id: str = Field(min_length=1)


class BridgeImportRequest(BaseModel):
    bridge_token: str = Field(min_length=1)
    page_url: str = Field(min_length=1)
    cookies: List[Dict[str, Any]]


class GateStateRequest(BaseModel):
    conversation_id: int


class GateCancelRequest(BaseModel):
    conversation_id: int
    turn_id: str = Field(min_length=1)
    gate_id: str = Field(min_length=1)


class ConversationView(BaseModel):
    id: int
    title: str
    default_model: str
    status: int
    is_generating: bool = False
    awaiting_user_gate: bool = False
    generating_started_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: List[ConversationView]
    total: int


class AttachmentView(BaseModel):
    id: int
    filename: str
    mime_type: str
    storage_key: str
    size: int
    status: int = 0
    is_attached: bool = True
    source: str = "user_upload"
    preview_url: Optional[str] = None


class MessageAttachmentView(BaseModel):
    attachment_id: int
    filename: str
    mime_type: str
    preview_url: Optional[str] = None


class AttachmentPreviewResponse(BaseModel):
    attachment_id: int
    filename: str
    mime_type: str
    url: str
