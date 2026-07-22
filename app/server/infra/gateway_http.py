import httpx

from app.server.infra.config import settings


class GatewayHttpClient:
    """模型网关共享 HTTP 连接池。"""

    def __init__(self) -> None:
        timeout = settings.GATEWAY_TIMEOUTS
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=timeout.connect_seconds,
                read=timeout.read_seconds,
                write=timeout.write_seconds,
                pool=timeout.pool_seconds,
            )
        )

    async def close(self) -> None:
        await self.client.aclose()


gateway_http_client = GatewayHttpClient()
