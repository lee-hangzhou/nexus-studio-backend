# Canvas patch operations

Agent tools (single op per call):

- `apply_canvas_patch` — exactly one `operation`: `create_node` | `update_node`
- `apply_canvas_edge_operation` — exactly one `operation`: `connect` | `disconnect`

There is **no** agent `delete_node`. Users delete via the HTTP patch API.

Node kinds: `text`, `image`, `video`, `audio` (envelope field is `kind`, not `type`).

## Revisions

- Each node and edge has its own `revision` (starts at `1` on create).
- There is **no** canvas-level / episode-level revision.
- `update_node` requires `node.revision` matching the entity from the latest `query_canvas_nodes` or patch result.
- `disconnect` requires `expected_revision` on the edge.
- On `revision_conflict`, re-query and retry with updated per-entity revisions.

## Node data (`node.data`)

Business fields live under structured `data` (not flat columns):

- `title`, `prompt`, `content`, `prompt_content`, `config`, `model`
- **text**：`content` = 正文(string)；`prompt_content` = 生成输入(segments)；`prompt` = 由 `prompt_content` 派生的纯文本
- **media**：`content` = 结构化 prompt(segments)；`prompt` = 派生纯文本（不要写 string content）
- `status`, `generate_task_id`, `output_asset_ids`, `generate_error` are system/projection fields — do not invent them on create

## Node and edge IDs

- The server assigns node UUIDs on `create_node`. You cannot choose or guess them.
- Use **only** `id` values from a real `query_canvas_nodes` or tool result.
- **Never** copy UUIDs from this skill doc or invent ids.

### Create then connect (two tool calls)

1. `apply_canvas_patch` with one `create_node`.
2. Read returned `nodes[].id`.
3. `apply_canvas_edge_operation` with `connect` using those exact ids.

Ports:

- Text prompt flow: `source_port="output_text"` → `target_port="prompt_input"`
- Media reference flow: `source_port="output_asset"` → `target_port="reference_asset"`
- `edge_type="dependency"`

## Create node example

```json
{
  "operation": {
    "op": "create_node",
    "node": {
      "kind": "video",
      "position": { "x": 200, "y": 150 },
      "data": {
        "title": "夜店舞蹈",
        "prompt": "网红风格美女在夜店跳舞"
      }
    }
  }
}
```

## Update node example

```json
{
  "operation": {
    "op": "update_node",
    "node": {
      "id": "<PASTE_node_id>",
      "revision": 3,
      "position": { "x": 10, "y": 20 },
      "data": { "prompt": "更新后的提示词" }
    }
  }
}
```

## Connect example

```json
{
  "operation": {
    "op": "connect",
    "edge": {
      "source": "<PASTE_source_node_id>",
      "target": "<PASTE_target_node_id>",
      "source_port": "output_text",
      "target_port": "prompt_input",
      "edge_type": "dependency"
    }
  }
}
```
