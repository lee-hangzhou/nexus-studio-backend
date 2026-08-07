"""生成提交用例：人手画布先单次 prepare，再走 GenerationService.submit 绑定节点"""

from __future__ import annotations

from app.agent.canvas.node_submit.prepare import prepare_node_submit
from app.agent.canvas.node_submit.types import ManualMaterialRef, MentionItemRef
from app.composition import generation_service
from app.server.canvas.services.episode_fence import canvas_episode_fence
from app.server.generation.schemas import SubmitGenerateRequest, GenerateTaskSubmitResponse
from app.server.projects.services import canvas_scope_service


async def submit_generate(
    *,
    user_id: int,
    body: SubmitGenerateRequest,
) -> GenerateTaskSubmitResponse:
    """统一 /generate/submit：创作页直提；画布人手 prepare 一次后提交并绑定"""
    req = body
    if body.episode_id is not None and body.node_id:
        await canvas_scope_service.require_write_scope(user_id, body.episode_id)
        async with canvas_episode_fence.generation(body.episode_id):
            if body.submit_content is not None:
                prepared = await prepare_node_submit(
                    body.episode_id,
                    body.node_id.strip(),
                    mode="manual",
                    prompt=body.prompt,
                    ref_asset_ids=body.ref_asset_ids,
                    content=body.submit_content,
                    manual_refs=[
                        ManualMaterialRef(asset_id=item.asset_id) for item in body.manual_refs
                    ],
                    preview_media_refs=[
                        MentionItemRef(asset_id=asset_id, type="image")
                        for asset_id in body.preview_media_asset_ids
                    ],
                )
                req = body.model_copy(
                    update={
                        "prompt": prepared.prompt,
                        "ref_asset_ids": list(prepared.ref_asset_ids),
                        "submit_content": None,
                        "manual_refs": [],
                        "preview_media_asset_ids": [],
                    }
                )
            return await generation_service.submit(user_id, req)
    return await generation_service.submit(user_id, req)
