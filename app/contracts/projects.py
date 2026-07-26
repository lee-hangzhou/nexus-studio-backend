from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class CoverView(BaseModel):
    asset_id: int
    asset_type: Literal["image", "video"]
    url: str


class ProjectView(BaseModel):
    id: int
    name: str
    status: int
    episode_count: int = 0
    activity_at: str
    cover_asset_id: int | None = Field(default=None, ge=1)
    cover: CoverView | None = None
    created_at: str
    updated_at: str


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class ProjectListRequest(BaseModel):
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=11, ge=1, le=60)
    query: str = ""


class ProjectIdRequest(BaseModel):
    project_id: int = Field(ge=1)


class ProjectUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cover_asset_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_update(self) -> "ProjectUpdateRequest":
        if "name" not in self.model_fields_set and "cover_asset_id" not in self.model_fields_set:
            raise ValueError("name or cover_asset_id required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self


class EpisodeView(BaseModel):
    id: int
    project_id: int
    creator_id: int
    episode_no: int
    name: str
    cover_asset_id: int | None = Field(default=None, ge=1)
    cover: CoverView | None = None
    created_at: str
    updated_at: str


class ProjectCreateResponse(BaseModel):
    project: ProjectView
    default_episode: EpisodeView


class ProjectDetailResponse(BaseModel):
    project: ProjectView
    episodes: list[EpisodeView]


class ProjectListResponse(BaseModel):
    items: list[ProjectView]
    page: int = 1
    page_size: int = 11
    total: int = 0


class EpisodeListRequest(BaseModel):
    project_id: int = Field(ge=1)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=200)
    query: str = ""


class EpisodeListResponse(BaseModel):
    items: list[EpisodeView]
    page: int = 1
    page_size: int = 100
    total: int = 0


class EpisodeCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)


class EpisodeIdRequest(BaseModel):
    episode_id: int = Field(ge=1)


class EpisodeUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    episode_id: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=255)
    cover_asset_id: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_update(self) -> "EpisodeUpdateRequest":
        if "name" not in self.model_fields_set and "cover_asset_id" not in self.model_fields_set:
            raise ValueError("name or cover_asset_id required")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("name cannot be null")
        return self
