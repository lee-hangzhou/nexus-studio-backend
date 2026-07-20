from pydantic import BaseModel, Field


class CanvasMemory(BaseModel):
    """一条结构化画布记忆"""

    subject: str = Field(description="记忆描述的主体，例如用户、项目、偏好或素材")
    predicate: str = Field(description="主体和内容之间的关系或属性类型")
    object: str = Field(description="需要记住的具体值、偏好或决策")
    context: str = Field(description="这条记忆适用的范围，例如某个项目或画布工作流")
