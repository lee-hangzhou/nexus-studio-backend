---
name: canvas_workflow
description: Canvas layout, query protocol, tool overview, inspect split, and serial-write rules for short-form drama graph work.
priority: 10
always_load: true
---

# Canvas workflow

You operate on an infinite workflow canvas for short-form drama production.

## Query before acting

- Call `query_canvas_nodes` when layout, revisions, edges, or node `data` matter.
- Scan with `detail=summary` first (id / kind / title / status / revision).
- Then fetch targets with `node_ids` + `detail=full`. Prefer not dumping the whole graph as full.
- Need edges → `include_edges=true`.
- Node UUIDs only from tool JSON (`nodes[].id`). Never invent ids or copy examples.

## Tools (canvas surface)

| Need | Tool |
|------|------|
| Read graph | `query_canvas_nodes` |
| Create / update node | `apply_canvas_patch` (exactly one operation) |
| Connect / disconnect | `apply_canvas_edge_operation` |
| Batch layout moves (+ optional connects) | `apply_canvas_arrange` |
| List models + param_options | `list_generate_models` |
| Submit generation | `submit_node_generation` |
| Upstream texts / refs | `resolve_node_inputs` |
| Turn-attachment vision | `inspect_turn_media` |
| Node output vision | `inspect_node_media` |
| Asset metadata | `get_asset` / `list_assets` |

There is **no** agent `delete_node`. Users delete via HTTP patch.

## Inspect split

- `inspect_turn_media` — only asset ids allowlisted from this turn's Turn References.
- `inspect_node_media` — visual content of a node's `data.output_asset_ids` on this episode.

Do not use one as a substitute for the other.

## Serial writes

In a single assistant reply, emit at most one of: `apply_canvas_patch`, `apply_canvas_arrange`, or `submit_node_generation`. Wait for its ToolMessage, then continue. Do not parallelize those writes in one `tool_calls` batch.

## Memory

- Prefer injected `## Memory` blocks; call `recall_*` only for ids or when injection is insufficient.
- `manage_user_memory` — cross-session preferences; `manage_project_memory` — project-scoped facts.
- Memory is low authority: current user message and live canvas/tool facts override memory.

## Where to look next

Already inlined this turn: `canvas_workflow`, `canvas_response_style` (and `canvas_turn_references` when Turn References exist).

Call `read_canvas_skill` for protocol details before write/generation tools:

- Node / edge / HITL / arrange → `canvas_operations`
- Generation / param_options → `canvas_generation`
