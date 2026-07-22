from typing import List, Optional

from pydantic import BaseModel, Field


class AssetCaptionRequest(BaseModel):
    """请求模型网关生成资产 caption 的载荷"""

    model: str
    storage_key: str
    mime_type: str


class AssetCaptionResult(BaseModel):
    """模型网关返回的资产 caption 结果"""

    caption: str = Field(min_length=1)


class AssetEmbeddingRequest(BaseModel):
    """请求模型网关生成统一多模态 embedding 的载荷"""

    model: str
    text: str
    storage_key: Optional[str] = None
    mime_type: Optional[str] = None


class AssetEmbeddingResult(BaseModel):
    """模型网关返回的 embedding 结果"""

    embedding: List[float]
