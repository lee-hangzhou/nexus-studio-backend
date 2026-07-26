from enum import IntEnum
from pathlib import Path
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.server.infra.config_models import (
    DatabasePoolConfig,
    GatewayTimeoutConfig,
    ObjectStorageTimeoutConfig,
)


def _locate_project_root(start: Path) -> Path:
    """仓库根：同时具备 pyproject.toml 与 app/ 包目录的最近祖先"""
    for candidate in (start, *start.parents):
        if (candidate / "pyproject.toml").is_file() and (candidate / "app").is_dir():
            return candidate
    raise RuntimeError(f"无法从 {start} 定位项目根（缺少 pyproject.toml + app/）")


_PROJECT_ROOT = _locate_project_root(Path(__file__).resolve())
_ENV_FILE = _PROJECT_ROOT / ".env"


class CheckpointerType(IntEnum):
    SQLITE = 1
    POSTGRES = 2


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=[str(_ENV_FILE), ".env"],
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    PROJECT_NAME: str = "nexus-studio"
    VERSION: str = "0.1.0"
    API_V1_PREFIX: str = "/api/v1"

    ENV: str = Field(default="dev")
    DEBUG: bool = Field(default=False)
    PORT: int = Field(default=8000)

    DATABASE_URL: str = Field(default="postgres://root:123456@localhost:5432/dream_drama")
    DATABASE_POOL: DatabasePoolConfig = Field(default_factory=DatabasePoolConfig)


    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    # Per-process pool; total ≈ workers × this value (stay well below Redis maxclients).
    REDIS_MAX_CONNECTIONS: int = Field(default=50)

    # 缓存（MultiLevelCache）
    CACHE_L1_TTL: int = Field(default=10)            # 进程内 L1 默认 TTL，短以控制跨实例脏读
    CACHE_L2_TTL: int = Field(default=120)           # Redis L2 默认 TTL
    CHAT_MODEL_LIST_TTL: int = Field(default=120)    # chat 模型列表缓存
    GEN_MODEL_LIST_TTL: int = Field(default=120)     # 创作页模型列表缓存
    CHAT_MSG_PAGE_TTL: int = Field(default=3600)     # 不可变历史消息页缓存

    # CHANGE THIS IN PRODUCTION!
    JWT_SECRET_KEY: str = Field(default="dev-secret-key-change-in-production")
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=7 * 24 * 60)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=30)

    PASSWORD_RESET_TOKEN_EXPIRE_MINUTES: int = Field(default=5)
    FRONTEND_BASE_URL: str = Field(default="http://localhost:5173")
    FRONTEND_RESET_PASSWORD_URL: str = Field(default="http://localhost:5173/reset-password")

    REGISTER_CODE_EXPIRE_MINUTES: int = Field(default=5)
    REGISTER_CODE_RESEND_SECONDS: int = Field(default=60)
    REGISTER_CODE_LENGTH: int = Field(default=6)

    ALIYUN_ACCESS_KEY_ID: str = Field(default="")
    ALIYUN_ACCESS_KEY_SECRET: str = Field(default="")
    ALIYUN_DM_ENDPOINT: str = Field(default="dm.aliyuncs.com")
    ALIYUN_DM_ACCOUNT_NAME: str = Field(default="support@presentview.com")
    ALIYUN_DM_FROM_ALIAS: str = Field(default="Nexus Studio")
    ALIYUN_DM_TEMPLATE_ID_REGISTER: str = Field(default="432287")
    ALIYUN_DM_TEMPLATE_ID_RESET: str = Field(default="432291")

    SMTP_HOST: str = Field(default="")
    SMTP_PORT: int = Field(default=587)
    SMTP_USER: str = Field(default="")
    SMTP_PASSWORD: str = Field(default="")
    SMTP_FROM: str = Field(default="noreply@dream-drama.local")
    SMTP_USE_TLS: bool = Field(default=True)

    CORS_ORIGINS: str = Field(default="")

    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: str = Field(default="json")

    GENERATE_CALLBACK_URL: str = Field(default="http://localhost:8000/api/v1/generate/callback")
    GENERATE_ZOMBIE_THRESHOLD_MINUTES: int = Field(default=30)

    GATEWAY_BASE_URL: str = Field(default="http://localhost:8083")
    GATEWAY_API_KEY: str = Field(default="")
    GATEWAY_USER_ID: str = Field(default="dream-drama")
    GATEWAY_CALLBACK_BASE_URL: str = Field(default="http://localhost:8000")
    GATEWAY_TEXT_EMBEDDING_MODEL: str = Field(default="qwen/qwen3-embedding-8b")
    GATEWAY_MULTIMODAL_EMBEDDING_MODEL: str = Field(default="google/gemini-embedding-2-preview")
    GATEWAY_EMBEDDING_ENCODING_FORMAT: str = Field(default="float")
    GATEWAY_TIMEOUTS: GatewayTimeoutConfig = Field(default_factory=GatewayTimeoutConfig)
    GATEWAY_CAPTION_MODEL: str = Field(default="default-caption-model")

    CHAT_MODEL_REGISTRY: str = Field(default="{}")
    CHAT_DEFAULT_MODEL: str = Field(default="gpt-5.5")
    CHAT_MAX_ITERATIONS: int = Field(default=50)
    CHAT_CONTEXT_BUDGET: int = Field(default=120_000)
    CHAT_RECENT_MESSAGE_LIMIT: int = Field(default=20)
    CHAT_SUMMARY_TRIGGER_RATIO: float = Field(default=0.6)
    CHAT_SUMMARY_KEEP_TOKENS: int = Field(default=20_000)
    CHAT_SUMMARY_TRIM_TO_SUMMARIZE: int = Field(default=4_000)
    CHAT_WORKSPACE_ROOT: str = Field(default="/tmp/dream-drama-chat-workspaces")
    CHAT_SANDBOX_IMAGE: str = Field(default="dream-drama-chat-sandbox:latest")
    CHAT_SANDBOX_TIMEOUT_SEC: int = Field(default=180)
    CHAT_SANDBOX_PACKAGES_ROOT: str = Field(default="/tmp/dream-drama-sandbox-packages")
    CHAT_SANDBOX_PIP_NETWORK: str = Field(default="bridge")
    CHAT_SANDBOX_RUN_NETWORK: str = Field(default="none")
    CHAT_SANDBOX_PIP_INDEX: str = Field(default="https://mirrors.aliyun.com/pypi/simple/")
    CHAT_SANDBOX_PIP_TRUSTED_HOST: str = Field(default="mirrors.aliyun.com")
    CHAT_GATE_TTL_SEC: int = Field(default=600)
    CHAT_BRIDGE_TTL_SEC: int = Field(default=600)
    CHAT_CHALLENGE_BBOX_DRIFT_RATIO: float = Field(default=0.15)
    CHAT_BROWSER_ENABLED: bool = Field(default=True)
    CHAT_BROWSER_INPROCESS: bool = Field(default=False)
    CHAT_BROWSER_HEADED: bool = Field(default=False)
    CHAT_BROWSER_SLOW_MO_MS: int = Field(default=0)
    CHAT_CAPTCHA_DEBUG_ARTIFACTS: bool = Field(default=False)
    CHAT_BROWSER_IMAGE: str = Field(default="dream-drama-browser-session:latest")
    CHAT_BROWSER_DOCKER_NETWORK: str = Field(default="bridge")
    CHAT_BROWSER_RUN_SERVER_PORT: int = Field(default=3333)
    CHAT_BROWSER_DRIVER_PORT: int = Field(default=3334)
    CHAT_BROWSER_IDLE_SEC: int = Field(default=1800)
    CHAT_BROWSER_EXEC_TIMEOUT_SEC: int = Field(default=180)
    CHAT_BROWSER_STORAGE_ENABLED: bool = Field(default=True)
    CHAT_BROWSER_STORAGE_TTL_SEC: int = Field(default=86400)
    CHAT_BROWSER_DEBUG_CAPTURE_TTL_SEC: int = Field(default=604800)
    CHAT_BROWSER_DEBUG_CAPTURE_MAX_COUNT: int = Field(default=30)
    CHAT_LOGIN_PROBE_TIMEOUT_MS: int = Field(default=15_000)
    CHAT_QR_MIN_WIDTH: int = Field(default=128, ge=32)
    CHAT_QR_MIN_HEIGHT: int = Field(default=128, ge=32)
    CHAT_QR_REQUIRE_DECODE: bool = Field(default=True)
    CHAT_QR_MIN_BBOX_SIZE: int = Field(default=64, ge=16)
    CHAT_GATEWAY_TIMEOUT_SEC: int = Field(default=300, ge=30)
    CHAT_GATEWAY_STEP_RETRIES: int = Field(default=1, ge=0, le=3)
    CHAT_COMPLETION_MAX_TOKENS: int = Field(default=8192)
    CHAT_TURN_LOCK_TTL_SEC: int = Field(default=1800)
    CHAT_HEARTBEAT_INTERVAL_SEC: int = Field(default=15)
    MEMORY_STORE_ENABLED: bool = Field(default=True)
    MEMORY_EMBEDDING_MODEL: str = Field(default="qwen/qwen3-embedding-8b")
    MEMORY_VECTOR_DIMENSION: int = Field(default=3072)
    MEMORY_TOOL_TIMEOUT_SEC: int = Field(default=60, ge=5, le=180)
    # 覆盖网关 embedding 慢请求与排队；过短会导致 project 语义注入每轮 timeout
    MEMORY_INJECTION_TIMEOUT_SEC: int = Field(default=60, ge=1, le=180)
    MEMORY_EXTRACTION_TIMEOUT_SEC: int = Field(default=120, ge=5, le=300)
    # 空=回落到本轮 turn 模型；非空必须在 gateway model catalog
    MEMORY_EXTRACT_MODEL: str = Field(default="")
    MEMORY_USER_INJECT_FETCH_LIMIT: int = Field(default=32, ge=1, le=200)
    MEMORY_PROJECT_INJECT_LIMIT: int = Field(default=5, ge=1, le=50)
    # 2026-07-26 Store asearch 校准（fields=["content"] JSON）：min_pos≈0.660, max_neg≈0.476 → 中点 0.5678
    MEMORY_PROJECT_INJECT_MIN_SCORE: float = Field(default=0.5678)
    CHAT_MAX_TOOL_CALLS: int = Field(default=80)
    CHAT_TURN_WALL_CLOCK_SEC: int = Field(default=900)
    CHAT_TOOL_REPEAT_GUARD: int = Field(default=3)
    CHAT_EMPTY_RECOVERY_ATTEMPTS: int = Field(default=2, ge=0, le=3)
    CHAT_EMPTY_RECOVERY_TIMEOUT_SEC: int = Field(default=45, ge=5, le=120)
    CHAT_TURN_USAGE_LOG_PATH: str = Field(default="/tmp/dream-drama-chat-turn-usage.jsonl")
    CHAT_PERSIST_TOOL_AUDIT: bool = Field(default=False)
    CHAT_SSE_PROTOCOL_VERSION: int = Field(default=2)
    CHAT_MCP_SERVERS: str = Field(default="[]")
    CHAT_ALLOWED_UPLOAD_EXTS: str = Field(default="txt,md,json,csv,docx,xlsx,pptx,pdf,png,jpg,jpeg,webp")
    CHAT_ALLOWED_UPLOAD_MIMES: str = Field(
        default=(
            "text/plain,text/markdown,application/json,text/csv,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
            "application/vnd.openxmlformats-officedocument.presentationml.presentation,"
            "application/pdf,image/png,image/jpeg,image/webp"
        )
    )
    CHAT_TURN_PLAN_CLASSIFIER_ENABLED: bool = Field(default=False)
    BOCHA_API_KEY: str = Field(default="")
    BOCHA_WEB_SEARCH_URL: str = Field(default="https://api.bocha.cn/v1/web-search")
    WEB_TOOL_TIMEOUT_SECONDS: float = Field(default=30, gt=0)
    CHAT_CONTEXT_TIMEZONE: str = Field(default="Asia/Shanghai")

    TOS_ENDPOINT: str = Field(default="")
    TOS_REGION: str = Field(default="cn-shanghai")
    TOS_ACCESS_KEY: str = Field(default="")
    TOS_SECRET_KEY: str = Field(default="")
    TOS_BUCKET: str = Field(default="union-llm")
    TOS_PRESIGN_EXPIRY_SECONDS: int = Field(default=86400)
    OBJECT_STORAGE_TIMEOUTS: ObjectStorageTimeoutConfig = Field(
        default_factory=ObjectStorageTimeoutConfig
    )

    ASSET_VECTOR_DIMENSION: int = Field(default=3072)
    ASSET_RETRIEVAL_LIMIT: int = Field(default=12)

    AGENT_MAX_RETRIES: int = Field(default=2)
    AGENT_MAX_FOLLOW_UPS: int = Field(default=2)
    CHECKPOINTER_TYPE: CheckpointerType = Field(default=CheckpointerType.POSTGRES)
    CHECKPOINTER_SQLITE_PATH: str = Field(default=".langgraph/checkpoints.sqlite")
    CHECKPOINTER_POSTGRES_URI: Optional[str] = Field(
        default="postgresql://root:123456@localhost:5432/dream_drama",
    )

    CANVAS_CHECKPOINT_THREAD_PREFIX: str = Field(default="canvas")
    CANVAS_CONTEXT_BUDGET: int = Field(default=120_000)
    CANVAS_SUMMARY_TRIGGER_RATIO: float = Field(default=0.6)
    CANVAS_SUMMARY_KEEP_TOKENS: int = Field(default=20_000)
    CANVAS_SUMMARY_TRIM_TO_SUMMARIZE: int = Field(default=4_000)
    CANVAS_TRIM_BUDGET_RATIO: float = Field(default=0.85)
    CANVAS_MAX_ITERATIONS: int = Field(default=50)
    CANVAS_MAX_TOOL_CALLS: int = Field(default=80)
    CANVAS_TURN_WALL_CLOCK_SEC: int = Field(default=600)
    CANVAS_TOOL_REPEAT_GUARD: int = Field(default=3)
    CANVAS_TURN_LOCK_TTL_SEC: int = Field(default=1800)
    CANVAS_HEARTBEAT_INTERVAL_SEC: int = Field(default=15)
    CANVAS_DEFAULT_VIDEO_DURATION_SEC: int = Field(default=5, ge=3, le=15)
    CANVAS_MANUAL_CONFIRM_TOOLS: str = Field(
        default="apply_canvas_patch,submit_node_generation",
    )
    # SSE Last-Event-ID replay (Redis Streams; disconnect ≠ cancel)
    SSE_REDIS_SUBSCRIBER_MAX_CONNECTIONS: int = Field(default=512, gt=0)
    SSE_REDIS_CONTROL_MAX_CONNECTIONS: int = Field(default=64, gt=0)
    SSE_REDIS_SUBSCRIBER_ACQUIRE_TIMEOUT_SEC: float = Field(default=20.0, gt=0)
    SSE_REDIS_SUBSCRIBER_SOCKET_TIMEOUT_SEC: float = Field(default=20.0, gt=0)
    SSE_ACTIVE_SUBSCRIBER_WARN_THRESHOLD: int = Field(default=400, gt=0)
    SSE_REPLAY_TTL_SEC: int = Field(default=3600, gt=0)
    SSE_REPLAY_META_TTL_SEC: int = Field(default=86400, gt=0)
    SSE_REPLAY_MAX_BYTES: int = Field(default=32 * 1024 * 1024, gt=0)
    SSE_EXECUTION_LEASE_TTL_SEC: int = Field(default=15, gt=0)
    SSE_EXECUTION_LEASE_REFRESH_SEC: int = Field(default=5, gt=0)
    SSE_DELTA_FLUSH_INTERVAL_MS: int = Field(default=50, gt=0)
    SSE_DELTA_MAX_CHARS: int = Field(default=512, gt=0)
    CANVAS_TURN_CANCEL_WAIT_SEC: float = Field(default=12.0, gt=0)
    CHAT_TURN_CANCEL_WAIT_SEC: float = Field(default=12.0, gt=0)

    @property
    def sse_replay_heartbeat_interval_sec(self) -> int:
        return max(self.CHAT_HEARTBEAT_INTERVAL_SEC, self.CANVAS_HEARTBEAT_INTERVAL_SEC)

    @property
    def canvas_manual_confirm_tools(self) -> frozenset[str]:
        return frozenset(
            name.strip()
            for name in self.CANVAS_MANUAL_CONFIRM_TOOLS.split(",")
            if name.strip()
        )

    @property
    def chat_max_tokens_before_summary(self) -> int:
        return int(self.CHAT_CONTEXT_BUDGET * self.CHAT_SUMMARY_TRIGGER_RATIO)

    @property
    def canvas_max_tokens_before_summary(self) -> int:
        return int(self.CANVAS_CONTEXT_BUDGET * self.CANVAS_SUMMARY_TRIGGER_RATIO)

    @property
    def canvas_max_tokens(self) -> int:
        return int(self.CANVAS_CONTEXT_BUDGET * self.CANVAS_TRIM_BUDGET_RATIO)

    @property
    def cors_origins_list(self) -> List[str]:
        if not self.CORS_ORIGINS:
            return []
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()
