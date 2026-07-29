---
name: canvas_operations
description: Canvas node/edge mutations, HITL user-edits-win, revision CAS, node.data shape, and apply_canvas_arrange batch layout.
priority: 20
always_load: false
---

# Canvas operations

## Agent write tools

- `apply_canvas_patch` — exactly one `operation`: `create_node` | `update_node`
- `apply_canvas_edge_operation` — exactly one `operation`: `connect` | `disconnect` (not HITL)
- `apply_canvas_arrange` — batch position moves + optional connects in one call (not HITL)

No agent `delete_node`. Users delete via the HTTP patch API.

Node kinds: `text`, `image`, `video`, `audio` (envelope field is `kind`, not `type`).

Pass `operation` as a nested object. Do not stringify it as a JSON string.

## HITL (user edits win)

In manual mode, `apply_canvas_patch` pauses for confirm. Whatever the user confirms on the card is final (prompt, title, editable node fields).

- After confirm, treat ToolMessage / returned node snapshot as source of truth — not the earlier chat draft.
- Do not call `update_node` only to restore the pre-edit intent.
- Closing speech and later tool args must match confirmed content.

`apply_canvas_edge_operation` and `apply_canvas_arrange` execute without manual approval.

## Serial writes

In one assistant reply, at most one of `apply_canvas_patch`, `apply_canvas_arrange`, or `submit_node_generation`. Wait for the result before the next write.

## Revisions

- Each node and edge has its own `revision` (starts at `1` on create). No canvas-level revision.
- `update_node` requires `node.revision` from latest `query_canvas_nodes` or patch result.
- `disconnect` requires `expected_revision` on the edge.
- On `revision_conflict` for single-op patch/edge: re-query and retry that operation.
- For `apply_canvas_arrange`: the whole batch is all-or-nothing CAS. On conflict, re-query, refresh **all** revisions in the batch (or shrink to a new complete batch), and resubmit the full batch. Do not retry only a failed subset.

## Node data (`node.data`)

Business fields under structured `data` (snake_case):

- `title`, `prompt`, `content`, `prompt_content`, `config`, `model`
- **text**: `content` = body string; `prompt_content` = generation input segments; `prompt` derived from `prompt_content`
- **media**: `content` = structured prompt segments; `prompt` = derived plain text (do not write string `content`)
- Media segments use `asset_id` (not `assetId`)
- `status`, `generate_task_id`, `output_asset_ids`, `generate_error` are projection fields — do not invent them on create

## Node and edge IDs

- Server assigns node UUIDs on `create_node`. Use only ids from real tool results.
- Never copy UUIDs from skill examples.

### Create then connect (two tool calls)

1. `apply_canvas_patch` with one `create_node`.
2. Read returned `nodes[].id`.
3. `apply_canvas_edge_operation` with `connect` using those ids.

Ports:

- Text prompt: `source_port="output_text"` → `target_port="prompt_input"`
- Media reference: `source_port="output_asset"` → `target_port="reference_asset"`
- `edge_type="dependency"`

## `apply_canvas_arrange`

Use for tidying layout: many position moves and optional new connections in **one** call.

- Args: `moves` (`id`, `revision`, `position`) and/or `connections` (same shape as connect).
- Maps to multiple position-only `update_node` ops + optional `connect` ops; one `apply_patch(ops=...)`.
- Success returns the same shape as a canvas patch result (nodes/edges views). Not a partial success list.
- On `revision_conflict`, nothing in the batch applied — resubmit a full corrected batch after re-query.

## Examples

Create:

```json
{
  "operation": {
    "op": "create_node",
    "node": {
      "kind": "video",
      "position": { "x": 200, "y": 150 },
      "data": { "title": "夜店舞蹈", "prompt": "网红风格美女在夜店跳舞" }
    }
  }
}
```

Update:

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

Connect:

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
