from pydantic import BaseModel, ConfigDict, Field


class RuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GatewayTimeoutConfig(RuntimeConfigModel):
    connect_seconds: float = Field(default=10, gt=0)
    read_seconds: float = Field(default=300, gt=0)
    write_seconds: float = Field(default=60, gt=0)
    pool_seconds: float = Field(default=10, gt=0)
    generation_seconds: float = Field(default=120, gt=0)
    query_seconds: float = Field(default=30, gt=0)
    embedding_seconds: float = Field(default=120, gt=0)


class ObjectStorageTimeoutConfig(RuntimeConfigModel):
    connect_seconds: int = Field(default=30, gt=0)
    socket_seconds: int = Field(default=60, gt=0)
    operation_seconds: int = Field(default=120, gt=0)


class DatabasePoolConfig(RuntimeConfigModel):
    min_size: int = Field(default=5, ge=1)
    max_size: int = Field(default=20, ge=1)
    connect_timeout_seconds: int = Field(default=10, gt=0)
    recycle_seconds: int = Field(default=3600, gt=0)
