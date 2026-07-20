# Chat Stream Agent

## 开发与测试环境

Python 使用 Conda 环境 **`dream-drama-env`**（与根目录 [README.md](../README.md) 一致）：

```bash
conda activate dream-drama-env
pip install -r requirements.txt   # 含 Pillow，图片压缩依赖
```

跑 chat 相关单测示例：

```bash
conda activate dream-drama-env
python -m pytest tests/chat/test_model_catalog.py tests/chat/test_image_compress.py \
  tests/chat/test_turn_input.py tests/chat/test_openai_vision_parts.py \
  tests/chat/test_anthropic_vision_parts.py tests/chat/test_vision_gate.py \
  tests/chat/test_message_limits.py tests/chat/test_summarization_middleware.py \
  tests/core/test_tool_loop_guard.py tests/chat/test_tool_loop_guard_integration.py -q
```

## 工具循环护栏（tool_loop_exhausted）

每 turn 新建 [`TurnToolLoopGuard`](../app/core/turn/tool_loop_guard.py)。对 `recall_*`：**每 turn 至多一次**——[`runner._dedupe_tool_calls`](../app/chat/agent/runner.py) 在同一条 AIMessage 内只保留首个 recall；`pre_check` 在后续 turn 内调用时立即占位/短路。对 `list_*`：同 turn 内 success 且空结果连续 2 次后，第 3 次短路。Canvas `list_generate_models` 同 `kind` 重复成功调用同理。turn 继续，不触发 `tool_repeat_guard` 整轮熔断。

## SSE `StreamFrame` (protocol_version=2)

| type | Purpose |
|------|---------|
| `token` | `channel`: `answer` \| `think`, `text` |
| `tool_start` | `call_id`, `name`, `args` |
| `tool_end` | `call_id`, `name`, `ok`, `preview` |
| `heartbeat` | `ts` |
| `error` | `code`, `message` |
| `cancelled` | `reason` |
| `done` | `turn_id`, `message_ids` |
| `conversation_title` | `conversation_id`, `title`, `updated_at` (ISO8601, optional) |

## Persistence per turn

1. `USER`
2. `ASSISTANT` (`phase=tool_request`) when model returns tool_calls
3. `TOOL` per tool result (including `tool_error`)
4. `ASSISTANT` (`phase=final`) final answer
5. `ASSISTANT` (`phase=cancelled`) on user cancel / guard stop

## Vision multimodal (image attachments)

- Upload: raw image ≤ 10MB; compress to base64 ≤ 5,242,880 bytes before TOS storage.
- `HumanMessage.content`: text block + `image_ref` blocks (`attachment_id`, `mime_type`); checkpoint/DB payload stores refs only.
- Hydrate scope: only refs in current turn `hydrate_attachment_ids`; historical checkpoint refs are skipped.
- OpenAI adapter: `image_url` + data URL; Anthropic adapter: `image.source.base64` + `media_type`.
- `supports_vision` from union_lm `/api/v1/models` via `ModelCatalog` (startup best-effort cache; runtime miss → `模型能力信息暂时不可用，请稍后重试`).
- Frontend: disable image upload when `supports_vision=false`; switching to non-vision model shows hint without removing attachments.
