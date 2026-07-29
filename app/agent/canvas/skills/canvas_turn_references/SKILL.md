---
name: canvas_turn_references
description: Turn References block — inline vs materials pointers, when to get_asset / inspect_turn_media / query_canvas_nodes.
priority: 15
always_load: false
---

# Turn references

## What you receive

- **User message:** typed text (and placeholders). Skill chips resolve separately; selected skill bodies are injected when pinned.
- **Turn References** block (only when this turn has attachments):
  - `inline` — assets/nodes in the input area
  - `materials` — assets/nodes on the materials bar
  - Each asset line: `origin`, `asset_id`, `media_type` — pointer only, no url/body
  - Each `node_id` — canvas node id string

## Rules

- Intent comes from the user message text. References are pointers for grounding and tool use.
- Do not infer actions from `media_type` alone (an image is not automatically “use as generation ref”).
- Do not auto-fetch everything. Call `get_asset` when you need url / mime / filename / text.
- Visual understanding (`image` / `video`): call `inspect_turn_media` with allowlisted `asset_id`s. Do not use `get_asset` as a substitute for seeing pixels.
- Do not auto-query nodes. Call `query_canvas_nodes` when you need layout, `data`, revisions, or edges.
- Check both `inline` and `materials` for ids in scope this turn.
- Never invent `origin`, `asset_id`, node ids, or URLs outside this list.

## Typical flows

| Need | Tool |
|------|------|
| See / describe turn image or video | `inspect_turn_media` |
| Reverse-engineer prompt | `inspect_turn_media` with `task=reverse_prompt` |
| Asset metadata | `get_asset` |
| Node snapshot / data | `query_canvas_nodes` |

Mutation and generation protocols live in `canvas_operations` / `canvas_generation`.
