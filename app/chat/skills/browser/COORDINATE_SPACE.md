# 坐标系 — css_viewport

## 机制工具统一坐标系

`browser_challenge_read_geometry`、`browser_challenge_screenshot_element`、`browser_challenge_dispatch_pointer_trace` 一律使用 **`css_viewport`**：

- 原点：当前 viewport 左上角。
- 单位：CSS 像素（与 Playwright `bounding_box()` / `page.mouse` 一致）。
- `dispatch_pointer_trace` 的 `x`/`y` 必须是 viewport 绝对坐标，不是元素内局部坐标。

## 图像工具坐标系

`cv_*` 工具在 **图像像素索引** 上工作（左上角为 (0,0)）。将 CV 结果映射到页面坐标时：

1. 用 `browser_challenge_screenshot_element` 返回的 `bbox_page` 作为图像在 viewport 中的锚点。
2. 缺口 x（图像像素）+ `bbox_page.x` → 目标 viewport x（还需加上 handle 中心偏移等几何换算）。

## 已废弃

- `image_local_normalized`、gate 内截图合成、preflight 归一化轨迹 — **不得再使用**。
