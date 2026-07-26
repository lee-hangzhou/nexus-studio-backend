from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class UserMemory(BaseModel):
    """一条用户级记忆（偏好或用户明确要求保存的背景事实）"""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(description="用户明确要求记住的一条完整陈述或长期偏好")
    context: str = Field(default="", description="适用上下文或来源说明")


class ProjectFactMemory(BaseModel):
    """一条画布项目耐久事实"""

    model_config = ConfigDict(extra="forbid")

    subject: str = Field(description="叙事实体或设定对象")
    predicate: str = Field(description="主体与内容之间的关系或属性类型")
    object: str = Field(description="需要记住的具体值或决策")
    context: str = Field(default="", description="适用范围说明")
