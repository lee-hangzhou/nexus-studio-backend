from pydantic import BaseModel, Field


class ChatMemory(BaseModel):
    """一条结构化 Chat 长期记忆。"""

    subject: str = Field(description="记忆描述的主体，例如用户、当前任务")
    predicate: str = Field(description="主体和内容之间的关系或属性类型")
    object: str = Field(description="需要记住的具体值、偏好或决策")
    context: str = Field(description="这条记忆适用的范围，例如全局用户偏好或仅本会话")
