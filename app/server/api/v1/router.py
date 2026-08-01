from fastapi import APIRouter

from app.server.api.v1.endpoints import (
    assets,
    auth,
    billing,
    canvas,
    chat,
    episodes,
    generate,
    health,
    projects,
    user_skills,
    users,
    workshop,
)

api_router = APIRouter()

api_router.include_router(health.router, prefix="/health", tags=["health"])
api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(chat.router, prefix="/chat", tags=["chat"])
api_router.include_router(generate.router, prefix="/generate", tags=["generate"])
api_router.include_router(projects.router, prefix="/projects", tags=["projects"])
api_router.include_router(episodes.router, prefix="/episodes", tags=["episodes"])
api_router.include_router(canvas.router, prefix="/canvas", tags=["canvas"])
api_router.include_router(assets.router, prefix="/assets", tags=["assets"])
api_router.include_router(user_skills.router, prefix="/user-skills", tags=["user-skills"])
api_router.include_router(workshop.router, prefix="/workshop", tags=["workshop"])
api_router.include_router(billing.router, prefix="/billing", tags=["billing"])
