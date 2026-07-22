# Canvas patch operations

Supported ops: `create_node`, `update_node`, `delete_node`, `connect`, `disconnect`.

Node kinds: `text`, `image`, `video`, `audio`.

## Node and edge IDs (read this before `connect`)

- The server assigns node UUIDs on `create_node`. You cannot choose or guess them.
- Use **only** `id` values from a real `query_canvas_nodes` or `apply_canvas_patch` ToolMessage (`nodes[].id`, `edges[].source` / `target`).
- **Never** copy UUIDs from this skill doc, examples, or chat prose — they are placeholders only.
- **Never** use labels like `text-node`, `node_1`, or made-up hex strings.
- If the canvas is empty (`revision` 0, no nodes), run `create_node` first; you have no valid `source`/`target` until the tool returns ids.

### Create then connect (two tool calls)

`connect` in the **same** `apply_canvas_patch` as `create_node` will fail: new node ids exist only **after** the patch returns.

1. `apply_canvas_patch` with `create_node` only (or several creates).
2. Read returned `nodes[].id` (or `query_canvas_nodes`).
3. Second `apply_canvas_patch` with `connect` using those exact ids and fresh `expected_revision`.

Node data flow fields:

- `input_prompt`: user/model instruction for this node.
- `output_text`: text produced by a text node and consumable by downstream nodes.
- `output_asset_ids`: system asset ids produced by media generation.

Canvas nodes only use `input_prompt`, `output_text`, and `output_asset_ids`.

Dependency edges carry ports:

- Text prompt flow: `source_port="output_text"` to `target_port="prompt_input"`.
- Media reference flow: `source_port="output_asset"` to `target_port="reference_asset"`.
- Use `edge_type="dependency"` for data-flow dependencies.

Always pass `expected_revision` from the latest `query_canvas_nodes` or patch result.

## `connect` / `disconnect` (required shape)

For `connect`, **both endpoints go inside `edge`**. Field names are exactly `source` and `target` (node UUID strings). Do **not** use `source_node_id`, `target_node_id`, or top-level `source`/`target` without an `edge` object.

```json
{
  "expected_revision": 1,
  "ops": [
    {
      "op": "connect",
      "edge": {
        "source": "<PASTE_source_node_id_FROM_PRIOR_TOOL_JSON>",
        "target": "<PASTE_target_node_id_FROM_PRIOR_TOOL_JSON>",
        "source_port": "output_text",
        "target_port": "prompt_input",
        "edge_type": "dependency"
      }
    }
  ]
}
```

For `disconnect`, pass `edge_id` from `query_canvas_nodes` / prior patch (`edges[].id`):

```json
{
  "expected_revision": 2,
  "ops": [{ "op": "disconnect", "edge_id": "<PASTE_edge_id_FROM_PRIOR_TOOL_JSON>" }]
}
```

Wrong:

```json
{ "op": "connect", "source": "...", "target": "..." }
```

```json
{ "op": "connect", "edge": { "source_node_id": "...", "target_node_id": "..." } }
```

Using invented or documentation UUIDs (causes `badly formed hexadecimal UUID string` or missing nodes).

## Create node example

```json
{
  "expected_revision": 0,
  "ops": [
    {
      "op": "create_node",
      "node": {
        "kind": "video",
        "position": { "x": 200, "y": 150 },
        "title": "夜店舞蹈",
        "input_prompt": "网红风格美女在夜店跳舞"
      }
    }
  ]
}
```

On `revision_conflict`, re-query and retry with updated revision.
