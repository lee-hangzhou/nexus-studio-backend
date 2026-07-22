"""应用级缓存单例。

复用 app/utils/cache.py 的 MultiLevelCache（L1 进程内 LRU + L2 Redis + 防击穿锁 + 负缓存），
统一接入项目 RedisClient，供 chat / generate 等读多写少 / 不可变数据复用。

约定：
- 只缓存不可变 / 读多写极少数据；进行中状态、排队信息不缓存。
- presigned URL 永不入缓存：缓存裸 storage_key / result_keys，签名在读出后现算。
- L1 是进程内的，invalidate_pattern 只清 Redis；易变数据用短 l1_ttl 控制跨实例脏读窗口。
"""

from app.server.infra.config import settings
from app.server.infra.redis import redis_client
from app.utils.cache import MultiLevelCache

app_cache = MultiLevelCache(
    redis=redis_client,
    l1_maxsize=2000,
    l1_ttl=settings.CACHE_L1_TTL,
    l2_ttl=settings.CACHE_L2_TTL,
    key_prefix="dd",
)
