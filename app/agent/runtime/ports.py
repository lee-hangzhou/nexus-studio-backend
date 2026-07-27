from __future__ import annotations

from app.server.ports.product import AssetsPort, CanvasPort, ChatPort, GenerationPort, UserSkillPort

_generation: GenerationPort | None = None
_canvas: CanvasPort | None = None
_chat: ChatPort | None = None
_assets: AssetsPort | None = None
_user_skills: UserSkillPort | None = None


def configure_ports(
    *,
    generation: GenerationPort,
    canvas: CanvasPort,
    chat: ChatPort,
    assets: AssetsPort,
    user_skills: UserSkillPort,
) -> None:
    """注入 agent 运行时 Port 句柄"""
    global _generation, _canvas, _chat, _assets, _user_skills
    _generation = generation
    _canvas = canvas
    _chat = chat
    _assets = assets
    _user_skills = user_skills


def get_generation_port() -> GenerationPort:
    """获取生成 Port"""
    if _generation is None:
        raise RuntimeError("generation port not configured")
    return _generation


def get_canvas_port() -> CanvasPort:
    """获取画布 Port"""
    if _canvas is None:
        raise RuntimeError("canvas port not configured")
    return _canvas


def get_chat_port() -> ChatPort:
    """获取聊天 Port"""
    if _chat is None:
        raise RuntimeError("chat port not configured")
    return _chat


def get_assets_port() -> AssetsPort:
    """获取资产 Port"""
    if _assets is None:
        raise RuntimeError("assets port not configured")
    return _assets


def get_user_skill_port() -> UserSkillPort:
    """获取用户技能 Port"""
    if _user_skills is None:
        raise RuntimeError("user skill port not configured")
    return _user_skills
