from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field

from app.contracts.gateway import GatewayGenerateCallback, GatewayResultItem
from app.contracts.generation import GenerateParamOptions
from app.domain.generation.enums import GenerationKind, GenerationTaskStatus, ReferenceMode

# ── 提交 ──────────────────────────────────────────────────────────────────────

class SubmitGenerateRequest(BaseModel):
    kind: GenerationKind
    prompt: str = Field(..., min_length=1)
    model_id: str = Field(..., min_length=1)
    voice_id: str | None = None
    ratio: Optional[str] = None
    resolution: Optional[str] = Field(default=None)
    count: int = Field(default=1, ge=1, le=6)
    duration: Optional[int] = Field(default=None, ge=3, le=15)
    reference_mode: Optional[ReferenceMode] = None
    ref_attachment_ids: List[int] = Field(default_factory=list)
    ref_asset_ids: List[int] = Field(default_factory=list)


class GenerateTaskSubmitResponse(BaseModel):
    task_id: int
    status: GenerationTaskStatus


class GenerateMaterialUploadResponse(BaseModel):
    material_id: int
    asset_id: Optional[int] = None
    filename: str
    mime_type: str
    url: str


# ── 任务状态 ───────────────────────────────────────────────────────────────────

class GenerateTaskStatusRequest(BaseModel):
    task_id: int


class GenerateTasksStatusRequest(BaseModel):
    task_ids: List[int] = Field(..., min_length=1, max_length=100)


class GenerateRefMaterial(BaseModel):
    attachment_id: Optional[int] = None
    asset_id: Optional[int] = None
    filename: str
    mime_type: str
    url: str
    source_type: Optional[str] = None


class GenerateTaskView(BaseModel):
    task_id: int
    kind: GenerationKind
    status: GenerationTaskStatus
    prompt: str
    model_id: str
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration: Optional[int] = None
    reference_mode: Optional[ReferenceMode] = None
    ref_materials: List[GenerateRefMaterial] = Field(default_factory=list)
    result_count: int = 0
    result_urls: List[GatewayResultItem] = Field(default_factory=list)
    result_asset_ids: List[int] = Field(default_factory=list)
    error_message: Optional[str] = None
    is_favorited: bool = False
    queue_status: Optional[int] = None
    queue_position: Optional[int] = None
    queue_total: Optional[int] = None
    estimated_wait_seconds: Optional[int] = None
    created_at: datetime


class GenerateTasksStatusResponse(BaseModel):
    items: List[GenerateTaskView]
    missing_task_ids: List[int] = Field(default_factory=list)


# ── 历史列表（cursor 分页，轻量条目）──────────────────────────────────────────

class GenerateHistoryItemView(BaseModel):
    """历史侧栏列表：仅首图预览 URL，不含完整 result_urls / ref_materials。"""

    task_id: int
    kind: GenerationKind
    status: GenerationTaskStatus
    prompt: str
    model_id: str
    ratio: Optional[str] = None
    resolution: Optional[str] = None
    duration: Optional[int] = None
    reference_mode: Optional[ReferenceMode] = None
    result_count: int = 0
    preview_url: Optional[str] = None
    preview_asset_id: Optional[int] = None
    preview_media_type: Optional[int] = None
    error_message: Optional[str] = None
    is_favorited: bool = False
    queue_status: Optional[int] = None
    queue_position: Optional[int] = None
    queue_total: Optional[int] = None
    estimated_wait_seconds: Optional[int] = None
    created_at: datetime


class HistoryRequest(BaseModel):
    kind: str = Field(default="all", pattern="^(all|image|video)$")
    status: str = Field(default="all", pattern="^(all|in_progress|success|failed)$")
    time_range: str = Field(default="all", pattern="^(all|today|week|month)$")
    query: str = Field(default="")
    favorites_only: bool = False
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: Optional[str] = None


class HistoryResponse(BaseModel):
    items: List[GenerateHistoryItemView]
    next_cursor: Optional[str] = None
    has_more: bool = False


# ── 取消 / 收藏 / 删除 ───────────────────────────────────────────────────────

class TaskCancelRequest(BaseModel):
    task_id: int


class TaskFavoriteRequest(BaseModel):
    task_id: int
    favorited: bool


class TaskDeleteRequest(BaseModel):
    task_id: int


class GenerateModelItem(BaseModel):
    model_id: str
    label: str
    kind: GenerationKind
    supports_vision: bool = False
    param_options: GenerateParamOptions = Field(default_factory=GenerateParamOptions)


class GenerateModelsResponse(BaseModel):
    items: List[GenerateModelItem]


# 回调协议直接复用网关 DTO，camelCase 仅存在于 gateway contract alias。
GenerateCallbackPayload = GatewayGenerateCallback
