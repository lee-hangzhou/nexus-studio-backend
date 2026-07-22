# 滑块 / 缺口 / 行为验证 — 浏览器内自动化

在活页面上用机制工具 + 自写脚本完成 **read → compute → write → verify** 循环。详见 [WRITE_VERIFY.md](WRITE_VERIFY.md)。

## WHEN TO USE

- Auth 链内或已登录会话上的交互式 challenge（滑块、缺口、拖拽轨道）。
- After inspect 产出 C/T/H selector、`scope_selector`（登录/验证模态根）、success/failure/panel probe selector。

## WHEN NOT TO USE

- 匿名态在任务 URL 上遇 WAF/滑块 → 先完成 auth（gate），勿用本循环代替登录。
- 未 verify 就再次 `dispatch_pointer_trace`。

## 范式（非站点步骤表）

1. **Read** — inspect → `browser_challenge_read_geometry`（含 `scale`, `resolved_index`）→ 按需 `screenshot_element`
2. **Compute** — `execute_python` / `cv_*`；位移用 `scale.css_per_intrinsic_x`（左界对左界），见 [COORDINATE_SPACE.md](COORDINATE_SPACE.md)
3. **Write** — `browser_challenge_dispatch_pointer_trace`
4. **Verify** — `browser_challenge_wait_probe` → 读 `outcome` + `observations`
5. **失败** — 读 `error_type`（`challenge_outcome_*`）；见 [FAILURE_POLICY.md](FAILURE_POLICY.md)；未 verify 禁止再次 write

## Parameters

- `scope_selector` — 来自 inspect 的 login/challenge 模态根，避免 strict mode 双节点。
- `success_selector` / `failure_selector` / `panel_selector` / `retry_text_probe` — 均由 inspect 提供，机制只观察。

## 相关文档

- [WRITE_VERIFY.md](WRITE_VERIFY.md)
- [COORDINATE_SPACE.md](COORDINATE_SPACE.md)
- [CV_PRIMITIVES.md](CV_PRIMITIVES.md)
- [POINTER_TRACE.md](POINTER_TRACE.md)

## 禁止

- 用 `confirm` 或 `image_captcha` 代替滑块。
- 写死像素 magic 或站点 crack。
- 混用 `image_local_normalized` 与 `css_viewport`。
