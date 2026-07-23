from app.agent.runtime.turn.enums import TurnTerminatedBy

INVALID_TOOL_RAW_PREVIEW_LEN = 300

TERMINATION_ERROR_MESSAGES: dict[TurnTerminatedBy, str] = {
    TurnTerminatedBy.CANCELLED: "request cancelled",
    TurnTerminatedBy.WALL_CLOCK: "本轮执行超时，已终止",
    TurnTerminatedBy.MAX_ITERATIONS: "max model iterations exceeded",
    TurnTerminatedBy.MAX_TOOLS: "max tool calls exceeded",
    TurnTerminatedBy.GUARD_ERROR: "repeated tool errors",
    TurnTerminatedBy.GUARD_STOP: "guard stopped turn",
    TurnTerminatedBy.TOOL_PARSE_FATAL: "工具参数解析失败",
    TurnTerminatedBy.TURN_FAILED: "turn failed",
    TurnTerminatedBy.INTERNAL: "internal error",
    TurnTerminatedBy.GATEWAY_UPSTREAM_TIMEOUT: "模型服务暂时无响应，请重试",
    TurnTerminatedBy.GATEWAY_EMPTY_STREAM: "模型服务暂时无响应，请重试",
    TurnTerminatedBy.GATEWAY_UPSTREAM_FAILED: "模型服务暂时无响应，请重试",
    TurnTerminatedBy.GATEWAY_PROTOCOL_ERROR: "模型服务暂时无响应，请重试",
    TurnTerminatedBy.AGENT_RECOVERY_EXHAUSTED: "本次回答生成失败，请重试",
    TurnTerminatedBy.EMPTY_RESPONSE: "模型未返回有效回答",
}
