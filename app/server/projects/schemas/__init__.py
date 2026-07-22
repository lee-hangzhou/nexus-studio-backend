from pydantic import BaseModel, Field


class ProjectView(BaseModel):
    id: int
    name: str
    status: int
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


class ProjectListResponse(BaseModel):
    items: list[ProjectView]
    page: int = 1
    page_size: int = 11
    total: int = 0
