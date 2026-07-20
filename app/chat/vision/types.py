from __future__ import annotations

IMAGE_REF_TYPE = "image_ref"
TEXT_BLOCK_TYPE = "text"

CHAT_IMAGE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
CHAT_IMAGE_MAX_BASE64_BYTES = 5_242_880
# base64 最大字节按 ~4 chars/token 粗算，vision 实际计费更高；trim 用固定上界避免低估
CHAT_IMAGE_TOKEN_ESTIMATE = 3500

IMAGE_COMPRESS_FAILED_MESSAGE = "图片过大，压缩后仍超出限制，请上传更小的图片"
