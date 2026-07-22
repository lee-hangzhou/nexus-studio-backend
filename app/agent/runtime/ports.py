from __future__ import annotations

from app.server.ports.product import AssetsPort, CanvasPort, ChatPort, GenerationPort

_generation: GenerationPort | None = None
_canvas: CanvasPort | None = None
_chat: ChatPort | None = None
_assets: AssetsPort | None = None


def configure_ports(
    *,
    generation: GenerationPort,
    canvas: CanvasPort,
    chat: ChatPort,
    assets: AssetsPort,
) -> None:
    global _generation, _canvas, _chat, _assets
    _generation = generation
    _canvas = canvas
    _chat = chat
    _assets = assets


def get_generation_port() -> GenerationPort:
    if _generation is None:
        raise RuntimeError("generation port not configured")
    return _generation


def get_canvas_port() -> CanvasPort:
    if _canvas is None:
        raise RuntimeError("canvas port not configured")
    return _canvas


def get_chat_port() -> ChatPort:
    if _chat is None:
        raise RuntimeError("chat port not configured")
    return _chat


def get_assets_port() -> AssetsPort:
    if _assets is None:
        raise RuntimeError("assets port not configured")
    return _assets
