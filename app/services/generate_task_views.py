from app.assets.service import AssetService
from app.chat.attachments.service import ChatAttachmentService
from app.models.assets import Assets
from app.models.chat_attachments import ChatAttachments
from app.models.generate_task import GenerateTask
from app.schemas.generate import (
    GenerateRefMaterial,
    GenerateTaskListItem,
    GenerateTaskView,
)
from app.services.generation_assets import task_result_asset_ids, to_result_urls
from app.services.generation_observation import GatewayQueueObservation


class GenerateTaskViewAssembler:
    def __init__(
        self,
        asset_service: AssetService,
        attachment_service: ChatAttachmentService,
    ) -> None:
        self._asset_service = asset_service
        self._attachment_service = attachment_service

    def task_view(
        self,
        task: GenerateTask,
        *,
        observation: GatewayQueueObservation | None = None,
        ref_materials: list[GenerateRefMaterial],
        favorited_asset_ids: set[int],
    ) -> GenerateTaskView:
        result_urls = to_result_urls(task.result_keys) if task.result_keys else []
        view = GenerateTaskView(
            task_id=task.id,
            kind=task.kind,
            status=task.status,
            prompt=task.prompt,
            model_id=task.model_id,
            ratio=task.ratio,
            resolution=task.resolution,
            duration=task.duration,
            reference_mode=task.reference_mode,
            ref_materials=ref_materials,
            result_count=len(result_urls),
            result_urls=result_urls,
            result_asset_ids=task_result_asset_ids(task),
            error_message=task.error_message,
            is_favorited=self._has_favorited_result_asset(
                task,
                favorited_asset_ids,
            ),
            created_at=task.created_at,
        )
        if observation is not None:
            view.queue_position = observation.position
            view.queue_total = observation.total
            view.estimated_wait_seconds = observation.estimated_wait_seconds
        return view

    def task_list_item(
        self,
        task: GenerateTask,
        *,
        observation: GatewayQueueObservation | None = None,
        favorited_asset_ids: set[int],
    ) -> GenerateTaskListItem:
        result_urls = to_result_urls(task.result_keys) if task.result_keys else []
        result_asset_ids = task_result_asset_ids(task)
        preview = result_urls[0] if result_urls else None
        view = GenerateTaskListItem(
            task_id=task.id,
            kind=task.kind,
            status=task.status,
            prompt=task.prompt,
            model_id=task.model_id,
            ratio=task.ratio,
            resolution=task.resolution,
            duration=task.duration,
            reference_mode=task.reference_mode,
            result_count=len(result_urls),
            preview_url=preview.url if preview is not None else None,
            preview_asset_id=result_asset_ids[0] if result_asset_ids else None,
            preview_media_type=preview.type if preview is not None else None,
            error_message=task.error_message,
            is_favorited=self._has_favorited_result_asset(
                task,
                favorited_asset_ids,
            ),
            created_at=task.created_at,
        )
        if observation is not None:
            view.queue_position = observation.position
            view.queue_total = observation.total
            view.estimated_wait_seconds = observation.estimated_wait_seconds
        return view

    def reference_materials_by_task_id(
        self,
        tasks: list[GenerateTask],
        attachments: list[ChatAttachments],
        assets: list[Assets],
    ) -> dict[int, list[GenerateRefMaterial]]:
        attachment_by_id = {row.id: row for row in attachments}
        asset_by_id = {row.id: row for row in assets}
        result: dict[int, list[GenerateRefMaterial]] = {}

        for task in tasks:
            materials: list[GenerateRefMaterial] = []
            for attachment_id in task.ref_attachment_ids or []:
                attachment = attachment_by_id.get(attachment_id)
                if attachment is None:
                    continue
                materials.append(
                    GenerateRefMaterial(
                        attachment_id=attachment.id,
                        asset_id=attachment.asset_id,
                        filename=attachment.filename,
                        mime_type=attachment.mime_type,
                        url=self._attachment_service.build_preview_url(
                            attachment.storage_key
                        ),
                        source_type="chat_attachment",
                    )
                )
            for asset_id in task.ref_asset_ids or []:
                asset = asset_by_id.get(asset_id)
                if asset is None:
                    continue
                materials.append(
                    GenerateRefMaterial(
                        asset_id=asset.id,
                        filename=asset.filename,
                        mime_type=asset.mime_type,
                        url=self._asset_service.preview_url(asset.storage_key),
                        source_type=asset.source_type,
                    )
                )
            result[task.id] = materials

        return result

    @staticmethod
    def _has_favorited_result_asset(
        task: GenerateTask,
        favorited_asset_ids: set[int],
    ) -> bool:
        return any(
            asset_id in favorited_asset_ids
            for asset_id in task_result_asset_ids(task)
        )
