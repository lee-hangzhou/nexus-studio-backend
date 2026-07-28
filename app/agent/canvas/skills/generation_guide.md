# Generation on canvas nodes

Use `submit_node_generation` only after the node exists. Use `node_id` from `query_canvas_nodes` or the patch result.

Required fields: `node_id`, `kind` (`image` or `video`), `prompt`, `model_id`.

## `list_generate_models`

Call when you need a real `model_id`. **One tool call = one `kind`**: either `"image"` or `"video"`, never both in one call.

- Need image models → `{"kind": "image"}`
- Need video models → `{"kind": "video"}`
- Need both → **two separate** `list_generate_models` calls

Invalid (breaks JSON parsing): `{"kind":"image"}{"kind":"video"}` or any string with two JSON objects glued together.

Do not invent model ids.

## Resolve → merge prompt → submit refs in order

When a node consumes upstream nodes through dependency edges:

1. Call `resolve_node_inputs` first.
2. Read `local_prompt`, `upstream_texts[]`, and `refs[]` (each ref has `slot`, `label` like `图片1`, `asset_id`).
3. Write **one** fused `prompt` that incorporates upstream text and local intent. Do **not** copy `local_prompt` verbatim when upstream text must drive the scene.
4. Pass `ref_asset_ids` in the **same order** as `refs` from resolve (subset allowed only as an ordered subsequence — do not reorder).
5. Call `submit_node_generation` with that `prompt` and `ref_asset_ids`.

Do **not** write placeholder sentences like「根据上游提示词与参考图…」into the video/image node's `data.prompt` as the final submit text. The fused prompt goes in `submit_node_generation.prompt`, not as a lazy template on the node.

Multi-image workflows: align character descriptions in the prompt with ref slots (e.g. 图片1 = 男主, 图片2 = 女主).

For text → image → video workflows:

- Create a text node with the user's request / final prompt in `data.content` (string).
- Create media nodes with intent in `data.prompt` (and optional segment `data.content`).
- Connect `text.output_text` to `image.prompt_input` only after both nodes exist; use real `nodes[].id` from a prior patch (see `canvas_operations` — two-step create then connect via `apply_canvas_edge_operation`).
- Submit image generation with your fused prompt and resolved refs.
- Connect `image.output_asset` to `video.reference_asset`; the workflow runner continues downstream after the image asset is ready (fills missing `config.model` / `config.duration` from gateway model list when the video node omits them).
- Do not invent asset ids or signed URLs. Use real `data.output_asset_ids` returned by tools or node queries.

Example `submit_node_generation`:

```json
{
  "node_id": "<PASTE_node_id_FROM_query_or_patch>",
  "kind": "video",
  "prompt": "男主骑车带女主穿过霓虹街道，电影感光影",
  "model_id": "your-video-model-id",
  "ref_asset_ids": [123, 456]
}
```

Use `list_node_generations` to inspect task status tied to nodes.

After submit, node `status` becomes `running` until the gateway callback updates assets.

## `node_generation_in_progress`

If `submit_node_generation` returns `node_generation_in_progress`, the node already has a generation task in progress (for example user-triggered from the canvas UI). **Do not retry. Do not create a replacement node.** Tell the user clearly that generation is already in progress for that node. To check progress, call `list_node_generations`.
