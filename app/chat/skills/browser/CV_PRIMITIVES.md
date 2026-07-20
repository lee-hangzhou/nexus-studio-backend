# CV 原语

闭式图像子问题，**不是**整站 solver。

## 工具

| 工具 | 输入 | 输出 |
|------|------|------|
| `cv_image_info` | workspace 图片路径 | width, height, channels |
| `cv_match_template` | image_path, template_path, 可选 roi | dx, dy, confidence |
| `cv_find_gap_x` | background_path + piece_path，或单图 image_path | candidates[{x, score}], confidence |

## 使用原则

- 坐标系：图像 **像素索引**；映射到页面见 [COORDINATE_SPACE.md](COORDINATE_SPACE.md)。
- `confidence` 由模型判断；低于阈值时工具返回 `cv_low_confidence`，**不得**强行 dispatch。
- **捅到底滑块**：优先用 `browser_challenge_read_geometry` 的 travel 几何解，不必先走 CV。
- **缺口拼图**：通常需要截图 + `cv_find_gap_x` 或 `cv_match_template`。

## 禁止

- 封装 `solve_xxx_captcha()` 或绑定特定站点。
- 把开源 demo 通过率当作 SLA。
