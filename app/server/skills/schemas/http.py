from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.server.skills.domain.enums import SkillScope, SkillSurface


class SkillDirMetaSchema(BaseModel):
    """技能目录元数据响应"""

    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    revision: int


class SkillFileMetaSchema(BaseModel):
    """技能文件元数据响应"""

    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    name: str
    revision: int
    enabled: bool
    description: str


class SkillTreeViewSchema(BaseModel):
    """技能树视图响应，不含文件内容"""

    model_config = ConfigDict(extra="forbid")

    dirs: list[SkillDirMetaSchema] = Field(default_factory=list)
    files: list[SkillFileMetaSchema] = Field(default_factory=list)


class DualTreeViewSchema(BaseModel):
    """用户与项目双树视图响应"""

    model_config = ConfigDict(extra="forbid")

    user: SkillTreeViewSchema
    project: SkillTreeViewSchema | None = None


class SkillFileDetailSchema(BaseModel):
    """技能文件详情响应，含内容"""

    model_config = ConfigDict(extra="forbid")

    id: int
    path: str
    name: str
    revision: int
    enabled: bool
    description: str
    content: str


class SelectedSkillSchema(BaseModel):
    """运行时选中的技能响应"""

    model_config = ConfigDict(extra="forbid")

    path: str
    scope: SkillScope
    content: str
    description: str


class UserSkillsListRequest(BaseModel):
    """列出用户技能树请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    project_id: int | None = None
    scope: SkillScope | None = None
    enabled: bool | None = None


class UserSkillsGetRequest(BaseModel):
    """读取单个技能文件请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    path: str = Field(min_length=1)
    project_id: int | None = None


class UserSkillsMkdirRequest(BaseModel):
    """创建技能目录请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    path: str = Field(min_length=1)
    project_id: int | None = None


class UserSkillsWriteRequest(BaseModel):
    """创建或覆盖技能文件请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    path: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str | None = None
    content: str = ""
    revision: int | None = None
    project_id: int | None = None
    id: int | None = None


class UserSkillsMoveRequest(BaseModel):
    """移动技能路径请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    from_path: str = Field(min_length=1)
    to_path: str = Field(min_length=1)
    revision: int
    project_id: int | None = None


class UserSkillsRemoveRequest(BaseModel):
    """删除技能路径请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    path: str = Field(min_length=1)
    revision: int
    project_id: int | None = None


class UserSkillsSetEnabledRequest(BaseModel):
    """切换技能文件启用状态请求"""

    model_config = ConfigDict(extra="forbid")

    surface: SkillSurface
    scope: SkillScope
    path: str = Field(min_length=1)
    enabled: bool
    revision: int
    project_id: int | None = None
