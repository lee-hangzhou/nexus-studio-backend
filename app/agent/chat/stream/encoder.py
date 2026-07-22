import json

from app.agent.chat.stream.frames import StreamFrame


def encode_sse_frame(frame: StreamFrame) -> str:
    payload = frame.model_dump(mode="json", exclude_none=True)
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
