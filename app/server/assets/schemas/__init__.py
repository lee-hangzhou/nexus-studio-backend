from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.server.assets.domain.upload_rules import (
    SOURCE_AGENT_UPLOAD,
    DirectUploadSourceType,
)


class AssetListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=40, ge=1, le=100)
    query: str = ""
    asset_type: str = "all"
    source_type: str = "library"
    favorites_only: bool = False
    created_from: str | None = None
    created_to: str | None = None


class AssetIdRequest(BaseModel):
    asset_id: int = Field(ge=1)


class AssetUpdateRequest(BaseModel):
    asset_id: int = Field(ge=1)
    filename: str | None = Field(default=None, max_length=512)
    favorite: bool | None = None


class AssetDeleteRequest(BaseModel):
    asset_ids: list[int] = Field(min_length=1, max_length=100)


class AssetViewResponse(BaseModel):
    id: int
    project_id: int | None = None
    filename: str
    mime_type: str
    asset_type: str
    source_type: str
    source_id: str | None = None
    metadata: dict[str, Any]
    status: str
    favorite: bool
    preview_url: str
    created_at: str
    updated_at: str


class AssetListResponse(BaseModel):
    items: list[AssetViewResponse]
    page: int
    page_size: int
    total: int


class AssetUploadUrlRequest(BaseModel):
    """申请资产车道直传 PUT URL"""

    filename: str = Field(min_length=1, max_length=512)
    source_type: DirectUploadSourceType
    project_id: int | None = Field(default=None, ge=1)
    episode_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _agent_requires_scope(self) -> "AssetUploadUrlRequest":
        """agent_upload 必须带 project_id 或 episode_id"""
        if self.source_type == SOURCE_AGENT_UPLOAD and self.project_id is None and self.episode_id is None:
            raise ValueError("agent_upload 必须提供 project_id 或 episode_id")
        return self


class AssetUploadUrlResponse(BaseModel):
    storage_key: str
    upload_url: str
    expires_in: int
    method: Literal["PUT"] = "PUT"
    source_type: DirectUploadSourceType


class AssetRegisterRequest(BaseModel):
    """登记已直传到 TOS 的对象为资产"""

    storage_key: str = Field(min_length=1, max_length=1024)
    filename: str = Field(min_length=1, max_length=512)
    mime_type: str = Field(min_length=1, max_length=256)
    source_type: DirectUploadSourceType
    project_id: int | None = Field(default=None, ge=1)
    episode_id: int | None = Field(default=None, ge=1)
    size_bytes: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _agent_requires_scope(self) -> "AssetRegisterRequest":
        """agent_upload 必须带 project_id 或 episode_id"""
        if self.source_type == SOURCE_AGENT_UPLOAD and self.project_id is None and self.episode_id is None:
            raise ValueError("agent_upload 必须提供 project_id 或 episode_id")
        return self
