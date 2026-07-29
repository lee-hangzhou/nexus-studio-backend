# Chat Browser Session 镜像

每个会话一个容器，内含两个常驻进程：

1. **Playwright run-server**（CDP，端口 3333）— 浏览器实例
2. **Session driver**（HTTP，端口 3334）— 与 `inprocess_session` 相同的心智模型：**单连接、单 context、单 page**，所有 exec / capture / gate 都走 driver，不再每次 `docker exec` 新建 CDP 连接

本地 Host 通过仅绑定 `127.0.0.1` 的随机端口调用 driver API；容器化 backend
通过共享 Docker 网络和稳定容器名调用。driver 不暴露到宿主机公网，也不再使用
URL pin / `active_page_url`。

## 构建（改 driver 后必须重建）

```bash
make docker-browser-build
# 或
docker build -t nexus-studio-browser-session:latest -f deploy/browser-session/Dockerfile deploy/browser-session
```

与沙箱一并构建：

```bash
make docker-images-build
```

## 环境变量（见 `.env` / `deploy/.env.prod`）

| 变量 | 说明 |
|------|------|
| `CHAT_BROWSER_ENABLED` | 是否注册浏览器工具 |
| `CHAT_BROWSER_INPROCESS` | **生产与本地联调均为 `false`** |
| `CHAT_BROWSER_IMAGE` | 默认 `nexus-studio-browser-session:latest` |
| `CHAT_BROWSER_DOCKER_NETWORK` | 容器网络，默认 `bridge` |
| `CHAT_BROWSER_RUN_SERVER_PORT` | run-server 端口，默认 `3333` |
| `CHAT_BROWSER_DRIVER_PORT` | session driver 端口，默认 `3334` |
| `CHAT_WORKSPACE_ROOT` | 会话工作区宿主机路径，挂载进 browser 容器 |

## 自检

```bash
docker images nexus-studio-browser-session
# 触发一次 browser 任务后
docker ps | grep nexus-studio-browser-
# 容器内 driver 健康检查（host_port 以 docker port 为准）
curl -s "http://127.0.0.1:<driver_host_port>/health"
```

## Driver API: `POST /v1/exec`

Request body: `{"code": "<async python source>"}`

Response JSON (required fields):

| Field | Type | Description |
|-------|------|-------------|
| `rc` | int | `0` success; non-zero failure |
| `stdout` | string | Captured stdout from user script |
| `stderr` | string | Captured stderr / traceback on failure |

Host parses this via `app/chat/browser/exec_result.py` — `rc` must be present and typed as int (never inferred with truthiness).
