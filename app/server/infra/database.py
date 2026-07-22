from typing import Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from tortoise import Tortoise

from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.singleton import Singleton
from app.server.persistence import TORTOISE_ORM_MODEL_MODULES


class Database(Singleton):
    def __init__(self) -> None:
        self._initialized = False
        self._context = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    def mark_initialized(self) -> None:
        self._initialized = True

    def mark_shutdown(self) -> None:
        self._initialized = False
        self._context = None

    @staticmethod
    def build_url(db_url: Optional[str] = None) -> str:
        return Database._add_pool_params(db_url or settings.DATABASE_URL)

    @staticmethod
    def _add_pool_params(base_url: str) -> str:
        """补充 Tortoise 支持的连接池参数"""

        if base_url.startswith("sqlite"):
            return base_url

        parsed = urlparse(base_url)
        params = parse_qs(parsed.query)

        configured_pool = settings.DATABASE_POOL
        pool_config: dict[str, int] = {
            "minsize": configured_pool.min_size,
            "maxsize": configured_pool.max_size,
        }
        if parsed.scheme.startswith("mysql"):
            pool_config["connect_timeout"] = configured_pool.connect_timeout_seconds
            pool_config["pool_recycle"] = configured_pool.recycle_seconds

        for key, value in pool_config.items():
            if key not in params:
                params[key] = [str(value)]

        new_query = urlencode(params, doseq=True)
        return urlunparse(parsed._replace(query=new_query))

    async def connect(self, db_url: Optional[str] = None) -> None:
        """脚本/测试直连数据库时使用；FastAPI 应用请走 lifespan 中的 RegisterTortoise。"""

        if self._initialized:
            logger.warning("Database already initialized")
            return

        full_url = self.build_url(db_url)

        self._context = await Tortoise.init(
            db_url=full_url,
            modules={"models": list(TORTOISE_ORM_MODEL_MODULES)},
            use_tz=True,
            _enable_global_fallback=True,
        )

        self._initialized = True
        logger.info("Database initialized")

    async def disconnect(self) -> None:
        if not self._initialized:
            return

        await Tortoise.close_connections()
        self._initialized = False
        self._context = None
        logger.info("Database connections closed")


db = Database()
