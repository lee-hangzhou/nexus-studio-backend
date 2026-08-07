---
name: canvas_generation
description: list_generate_models param_options, submit_node_generation fields, resolve/merge refs, HITL confirm, and in-progress handling.
priority: 30
always_load: false
---

# Generation on canvas nodes

Use `submit_node_generation` only after the node exists. Use `node_id` from `query_canvas_nodes` or a patch result.

## `list_generate_models` (capability table)

Call before submitting when you need a real `model_id` or allowed parameters.

- **One kind per call:** `"image"`, `"video"`, or `"audio"` — never multiple kinds in one call.
- Need both image and video → two separate calls.
- At most one successful call per `kind` per user turn; duplicates return `tool_loop_exhausted`. Reuse a prior successful ToolMessage for that `kind` in this turn.
- Response `items[]` each include `model_id`, `label`, `kind`, and **`param_options`**:
  - `ratios`, `resolutions`, `counts`, `durations`
  - `reference_modes` (`value` + `label`)
  - `material_limits`, `ratios_by_resolution`
- `model_id` for submit **must** come from this list. Do not invent model ids.
- Choose `ratio` / `resolution` / `duration` / `count` / `reference_mode` only from that model's `param_options` (and only when needed / required).

## `submit_node_generation`

Required: `node_id`, `kind`, `prompt`, `model_id`.

Additional fields (flat, not a nested parameters bag):

- Image: `ratio`, `resolution`, `count`, `ref_asset_ids` as allowed by `param_options`
- Video: **`reference_mode` and `duration` are required**. Missing or out-of-range values fail submit validation. Values must be in `param_options`. Also `ratio` / `resolution` / `ref_asset_ids` when applicable.
- Audio: `voice_id` when known

Do not invent asset ids. Use real ids from resolve / query / tools.

## Resolve → merge prompt → submit refs in order

When a node consumes upstream nodes through dependency edges:

1. Call `resolve_node_inputs` first.
2. Read `local_prompt`, `upstream_texts[]`, and `refs[]` (`slot`, `label` like `图片1`, `asset_id`).
3. Write **one** fused `prompt` that incorporates upstream text and local intent. Do not copy `local_prompt` verbatim when upstream must drive the scene.
4. Pass `ref_asset_ids` in the **same order** as `refs` (ordered subsequence allowed; do not reorder).
5. Call `submit_node_generation` with that `prompt` and `ref_asset_ids`.

Do not write placeholder sentences like「根据上游提示词与参考图…」into the node's `data.prompt` as the final submit text. The fused prompt goes in `submit_node_generation.prompt`.

Multi-image: align character descriptions with ref slots (e.g. 图片1 = 男主, 图片2 = 女主).

## Node-local materials: `library_refs` and upload results

- `data.library_refs` on the **target node** are **asset-library** reference tags (writable via `apply_canvas_patch`). They are not projection fields.
- Node **upload** writes a result snapshot (`output_source=upload`, `output_asset_ids`) — it is **not** a `library_refs` entry.
- On submit, the target node's own `library_refs` are **auto-merged** into `ref_asset_ids`, ordered **after** connected refs — do **not** add those `asset_id`s yourself, or submit validation fails. If the fused `prompt` needs to reference one, describe it naturally in text.
- A node with upload/result snapshot exposes `output_asset_ids` to downstream `resolve_node_inputs.refs` when connected.

## Text → image → video

- Text node: user request / body in `data.content` (string).
- Media nodes: intent in `data.prompt` (optional segment `data.content`).
- Connect `text.output_text` → `image.prompt_input` only after both exist (see `canvas_operations`).
- Submit image with fused prompt and resolved refs.
- Connect `image.output_asset` → `video.reference_asset`; downstream may continue after the image asset is ready.

## HITL (user edits win)

In manual mode, `submit_node_generation` pauses for confirm. User edits on the card (prompt, model, ratio, duration, `reference_mode`, etc.) are the final submit values.

- Do not “correct” back to the original chat draft after confirm.
- Closing text must match the confirmed submit.

## `node_generation_in_progress`

If submit returns `node_generation_in_progress`, a generation is already running for that node. **Do not retry. Do not create a replacement node.** Tell the user generation is in progress. Use `list_node_generations` to inspect status.

## Example

```json
{
  "node_id": "<PASTE_node_id_FROM_query_or_patch>",
  "kind": "video",
  "prompt": "男主骑车带女主穿过霓虹街道，电影感光影",
  "model_id": "<from list_generate_models>",
  "duration": 5,
  "reference_mode": 3,
  "ref_asset_ids": [123, 456]
}
```

After submit, node `status` becomes `running` until the gateway callback updates assets. Progress arrives via SSE — do not poll the user.
