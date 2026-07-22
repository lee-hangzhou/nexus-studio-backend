from fastapi import APIRouter

from app.server.api.schemas import Response

router = APIRouter()


@router.get("")
async def health_check() -> Response[dict]:
    return Response(data={"status": "ok"})


@router.get("/ready")
async def readiness_check() -> Response[dict]:
    return Response(data={"status": "ready"})
