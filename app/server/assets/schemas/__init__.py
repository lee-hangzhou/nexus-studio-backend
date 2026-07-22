from typing import Any

from pydantic import BaseModel, Field


class AssetListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=40, ge=1, le=100)
    query: str = ""
    asset_type: str = "all"
    source_type: str = "all"
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
