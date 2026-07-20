"""SSE frame contracts.

具体 frame 定义集中在 ``app.contracts.stream``；本模块保留聊天流内部的稳定导入路径。
"""

from app.contracts.stream import StreamFrame, create_stream_frame
from app.domain.chat.enums import StreamFrameType

__all__ = ["StreamFrame", "StreamFrameType", "create_stream_frame"]
