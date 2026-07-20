LANGGRAPH_EVENTS_PER_MODEL_STEP = 4


def langgraph_recursion_limit(max_model_steps: int) -> int:
    """LangGraph 每个模型步骤最多经过模型、工具及状态路由等四个图事件。"""

    return max_model_steps * LANGGRAPH_EVENTS_PER_MODEL_STEP
