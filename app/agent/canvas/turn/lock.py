from app.server.infra.config import settings
from app.server.infra.logger import logger
from app.server.infra.redis import redis_client
from app.server.canvas.domain.constants import CANVAS_TURN_LOCK_KEY_TEMPLATE
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode


class ProjectTurnLock:
    """基于 Redis NX 的项目级 turn 互斥锁"""

    def _key(self, project_id: int) -> str:
        """生成项目锁 Redis key"""
        return CANVAS_TURN_LOCK_KEY_TEMPLATE.format(project_id=project_id)

    async def acquire(self, project_id: int, turn_id: str) -> None:
        """尝试获取锁, 失败抛 CANVAS_PROJECT_BUSY"""
        ok = await redis_client.set(
            self._key(project_id),
            turn_id,
            ex=settings.CANVAS_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            active = await redis_client.get(self._key(project_id))
            logger.info(
                "canvas.turn.lock",
                action="busy",
                project_id=project_id,
                turn_id=turn_id,
                active_turn_id=active,
            )
            raise AppError(
                ErrorCode.CANVAS_PROJECT_BUSY,
                f"canvas project {project_id} is busy",
                details={"active_turn_id": active},
            )
        logger.info("canvas.turn.lock", action="acquire", project_id=project_id, turn_id=turn_id)

    async def release(self, project_id: int, turn_id: str) -> None:
        """仅释放当前 turn 持有的锁, 避免误删他人锁"""
        key = self._key(project_id)
        active = await redis_client.get(key)
        if active == turn_id:
            await redis_client.delete(key)
            logger.info("canvas.turn.lock", action="release", project_id=project_id, turn_id=turn_id)

    async def force_cancel(self, project_id: int) -> str | None:
        """强制取消当前项目活跃 turn, 供用户主动取消"""
        key = self._key(project_id)
        active = await redis_client.get(key)
        if active:
            await redis_client.delete(key)
        return active


project_turn_lock = ProjectTurnLock()
