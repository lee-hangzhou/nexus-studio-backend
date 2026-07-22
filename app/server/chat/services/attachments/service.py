import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import List
from uuid import uuid4

from fastapi import UploadFile

from app.server.assets.services.service import ASSET_SOURCE_CHAT_UPLOAD, asset_service, asset_type_from_mime
from app.server.chat.services.attachments.materialize_cache import is_cache_hit, load_manifest, manifest_key, record_materialized
from app.server.chat.services.attachments.status import attachment_source
from app.server.chat.services.vision import compress_image_bytes, is_image_mime
from app.server.infra.object_storage import TosObjectStorage
from app.server.chat.domain.enums import AttachmentSource, ChatAttachmentStatus
from app.server.exceptions.base import AppError
from app.server.exceptions.codes import ErrorCode
from app.server.chat.persistence.attachments import ChatAttachments


@dataclass(frozen=True)
class AttachmentItem:
    id: int
    filename: str
    mime_type: str
    storage_key: str
    size: int
    status: int
    is_attached: bool
    source: str


@dataclass(frozen=True)
class MaterializedAttachment:
    attachment_id: int
    filename: str
    workspace_path: str


class ChatAttachmentService:
    def __init__(self) -> None:
        self.storage = TosObjectStorage()

    async def upload_and_enqueue(
        self,
        *,
        user_id: int,
        conversation_id: int,
        filename: str,
        mime_type: str,
        raw_bytes: bytes,
    ) -> ChatAttachments:
        upload = UploadFile(file=BytesIO(raw_bytes), filename=filename)
        effective_bytes = raw_bytes
        effective_mime = mime_type
        if is_image_mime(mime_type):
            effective_bytes, effective_mime = compress_image_bytes(raw_bytes, mime_type=mime_type)
            upload = UploadFile(file=BytesIO(effective_bytes), filename=filename)

        storage_key = f"chat/{user_id}/{conversation_id}/{uuid4().hex}/{filename}"
        await self.storage.put_upload_file(storage_key, upload)

        row = await ChatAttachments.create(
            conversation_id=conversation_id,
            user_id=user_id,
            filename=filename,
            mime_type=effective_mime,
            storage_key=storage_key,
            size=len(effective_bytes),
            status=int(ChatAttachmentStatus.UPLOADED),
            source=AttachmentSource.USER_UPLOAD.value,
            is_attached=True,
            file_sha256=hashlib.sha256(effective_bytes).hexdigest(),
        )
        asset = await asset_service.create_asset(
            user_id=user_id,
            storage_key=storage_key,
            filename=filename,
            mime_type=effective_mime,
            asset_type=asset_type_from_mime(effective_mime),
            source_type=ASSET_SOURCE_CHAT_UPLOAD,
            source_id=row.id,
            metadata={
                "conversation_id": conversation_id,
                "attachment_id": row.id,
                "size": len(effective_bytes),
                "file_sha256": row.file_sha256,
            },
        )
        row.asset_id = asset.id
        await row.save(update_fields=["asset_id"])
        return row

    async def materialize_to_workspace(
        self,
        workspace: Path,
        rows: list[ChatAttachments],
    ) -> list[MaterializedAttachment]:
        attachments_dir = workspace / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        materialized: list[MaterializedAttachment] = []
        for row in rows:
            safe_name = Path(row.filename).name
            if not safe_name:
                continue

            binary_rel = f"attachments/{safe_name}"

            if is_cache_hit(workspace, row.id, row.file_sha256, safe_name):
                manifest_entry = load_manifest(workspace).get(manifest_key(row.id, row.file_sha256), {})
                binary_rel = manifest_entry.get("binary_rel") or binary_rel
            else:
                await self.storage.download_to_path(row.storage_key, workspace / binary_rel)
                record_materialized(
                    workspace,
                    attachment_id=row.id,
                    file_sha256=row.file_sha256,
                    binary_rel=binary_rel,
                )

            materialized.append(
                MaterializedAttachment(
                    attachment_id=row.id,
                    filename=safe_name,
                    workspace_path=binary_rel,
                )
            )
        return materialized

    async def list_attached(self, *, user_id: int, conversation_id: int) -> List[AttachmentItem]:
        rows = await ChatAttachments.filter(
            user_id=user_id,
            conversation_id=conversation_id,
            is_attached=True,
        ).order_by("created_at")
        return [self._to_item(row) for row in rows]

    async def detach(self, *, user_id: int, conversation_id: int, attachment_id: int) -> None:
        row = await ChatAttachments.get_or_none(
            id=attachment_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "attachment not found")
        row.is_attached = False
        row.status = int(ChatAttachmentStatus.DETACHED)
        row.detached_at = datetime.now(timezone.utc)
        await row.save(update_fields=["is_attached", "status", "detached_at"])

    async def attach(self, *, user_id: int, conversation_id: int, attachment_id: int) -> None:
        row = await ChatAttachments.get_or_none(
            id=attachment_id,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "attachment not found")
        row.is_attached = True
        if row.status == int(ChatAttachmentStatus.DETACHED):
            row.status = int(ChatAttachmentStatus.UPLOADED)
        await row.save(update_fields=["is_attached", "status"])

    async def bind_to_message(
        self,
        *,
        user_id: int,
        conversation_id: int,
        attachment_ids: list[int],
        message_id: int,
    ) -> None:
        if not attachment_ids:
            return
        await ChatAttachments.filter(
            id__in=attachment_ids,
            user_id=user_id,
            conversation_id=conversation_id,
        ).update(message_id=message_id)

    def build_preview_url(self, storage_key: str) -> str:
        return self.storage.presigned_get_url(storage_key)

    @staticmethod
    def _to_item(row: ChatAttachments) -> AttachmentItem:
        return AttachmentItem(
            id=row.id,
            filename=row.filename,
            mime_type=row.mime_type,
            storage_key=row.storage_key,
            size=row.size,
            status=row.status,
            is_attached=row.is_attached,
            source=attachment_source(row),
        )


chat_attachment_service = ChatAttachmentService()
