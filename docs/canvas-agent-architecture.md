# 画布 Agent 架构方案

> dream-drama 无限画布 + 画布 Agent 的目标架构。  
> 对齐 LangGraph / langmem 官方记忆实践，复用现有 Chat Agent 基础设施，适配 FastAPI 单体部署。

**版本**：v1 · 2026-06

---

## 目录

1. [背景与目标](#1-背景与目标)
2. [设计原则](#2-设计原则)
3. [系统边界](#3-系统边界)
4. [总体架构](#4-总体架构)
5. [存储与状态](#5-存储与状态)
6. [短期记忆：SummarizationNode](#6-短期记忆summarizationnode)
7. [长期记忆：Memory Store](#7-长期记忆memory-store)
8. [Agent 主循环](#8-agent-主循环)
9. [工具设计](#9-工具设计)
10. [执行模式：auto / manual](#10-执行模式auto--manual)
11. [前后端协议](#11-前后端协议)
12. [与 Chat / Generate / 编剧 的关系](#12-与-chat--generate--编剧-的关系)
13. [配置项](#13-配置项)
14. [可观测与熔断](#14-可观测与熔断)
15. [分阶段落地](#15-分阶段落地)
16. [明确不做](#16-明确不做)
17. [附录](#17-附录)

---

## 1. 背景与目标

### 1.1 产品定位

无限画布是短剧创作的**可视化工作区**：节点表示文本、生图、生视频、音频等任务；边表示依赖与数据流。右侧 Agent 面板用于自然语言驱动画布变更与生成任务。

### 1.2 架构目标


| 目标             | 说明                                                                                               |
| -------------- | ------------------------------------------------------------------------------------------------ |
| **服务端权威**      | 画布以 **行级表**（`canvas_nodes` / `canvas_edges`）为准；`revision` 在 project 元数据行；前端只消费 API 组装的 snapshot  |
| **Agent 范式**   | ReAct loop，模型动态选工具；禁止 turn 内意图路由                                                                 |
| **记忆分层**       | 短期：SummarizationNode；长期：PostgresStore + pgvector                                                 |
| **复用 Chat 基建** | checkpoint、SSE StreamFrame、ToolResult、guards、runner、**持久化边界**（业务表权威 + Saver 自管 checkpoint，不绑同事务） |
| **生成闭环**       | 画布节点生成复用 `generate_task` + union_lm 网关                                                           |


### 1.3 非目标

- 不拆 Go/Python 服务
- Phase 1 不上 Milvus 独立知识库
- 画布 Agent 为 ReAct + 工具；编剧 Screenwriter 预定义 Workflow **已从代码库移除**

---

## 2. 设计原则

### 2.1 Agent 心智模型（与 `.cursor/rules/chat-agent.mdc` 一致）

- **判断归模型**：调哪个工具、是否拉 snapshot、是否 recall 记忆 — 由工具 description + 静态 system policy 决定。
- **机制归系统**：工具怎么执行、revision 怎么校验、步数上限、熔断、`error_type` 枚举。
- **说明书，不是决策树**：不在 orchestrator 里写「遇到 X 走 A」的业务分支。
- **业务表唯一权威**：画布节点/边/消息/操作审计以 Tortoise 业务表为准；checkpoint 为 **可重建的派生态**（续跑、interrupt），允许与业务表短暂不一致或丢失（丢失则重跑 turn，**不影响**已落库的画布与 Feed）。
- **事务只包业务**：同库业务写用 `in_transaction()`；**不**魔改 `AsyncPostgresSaver`、不把 checkpoint 与业务表绑在同一 PG 事务。
- **契约优于约定**：工具返回统一 `ToolResult` JSON；禁止字符串前缀协议。

### 2.2 记忆原则（LangGraph / langmem 官方）


| 层次   | 机制                  | 官方组件                                                        |
| ---- | ------------------- | ----------------------------------------------------------- |
| 会话续跑 | thread 级 checkpoint | `AsyncPostgresSaver`                                        |
| 短期压缩 | 超阈值滚动摘要，非丢弃         | `SummarizationNode` + `RunningSummary`                      |
| 长期持久 | 跨 session 语义检索      | `AsyncPostgresStore` + pgvector                             |
| 长期写入 | 热路径工具 + 冷路径后台提取     | `create_manage_memory_tool` + `create_memory_store_manager` |


> Chat 模块当前使用 `history_trim`（尾部裁剪）是**尚未对齐官方实践**的实现；画布 Agent **从设计起采用 SummarizationNode**。后续 Chat 可同构迁移。

---

## 3. 系统边界

```
┌─────────────────────────────────────────────────────────────────┐
│                         React 前端                               │
│  React Flow 画布  │  Agent 侧栏 Feed  │  节点属性 / 手动编辑      │
└────────────┬────────────────────────┬───────────────────────────┘
             │ REST + SSE              │
             ▼                         │
┌────────────────────────────────────────────────────────────────┐
│                      FastAPI（单体）                              │
│  ┌──────────────┐  ┌─────────────────┐  ┌──────────────────┐  │
│  │ Canvas API   │  │ canvas_agent    │  │ GenerateService  │  │
│  │ (权威状态)    │  │ turn/orchestrator│  │ (generate_task)  │  │
│  └──────┬───────┘  └────────┬────────┘  └────────┬─────────┘  │
│         │                   │                     │             │
│         └───────────────────┼─────────────────────┘             │
│                             ▼                                   │
│                    app/server/<domain>/services/*               │
└────────────┬───────────────────────┬───────────────────────────┘
             │                       │
             ▼                       ▼
      PostgreSQL                 union_lm
      ├─ canvas_* 业务表          (生成网关)
      ├─ generate_task
      ├─ LangGraph checkpoints
      └─ LangGraph store (pgvector)
```

**约束：**

- 前端不直连 LLM；所有 Agent 输出经 SSE 推送。
- Agent 不持有画布权威副本；变更必须经 Canvas API 落库后，以 `canvas_patch` 事件通知前端。
- 用户手动画布与 Agent 写画布走**同一条 patch 管线**（同一 revision 语义）。

---

## 4. 总体架构

### 4.1 模块划分（建议目录）

```
app/canvas/
  agent/
    factory.py          # build_canvas_agent
    state.py            # CanvasAgentState（messages + context + running_summary）
    graph.py            # StateGraph：summarize → model → tools
  turn/
    orchestrator.py     # stream_canvas_turn
    lock.py             # CanvasTurnLock（Redis，同 Chat conversation_turn_lock）
    persistence.py      # canvas_messages 写入（同 Chat TurnPersistence 模式）
    guards.py           # 复用或扩展 Chat TurnGuards
    memory_background.py # turn 结束 create_memory_store_manager
  tools/
    canvas_read.py      # query_canvas_nodes
    canvas_write.py     # apply_canvas_patch
    generation.py       # submit_node_generation
  memory/
    summarization.py    # SummarizationNode 配置与 State 键
    store.py            # PostgresStore 初始化、namespace 工具
    schemas.py          # CanvasMemory Pydantic schema
  skills/
    canvas_agent.md
    canvas_operations.md
    generation_guide.md
  api/
    routes.py           # FastAPI router
```

### 4.2 LangGraph 拓扑

```
START
  → summarize（SummarizationNode）
  → agent（LLM，bind tools）
  → tools（ToolNode，执行后回 agent）
  → … 循环 …
  → END

turn 结束后（orchestrator 层，图外）：
  → memory_background.ainvoke（不阻塞 SSE done）
```

与 Chat 的 `create_agent` 单图不同，画布 Agent 显式拆 **summarize 节点**，以便：

- `messages` 保留完整 thread（含 ToolMessage），供 checkpoint 与 UI 对齐；
- `summarized_messages` / `context` 单独喂模型，符合 langmem 官方推荐。

---

## 5. 存储与状态

### 5.1 三类存储


| 存储                 | 用途                              | 管理者                                       |
| ------------------ | ------------------------------- | ----------------------------------------- |
| **业务表** `canvas_`* | 画布 graph、产品消息、操作审计              | Tortoise ORM / service                    |
| **Checkpointer**   | Agent thread state、interrupt 断点 | LangGraph `AsyncPostgresSaver`            |
| **Memory Store**   | 跨 session 长期记忆                  | LangGraph `AsyncPostgresStore` + pgvector |


### 5.2 业务表设计

#### 5.2.1 禁止：整图 `graph_json` 读改写回

**不采用**「单表 JSONB 存 `{ nodes, edges }`，每次 patch 读出整块、改完、写回」——与 Phase 无关，**上线第一天就不做**。

单用户、单画布、上千节点时，改一个字段若走全量 RMW：


| 操作  | 典型代价（量级）                                              |
| --- | ----------------------------------------------------- |
| 读   | 从 PG 拉整图 JSONB（可能数 MB，TOAST/网络/反序列化）                  |
| 写   | 整列写回 + WAL + 索引维护；行级 `SELECT FOR UPDATE` 锁整行元数据       |
| 频率  | 拖拽改 position、生成回调改 `status`、Agent 多次 patch — **每次全量** |


例如 1000 节点、每节点 ~~2KB  payload → 图 ~2MB；**改 1 个节点的 prompt** 仍可能触发 **~~2MB 读 + ~2MB 写**；一天几百次操作就是 GB 级无效 IO，且延迟随节点数线性恶化。

**权威存储必须是行级：** 改哪个节点/边，就只 `INSERT`/`UPDATE`/`DELETE` 对应行；project 级 `revision` 仅作 CAS 版本号，不承载图数据。

> 说明：前端当前 React Flow 内存结构**不是** schema 依据；API 层 `CanvasSnapshot` 由查询组装，前端按契约渲染即可。

#### 5.2.2 `canvas_project_meta`（project 级元数据，无图数据）

```sql
CREATE TABLE canvas_project_meta (
  project_id       BIGINT PRIMARY KEY REFERENCES projects(id),
  revision         BIGINT NOT NULL DEFAULT 0,
  node_count       INT NOT NULL DEFAULT 0,   -- 可选冗余，供 TurnFacts；由触发器或事务内维护
  edge_count       INT NOT NULL DEFAULT 0,
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

- 一行 per project；patch 成功时 `**revision++**`（在同事务内、CAS 校验之后）。
- **不存** `graph_json`。

#### 5.2.3 `canvas_nodes`（权威：节点）

```sql
CREATE TABLE canvas_nodes (
  id               UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL REFERENCES projects(id),
  kind             VARCHAR(16) NOT NULL,     -- text | image | video | audio
  position_x       DOUBLE PRECISION NOT NULL,
  position_y       DOUBLE PRECISION NOT NULL,
  title            VARCHAR(512) NOT NULL DEFAULT '',
  summary          TEXT NOT NULL DEFAULT '',
  prompt           TEXT NOT NULL DEFAULT '',
  status           VARCHAR(16) NOT NULL DEFAULT 'idle',
  model_id         VARCHAR(128),
  ratio            VARCHAR(16),
  duration_sec     INT,
  resolution       VARCHAR(16),
  task_id          BIGINT,                   -- FK generate_task.id，可空
  asset_keys       JSONB,                    -- TOS keys 等，小字段可 JSONB
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at       TIMESTAMPTZ              -- 软删；查询默认 deleted_at IS NULL
);

CREATE INDEX idx_canvas_nodes_project_alive
  ON canvas_nodes (project_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_canvas_nodes_project_status
  ON canvas_nodes (project_id, status) WHERE deleted_at IS NULL;
```

- `id`：**服务端** `uuid4()` 生成；Agent/客户端 create 不得指定已有 id。
- `update_node`：`UPDATE canvas_nodes SET ... WHERE id = $1 AND project_id = $2` — **O(1) 行 IO**。
- 生成回调改 status：只更新该节点行，**不碰**其余 999 行。

#### 5.2.4 `canvas_edges`（权威：边）

```sql
CREATE TABLE canvas_edges (
  id               UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL REFERENCES projects(id),
  source_node_id   UUID NOT NULL REFERENCES canvas_nodes(id),
  target_node_id   UUID NOT NULL REFERENCES canvas_nodes(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  deleted_at       TIMESTAMPTZ
);

CREATE INDEX idx_canvas_edges_project_alive
  ON canvas_edges (project_id) WHERE deleted_at IS NULL;
CREATE INDEX idx_canvas_edges_source
  ON canvas_edges (project_id, source_node_id) WHERE deleted_at IS NULL;
```

`delete_node`：同事务内软删节点 + 关联边。

#### 5.2.5 API 层 `CanvasSnapshot`（组装视图，非存储真相）

`GET /api/v1/canvas/{project_id}` 在 service 层查询 `canvas_nodes` + `canvas_edges`，组装为：

```json
{
  "revision": 42,
  "nodes": [ { "id": "...", "kind": "image", "position": { "x": 0, "y": 0 }, ... } ],
  "edges": [ { "id": "...", "source": "...", "target": "..." } ]
}
```

- **首次进画布**可能一次拉全量（节点很多时仍是一次读多行，但是 **索引行读**，不是反序列化 2MB 单 JSON；后续可做分页/视口加载，与 patch 路径无关）。
- **增量**：SSE `canvas_patch` 只带 **本次变更的 node/edge delta**，禁止推送整图。

#### `canvas_messages`

侧栏 Feed / 对话历史（**用户可见层**，不含完整 ToolMessage 正文）。

```sql
CREATE TABLE canvas_messages (
  id               BIGSERIAL PRIMARY KEY,
  project_id       BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  role             SMALLINT NOT NULL,       -- 对齐 ChatMessageRole
  content          TEXT NOT NULL,
  metadata         JSONB,                   -- tool_steps 摘要、client_turn_id、phase
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX idx_canvas_messages_project ON canvas_messages (project_id, created_at);
```

#### `canvas_operations`（审计 + 幂等）

```sql
CREATE TABLE canvas_operations (
  op_id            UUID PRIMARY KEY,
  project_id       BIGINT NOT NULL,
  user_id          BIGINT NOT NULL,
  turn_id          VARCHAR(64),
  op_type          VARCHAR(32) NOT NULL,    -- create_node | update_node | ...
  payload          JSONB NOT NULL,
  status           VARCHAR(16) NOT NULL,    -- pending | applied | rejected | failed
  revision_before  BIGINT,
  revision_after   BIGINT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

### 5.3 消息与 Agent 状态（两套存储，独立提交）

与 Chat 相同：**两轨逻辑分工不同，不在同一事务里提交**。

```
用户发一条消息
  │
  ├──→ canvas_messages（业务轨，Tortoise）
  │      Feed / 历史 / 审计；含 client_turn_id 幂等
  │
  └──→ LangGraph graph 运行
         AsyncPostgresSaver 在每个 super-step 后自行 commit checkpoint
         供续跑、SummarizationNode、interrupt
```


|      | checkpoint 四表                 | canvas_messages + canvas_* 图数据 |
| ---- | ----------------------------- | ------------------------------ |
| 权威级别 | **派生**（可丢、可重建）                | **唯一权威**                       |
| 用途   | Agent loop 续跑、interrupt 恢复    | 用户可见历史、画布真相、revision           |
| 丢失影响 | 重跑当前 turn 或新 thread；已落库画布不受影响 | 不可丢（须 fail loud + 业务事务）        |


**不建** `canvas_turns` 等平行状态机；运行/中断/续跑交给 checkpoint，并发交给 Redis 锁（§8.4），幂等交给 `canvas_messages.metadata`（§5.6.1、§5.6.3）。

### 5.4 Checkpointer

- **thread_id**：`canvas-{project_id}`（常量前缀 `CANVAS_CHECKPOINT_THREAD_PREFIX`）
- 与 Chat 共用同一 `AsyncPostgresSaver` 实例与 PG 四表，靠 thread_id 隔离。
- **写入**：LangGraph **原生 Saver 自管**提交；不绑外部连接、不 deferred flush、不自定义 `CanvasCheckpointWriter`。
- **interrupt / resume**：状态在 checkpoint 内；`resume` 为同 `thread_id` 的新 HTTP 请求，与 Chat 一致。

### 5.5 画布状态：工具查询，非 system prompt 注入

**不在每轮 system prompt 里注入 `canvas_skeleton` 或节点列表。** 原因：

- 节点一多就占 context，且每轮重复、易与 DB 权威状态不同步；
- 「当前画布长什么样」依赖语义理解，应由模型决定何时查、查多细，而不是后端每轮全量塞入。

改为提供读工具 `**query_canvas_nodes*`*（见 §9.1），模型按需调用。  
写操作所需的 `**revision**` 由机制层以标量 fact 传入（见 §8.1 `CanvasTurnFacts.revision`），不附带节点明细。

### 5.6 持久化边界（简化）

**明确放弃**：业务表 + checkpoint **同事务**、`TurnUnitOfWork`、`M0~M3` 里程碑、受控/deferred checkpoint、`canvas_turns` 表。原因：魔改 Saver 成本高、两套状态对账易出 bug；Chat 已验证「业务顺序写 + Saver 自管」足够。

#### 5.6.1 职责分工（不另造状态机）


| 需求                           | 机制                                                                        |
| ---------------------------- | ------------------------------------------------------------------------- |
| 运行中 / interrupt / 续跑         | LangGraph checkpoint 四表（`thread_id = canvas-{project_id}`）                |
| 同一 episode 同时只有一个 Agent turn | Redis `canvas_turn_lock`（§8.4）                                           |
| 重复提交同一用户消息                   | `canvas_messages.metadata.client_turn_id` 唯一约束或先查后插                       |
| 画布 patch 原子性                 | `in_transaction()` + 行级表 + `revision` CAS（§5.6.2）                         |
| Feed / 列表 / 画布真相             | `canvas_messages` + `canvas_nodes` / `canvas_edges` / `canvas_operations` |


#### 5.6.2 画布 patch 的事务（行级 L1，保留）

`apply_canvas_patch` 工具内 — **禁止** 读/写整图 JSON：

```python
async with in_transaction():
    meta = await CanvasProjectMeta.filter(project_id=pid).select_for_update().first()
    if meta.revision != expected_revision:
        raise RevisionConflict

    changed_nodes: list[CanvasNode] = []
    changed_edges: list[CanvasEdge] = []

    for op in ops:
        match op.op:
            case "create_node":
                row = await CanvasNodes.create(...)  # INSERT 一行
                changed_nodes.append(row)
            case "update_node":
                n = await CanvasNodes.filter(id=op.node_id, project_id=pid).select_for_update().first()
                if not n:
                    raise InvalidNodeId
                apply_fields(n, op.patch)  # UPDATE 仅变更列
                await n.save()
                changed_nodes.append(n)
            case "delete_node":
                await soft_delete_node_and_edges(op.node_id, pid)
            case "connect" | "disconnect":
                ...  # INSERT/软删 canvas_edges 单行

    meta.revision += 1
    await meta.save(update_fields=["revision", "updated_at", "node_count", ...])
    await CanvasOperations.create(..., revision_after=meta.revision)

    return PatchResult(revision=meta.revision, nodes=changed_nodes, edges=changed_edges)
```

- 单节点 `update_node`：**1 行** meta 锁 + **1 行** node UPDATE + revision 递增；IO 与全图节点数**无关**。
- `generate_task` 创建 + `canvas_nodes.task_id` / `status` 回写：同一 L1 事务，只 UPDATE 目标节点行。
- SSE `canvas_patch`  payload 用 `PatchResult` 增量，不序列化全图。

#### 5.6.3 `canvas_messages` 与 orchestrator 写入顺序

对齐 Chat `TurnPersistence` 模式（**无** UoW）：

1. **turn 开始**：`persist_user_message` → `canvas_messages`（`metadata.turn_id`、`client_turn_id`）；再 `agent.astream(...)`，Saver 自行写入 checkpoint。
2. **loop 中**：按事件 persist tool_request / tool 摘要 / final assistant（业务表）；checkpoint 由 graph 步进自动更新。
3. **幂等**：同一 `(project_id, client_turn_id)` 重复 POST → 返回已有 turn 结果或 409，不重复插用户消息。

**checkpoint 与 messages 短暂不一致**（例如进程在写消息后、下一步 checkpoint 前崩溃）：允许；恢复时以 **业务表 Feed + 画布行级数据** 为准，必要时 **重跑** 未完成 turn（用户可见「请重试」），**不**用 checkpoint 反写业务表。

**禁止**：为对齐全网 checkpoint 而维护 `canvas_turns` / `sync_seq` / 双轨对账任务（除非日后实测有强需求再单独立项）。

#### 5.6.4 失败语义


| 场景                             | 行为                                                        |
| ------------------------------ | --------------------------------------------------------- |
| `canvas_messages` / patch 事务失败 | 业务回滚；SSE `error`；checkpoint 可能已写入更早 super-step（可接受）       |
| patch `revision_conflict`      | 仅 L1 回滚；ToolMessage 结构化错误                                 |
| checkpoint 损坏 / 丢失             | 新 turn 或清 thread 重跑；**已提交**的 `canvas_nodes` / messages 不变 |
| 业务写成功但 turn 中途崩溃               | Feed 可见已持久化消息；画布以行级表为准；Agent 侧可重试                         |


---

## 6. 短期记忆：SummarizationNode

### 6.1 为何不用 history_trim

`history_trim`（strategy=`last`）在超 budget 时**直接丢弃**旧消息。对长会话：

- 早期用户约束、已确认的设计决策可能永久丢失；
- 模型无法通过 recall 补偿（若用户尚未触发长期记忆写入）。

官方推荐用 **SummarizationNode** 将旧消息**压缩为恒定上限的摘要**，而非丢弃。

### 6.2 State 字段

```python
class CanvasAgentState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]
    context: dict[str, Any]  # RunningSummary，SummarizationNode 读写
    # 私有：summarized_messages 由 summarize 节点产出，仅 model 节点消费
```

参考 [langmem summarization guide](https://langchain-ai.github.io/langmem/guides/summarization/)：

- UI / `canvas_messages` 渲染**完整** messages 历史；
- 模型输入 = `[摘要消息] + 最近未摘要原文`；
- `RunningSummary` 记录上次压缩位置，滚动更新，不重复压缩已摘要内容。

### 6.3 参数（可配置）


| 参数                          | 建议默认                    | 说明                  |
| --------------------------- | ----------------------- | ------------------- |
| `max_tokens_before_summary` | `context_budget * 0.6`  | 超过则触发摘要             |
| `max_summary_tokens`        | 2048                    | 摘要本身上限，控制总量不随对话线性增长 |
| `max_tokens`                | `context_budget * 0.85` | 摘要 + 保留原文的总预算       |


### 6.4 与 tool 结果截断的关系

保留 Chat 已有机制：**单条 ToolMessage 超过 8000 字先截断并标 `[已截断]`**，再进入 SummarizationNode。这是物理约束，不是语义决策。

摘要后若 tool 成对完整性受影响，summarize 节点输出前执行 `sanitize_tool_pairs`（复用 `app/chat/memory/message_validate.py`）。

### 6.5 被摘要稀释的信息

早期细节可能只在摘要里保留梗概。补偿路径：

1. 长期记忆 **recall 工具**（模型自主决定何时搜）；
2. 必要时 `**query_canvas_nodes`** 拉当前画布事实（模型自决 detail 档位）；
3. 冷路径 **memory_store_manager** 在 turn 结束时提取关键决策入库。

---

## 7. 长期记忆：Memory Store

### 7.1 基础设施

- **Store**：`AsyncPostgresStore`（与 checkpoint 同 PG，开启 **pgvector**）
- **Embedding**：经 union_lm 或项目统一 embedding 网关（与 Chat 检索若共用则同配置）
- **编译 graph 时**：`graph.compile(checkpointer=..., store=store)`

### 7.2 Namespace 设计


| Namespace 模板                                       | 策略         | 内容                             |
| -------------------------------------------------- | ---------- | ------------------------------ |
| `("canvas_memories", "{user_id}", "{project_id}")` | Collection | 项目级设计决策、节点风格、重要操作              |
| `("canvas_memories", "{user_id}")`                 | Profile    | 用户跨项目偏好（固定 key `profile`，覆盖更新） |


### 7.3 记忆 Schema

```python
class CanvasMemory(BaseModel):
    subject: str      # 如 "用户"、"P1主角造型"
    predicate: str    # 如 "偏好风格"、"已确认方案"
    object: str       # 如 "赛博朋克低饱和"
    context: str      # 适用范围，避免误当全局偏好
```

`context` 字段解决裸字符串脱离对话后语义模糊的问题。

### 7.4 两条写入路径（官方 Stateful Integration，均保留）

#### 热路径：Memory Tools

```python
manage_memory = create_manage_memory_tool(
    namespace=("canvas_memories", "{user_id}", "{project_id}"),
    schema=CanvasMemory,
)

recall_project_memory = create_search_memory_tool(
    namespace=("canvas_memories", "{user_id}", "{project_id}"),
)

recall_user_memory = create_search_memory_tool(
    namespace=("canvas_memories", "{user_id}"),
)
```

- 模型在 loop 内**自主**决定何时 create/update/delete/search；
- 适用：用户明说「记住」、提及「按之前风格」、识别记忆过时。

#### 冷路径：Store Manager（turn 结束后台）

```python
memory_manager = create_memory_store_manager(
    model=summarization_model,  # 或专用 extract 模型
    store=store,
    namespace=("canvas_memories", "{user_id}", "{project_id}"),
    schemas=[CanvasMemory],
    enable_inserts=True,
    enable_updates=True,
    enable_deletes=True,
)
```

- turn 正常结束（`terminated_by=completed`）后 `asyncio.create_task(memory_manager.ainvoke(...))`；
- **不阻塞** SSE `done`；
- 兜底：用户未触发 manage 工具但对话中出现值得记住的决策。

#### 重复写入治理（工程要求，非砍掉任一路径）


| 措施                       | 说明                                             |
| ------------------------ | ---------------------------------------------- |
| 同一 Store + schema        | 两路径写同一 namespace，由 langmem `enable_updates` 合并 |
| 日志 `memory_write_source` | `tool_manage` / `background_extract`           |
| 可选 content hash          | 后台提取前查近邻，跳过高度重复条目                              |
| 可观测                      | Store search 按 key 可追溯写入时间                     |


### 7.5 召回场景


| 场景                     | 路径                            |
| ---------------------- | ----------------------------- |
| session 内早期信息被摘要压缩     | 模型调 `recall_project_memory`   |
| 新 session 打开同一 project | system policy 提示可 recall；模型自决 |
| 跨项目用户偏好                | `recall_user_memory`          |


**代码不做触发判断**（不用关键词匹配用户输入来决定是否检索）。

---

## 8. Agent 主循环

### 8.1 每轮输入构成

**System Prompt（静态顺序）：**

```
1. skills/*.md（全量）
2. project meta（projects 表：name, tone, style, config）
3. 静态 policy 段（工具使用边界、manual 模式说明、revision / patch 规则）
```

**不注入**：节点列表、skeleton、graph_json 片段。画布现状通过 `**query_canvas_nodes`** 工具获取。

**Messages（动态）：**

```
1. [摘要]（SummarizationNode 产出，超阈值才有）
2. [最近 N 条原文 + ToolMessage]
3. [当前用户消息]
```

**Facts（机制层标量，非节点明细）：**

```python
@dataclass
class CanvasTurnFacts:
    revision: int                    # apply_canvas_patch 必填，来自 canvas_project_meta
    node_count: int                  # 仅计数，不展开
    mode: Literal["auto", "manual"]
    enable_tools: bool
    attachments_not_ready: bool
    pending_generation_count: int
```

`revision` / `node_count` 是 orchestrator 读库得到的**客观事实**，不是替模型做的「该看哪些节点」判断。

### 8.2 orchestrator 流程

```
stream_canvas_turn(episode_id, user_message, model_key, mode, client_turn_id, ...)
  │
  ├─ 1. Redis acquire canvas_turn_lock（跨 worker）
  ├─ 2. 幂等检查 client_turn_id（canvas_messages.metadata）
  ├─ 3. 读 canvas_episode_meta.revision、node_count 等标量 facts
  ├─ 4. persist_user_message → canvas_messages（业务事务或单条写入）
  ├─ 5. build_canvas_agent(checkpointer=AsyncPostgresSaver, store, tools)
  ├─ 6. run_agent_turn_stream
  │      ├─ LangGraph 每 super-step：Saver 自管 checkpoint
  │      └─ orchestrator：按事件写 canvas_messages 摘要 / SSE token·tool_*
  ├─ 7. finalize assistant → canvas_messages；SSE done
  ├─ 8. touch project.updated_at
  ├─ 9. 若 completed → background memory_manager.ainvoke（Store，独立事务）
  └─ 10. finally: Redis release lock（value == turn_id）
```

**注意：** `apply_canvas_patch` / `submit_node_generation` 在工具内走 §5.6.2 行级 `in_transaction()`，与 messages/checkpoint 写入 **解耦**。SSE 在对应 **业务行已 commit** 后推送（patch 的 `canvas_patch`、消息的 token 等同理）。

### 8.3 与 Chat orchestrator 复用


| 组件                                  | 复用方式                                                                       |
| ----------------------------------- | -------------------------------------------------------------------------- |
| `run_agent_turn_stream`             | 直接复用或薄封装                                                                   |
| `StreamFrame` / SSE encoder         | 直接复用                                                                       |
| `ToolResult` / `parse_tool_message` | 直接复用，扩展 canvas 专用 `error_type`                                             |
| `TurnGuards`                        | 复用，配置独立常量                                                                  |
| `**CanvasTurnLock`**                | **同构** `app/chat/turn/lock.py` 的 `ConversationTurnLock`（Redis `SET NX EX`） |
| `GatewayChatModel`                  | 复用；画布 graph 用 SummarizationNode 替代 model 内 trim                            |
| `PromptComposer`                    | 参考，canvas 独立 composer                                                      |


### 8.4 并发控制：Redis turn 锁 + revision CAS

#### 8.4.1 问题：`asyncio.Lock` 不够用

`asyncio.Lock` **仅单进程有效**。`uvicorn --workers > 1` 或多容器时，同一 `project_id` 的两个 turn 若落到不同 worker，进程内锁互不可见，会并发跑 Agent、并发写 checkpoint / 画布。

配置项 `CANVAS_TURN_LOCK_TTL_SEC` 的语义是 **分布式锁 TTL**，应对齐 Redis 实现，而不是 asyncio。

#### 8.4.2 现有基建（Chat 已落地）

Chat turn 已用 Redis 分布式锁，画布 **直接同模式**，不另造轮子：

```python
# app/chat/turn/lock.py（现状）
class ConversationTurnLock:
    def _key(self, conversation_id: int) -> str:
        return f"chat:turn_lock:{conversation_id}"

    async def acquire(self, conversation_id: int, turn_id: str) -> None:
        ok = await redis_client.set(
            self._key(conversation_id),
            turn_id,
            ex=settings.CHAT_TURN_LOCK_TTL_SEC,
            nx=True,
        )
        if not ok:
            raise AppError(ErrorCode.CONVERSATION_BUSY, ...)

    async def release(self, conversation_id: int, turn_id: str) -> None:
        key = self._key(conversation_id)
        if await redis_client.get(key) == turn_id:
            await redis_client.delete(key)
```

画布对应实现 `app/canvas/turn/lock.py`：


| 项        | Chat                               | Canvas                          |
| -------- | ---------------------------------- | ------------------------------- |
| Key      | `chat:turn_lock:{conversation_id}` | `canvas:turn_lock:{episode_id}` |
| TTL 配置   | `CHAT_TURN_LOCK_TTL_SEC`           | `CANVAS_TURN_LOCK_TTL_SEC`      |
| busy 错误码 | `CONVERSATION_BUSY`                | `CANVAS_EPISODE_BUSY`           |
| 实例       | `conversation_turn_lock`           | `canvas_turn_lock`              |


**Redis 客户端**：复用 `app/core/redis.py` 的 `redis_client`（生产 `deploy/.env.prod` 已配 `REDIS_URL`）。

#### 8.4.3 两层防护（锁 + CAS，职责不同）


| 机制                          | 粒度                                          | 作用                                               | 跨 worker |
| --------------------------- | ------------------------------------------- | ------------------------------------------------ | -------- |
| **Redis project turn lock** | 每个 project 同时仅 **一个 Agent turn**            | 串行 Agent loop，避免双 turn 交叉写 checkpoint / 工具       | ✅        |
| **revision CAS**            | 每次 patch 前校验 `canvas_project_meta.revision` | 挡住 Agent 写 vs 用户手动画布 PATCH vs 生成回调改节点 status 的并发 | ✅（DB 层）  |


两者 **重叠但不冗余**：

- 仅有 CAS、无 turn 锁：两个 Agent turn 可同时跑，工具与 checkpoint 交叉；CAS 挡不住双 Agent loop。
- 仅有 turn 锁、无 CAS：同一 turn 内够用，但 **用户手动画布** 或 **generate 回调** 更新节点仍可能与 Agent patch 竞态，靠 revision CAS + L1 事务回滚。

#### 8.4.4 生命周期与 TTL

```
POST /canvas/episodes/{episode_id}/turn
  → canvas_turn_lock.acquire(episode_id, turn_id)   # 失败 → 409 CANVAS_EPISODE_BUSY + active_turn_id
  → try: ... orchestrator ...
  → finally: canvas_turn_lock.release(episode_id, turn_id)

cancel / 异常 / worker 崩溃：
  → TTL（CANVAS_TURN_LOCK_TTL_SEC，默认 1800s）到期自动释放，避免死锁
  → 可选：force_cancel(episode_id) 供用户「停止生成」API（同 Chat force_cancel）
```

**续期**：若 turn 超过 TTL 仍在跑（长工具链），orchestrator heartbeat 路径可 `EXPIRE` 续期（与 Chat 若已有续期逻辑对齐；若无，Phase 3 补）。

#### 8.4.5 `apply_canvas_patch` 不再用 `asyncio.Lock`

同一 project 在 turn 锁持有期间 **只有一个 Agent turn**，写类工具在 runner 内 **串行** 执行，patch 工具内 **不需要** 再套 `asyncio.Lock`。

并发 patch 场景：


| 来源                               | 处理方式                                        |
| -------------------------------- | ------------------------------------------- |
| 同一 turn 内多次 `apply_canvas_patch` | runner 串行 + 每次 revision CAS                 |
| Agent turn vs 用户 REST PATCH      | revision CAS；冲突返回 409 / `revision_conflict` |
| 生成完成回调更新节点                       | 独立 L1 事务 + revision CAS（不占 turn 锁）          |


#### 8.4.6 API 与前端

- turn 进行中再次 POST turn → **409**，body 含 `active_turn_id`，前端提示「画布 Agent 正在执行」。
- 用户手动画布 PATCH **不抢 turn 锁**（产品允许边聊边改），仅 revision CAS；Agent 侧通过 `query_canvas_nodes` 看到最新 revision。

---

## 9. 工具设计

所有工具返回 `ToolResult` JSON（与 Chat 一致）。新增 canvas 专用 `error_type`：


| error_type          | 含义                             |
| ------------------- | ------------------------------ |
| `revision_conflict` | patch 时 revision 不匹配           |
| `invalid_node_id`   | 节点不存在                          |
| `invalid_patch`     | patch schema 校验失败              |
| `generation_failed` | generate_task 提交失败             |
| `canvas_locked`     | 写锁超时（保留；若仅用 turn 锁 + CAS，可不触发） |


### 9.1 读工具（可并发；manual 模式跳过确认）

#### `query_canvas_nodes`

查询画布节点与边的情况；**替代原 canvas_skeleton 每轮注入**。

**参数：**


| 参数              | 类型                          | 说明                                     |
| --------------- | --------------------------- | -------------------------------------- |
| `node_ids`      | `list[str] | null`          | 只查指定节点；空则按 filter 列出                   |
| `kind`          | `str | null`                | 过滤：text / image / video / audio        |
| `status`        | `str | null`                | 过滤：idle / running / success / failed   |
| `detail`        | `summary | standard | full` | 返回粒度，默认 `standard`                     |
| `include_edges` | `bool`                      | 是否返回边（默认 `false`；`full` 或改图前建议 `true`） |


**返回（始终含）：** `revision`、匹配节点数、各 status 计数。

**detail 档位：**


| 档位         | 每节点字段                                                             |
| ---------- | ----------------------------------------------------------------- |
| `summary`  | id, kind, status, title                                           |
| `standard` | + summary, position, task_id, prompt 前 200 字, model/ratio 等 chips |
| `full`     | + 完整 prompt、asset_keys、resolution/duration 等全部 data               |


**docstring 要点（写给模型）：**

- 用户提到具体节点、要改图、要生成前，先调用本工具确认现状；
- 仅寒暄或与画布无关时可不调用；
- 小范围修改用 `node_ids` + `standard`；大规模重组或 patch 前用 `full` + `include_edges=true`；
- 返回的 `revision` 用于后续 `apply_canvas_patch` 的 `expected_revision`。

**实现：** SQL 查询 `canvas_nodes` / `canvas_edges`（`WHERE project_id` + 可选 `id IN (...)` / `kind` / `status`）；**不**加载整图到内存再过滤。`full` 档位仍受单条 ToolMessage 8000 字上限，超出则标 `[已截断]` 并建议缩小 `node_ids`。

#### `list_node_generations`

- **何时**：查某节点或 project 下 generate_task 状态。
- **实现**：封装 `GenerateService`，不重复任务逻辑。

### 9.2 写工具（manual 模式需 confirm）

#### `apply_canvas_patch`

统一 mutation 入口，支持：

```json
{
  "ops": [
    { "op": "create_node", "node": { ... } },
    { "op": "update_node", "node_id": "...", "patch": { ... } },
    { "op": "delete_node", "node_id": "..." },
    { "op": "connect", "edge": { "source": "...", "target": "..." } },
    { "op": "disconnect", "edge_id": "..." }
  ],
  "expected_revision": 42
}
```

执行路径：

```
LLM 调用
  → (manual) interrupt → tool_pending
  → confirm 后分配 op_id
  → CanvasService.apply_patch（revision CAS + L1 事务；无 asyncio.Lock，见 §8.4）
  → 成功：revision++，SSE canvas_patch
  → 失败：ToolResult.fail(revision_conflict, ...)
```

节点 `id` 由**服务端**在 create 时分配 UUID，模型不应自造 id（patch 校验拒绝非 UUID 格式的 create 除外服务端生成）。

#### `submit_node_generation`

- 对指定 `node_id` 创建 `generate_task`，更新节点 `task_id` + `status=running`。
- 参数：kind、prompt、model_id、ratio、resolution、duration、ref_attachment_ids 等，与创作页 submit 对齐。
- 完成后由后台任务/网关回调更新节点行，并推送 SSE `**generation_progress`**（及必要时 `**canvas_patch**`）；前端不轮询写库。

### 9.3 记忆工具（langmem 预置）


| 工具                      | 实现                                            |
| ----------------------- | --------------------------------------------- |
| `manage_memory`         | `create_manage_memory_tool`                   |
| `recall_project_memory` | `create_search_memory_tool`，project namespace |
| `recall_user_memory`    | `create_search_memory_tool`，user namespace    |


### 9.4 Skill（非工具）

`app/canvas/skills/*.md` 启动时读取，全量注入 system prompt：

- 画布 Agent 行为准则
- 节点类型与 patch 规范
- 生成任务参数约定
- 何时应用 recall / **query_canvas_nodes**（说明书式，非分支代码）

---

## 10. 执行模式：auto / manual

由请求参数 `mode: auto | manual` 传入，**不从用户输入推断**。

### 10.1 auto（默认）

```
LLM → tool call → 直接执行 → ToolMessage → 继续 loop
```

### 10.2 manual

```
LLM → tool call（写类）
  → LangGraph interrupt
  → SSE tool_pending { tool_name, summary, call_id }
  → 等待 POST /canvas/{project_id}/turn/resume

用户 confirm：
  → resume(action=confirm) → 执行工具 → 继续 loop

用户 reject：
  → resume(action=reject) → ToolMessage(success=false, 用户拒绝) → LLM 自决下一步
```

**需确认工具列表**（配置项，非代码语义分支）：

```python
CANVAS_MANUAL_CONFIRM_TOOLS = (
    "apply_canvas_patch",
    "submit_node_generation",
    "manage_memory",  # 可选，看产品
)
```

读类工具（`query_canvas_nodes`、`recall_*`、`list_node_generations`）始终 auto 执行。

interrupt 期间不占写锁；`op_id` 在 confirm 后生成。

---

## 11. 前后端协议

### 11.1 REST


| 方法    | 路径                                        | 说明                          |
| ----- | ----------------------------------------- | --------------------------- |
| GET   | `/api/v1/canvas/{project_id}`             | snapshot + revision         |
| PATCH | `/api/v1/canvas/{project_id}`             | 用户手动画布 patch（同 revision 语义） |
| GET   | `/api/v1/canvas/{project_id}/messages`    | Feed 历史分页                   |
| POST  | `/api/v1/canvas/{project_id}/turn`        | 发起 Agent turn               |
| POST  | `/api/v1/canvas/{project_id}/turn/resume` | manual 模式 confirm/reject    |
| POST  | `/api/v1/canvas/{project_id}/turn/cancel` | 取消进行中的 turn                 |


**Turn 请求体：**

```json
{
  "content": "把主角立绘改成赛博朋克风",
  "model_key": "default",
  "mode": "auto",
  "client_turn_id": "uuid",
  "enable_tools": true
}
```

**Resume 请求体：**

```json
{
  "client_turn_id": "uuid",
  "action": "confirm",
  "tool_call_id": "call_xxx"
}
```

### 11.2 SSE（StreamFrame v2 扩展）

复用 `docs/chat-stream-agent.md` 基础帧，新增：


| type                  | 字段                                    | 说明                      |
| --------------------- | ------------------------------------- | ----------------------- |
| `canvas_patch`        | `revision`, `nodes`, `edges`, `op_id` | 本次已提交的行级 delta（**非**整图） |
| `tool_pending`        | `call_id`, `name`, `summary`          | manual 待确认              |
| `generation_progress` | `node_id`, `task_id`, `status`        | 可选，任务状态变更               |


既有帧：`token`, `tool_start`, `tool_end`, `heartbeat`, `error`, `cancelled`, `done`。

CoT / think：**不透传**到 Feed（与 Chat 一致）；若调试需要可走独立 channel。

### 11.3 前端数据流

```
1. 进入画布页 → GET canvas snapshot → 渲染 React Flow
2. 用户发消息 → POST turn → 订阅 SSE
3. 收到 canvas_patch / generation_progress → 按 node/edge id 合并 delta，更新 revision（权威，不轮询写库）
4. 节点工具栏「生成」→ POST .../nodes/{id}/generate 轻量 SSE（不走完整 turn）
5. 收到 token / tool_* → 更新 Feed
6. 用户拖拽改节点 → PATCH canvas（带 expected_revision）
7. revision 冲突 → 提示刷新或自动 re-fetch snapshot
8. getTaskStatus 仅可选只读预览，禁止用轮询结果 PATCH 节点
```

节点 id 必须使用服务端 UUID，禁止客户端自增 `n-image-101`。

---

## 12. 与 Chat / Generate 的关系


| 模块           | 关系                                                                                                                           |
| ------------ | ---------------------------------------------------------------------------------------------------------------------------- |
| **Chat**     | 同构 Agent 范式；不同 thread_id、tools、skills；记忆 namespace 隔离；Chat 短期记忆后续应对齐 SummarizationNode                                       |
| **Generate** | 共用 `generate_task` 表与 `GenerateService`；画布节点是任务的 UI 锚点；进度经 SSE，非前端轮询写节点                                                      |
| **projects** | 画布绑定 `project_id`；Supervisor / Screenwriter 栈与 `sessions` 等表已拆除（见 `db/migrations/20250602_drop_supervisor_screenwriter.sql`） |


---

## 13. 配置项

```python
# app/core/config.py 建议新增

CANVAS_CHECKPOINT_THREAD_PREFIX: str = "canvas"

CANVAS_CONTEXT_BUDGET: int = 120_000
CANVAS_MAX_TOKENS_BEFORE_SUMMARY: int = 72_000   # 或 budget * ratio
CANVAS_MAX_SUMMARY_TOKENS: int = 2_048
CANVAS_MAX_TOOL_CALLS: int = 80
CANVAS_TURN_WALL_CLOCK_SEC: int = 600
CANVAS_TOOL_REPEAT_GUARD: int = 3
CANVAS_TURN_LOCK_TTL_SEC: int = 1800   # Redis SET NX EX，同 CHAT_TURN_LOCK_TTL_SEC 语义

CANVAS_MANUAL_CONFIRM_TOOLS: str = "apply_canvas_patch,submit_node_generation"

# Memory Store（可与 CHECKPOINTER 同 URI，不同表由框架管理）
CANVAS_MEMORY_STORE_ENABLED: bool = True
CANVAS_MEMORY_EMBEDDING_MODEL: str = "..."
```

---

## 14. 可观测与熔断

### 14.1 Turn 级日志

```
canvas.turn.start   project_id, mode, model_key, revision, enable_tools
canvas.turn.lock    action=acquire|release|busy, project_id, turn_id
canvas.turn.done    project_id, turn_id, terminated_by, tool_calls_count
canvas.turn.empty   answer_chars, think_chars
```

### 14.2 Tool 级日志

```
canvas.tool  tool_name, error_type, duration_ms, result_chars
```

### 14.3 Memory 级日志

```
canvas.memory.write  source=tool_manage|background_extract, namespace, key
canvas.memory.search query_chars, hits
canvas.summarize     triggered=true|false, summary_tokens, messages_summarized
```

### 14.4 Guards

与 Chat 相同语义：

- `max_tool_calls` 总量上限
- `tool_repeat_guard` 单工具同 error_type 重复失败上限
- `wall_clock` 熔断
- 均基于 `error_type` 枚举，不用字符串前缀

---

## 15. 分阶段落地

### Phase 0 — 画布权威状态

- `canvas_project_meta` + `canvas_nodes` + `canvas_edges` + migration
- `CanvasService`：snapshot 组装、行级 patch、revision CAS
- 验收：单节点 update 仅触发该行 UPDATE（可 SQL/日志断言），不读写整图 JSONB
- 前端：按 snapshot API 重写持久化（不以现有 `useCanvasState` 为准）
- 验收：刷新后画布一致

### Phase 1 — Agent 读写（无长期记忆）

- `canvas_messages` + turn API + SSE
- LangGraph：`summarize` + agent + tools
- 工具：`query_canvas_nodes`、`apply_canvas_patch`
- SummarizationNode 接入 + 单测（摘要后 tool 成对）
- `CanvasTurnLock`（Redis，复用 Chat lock 模式）+ `CANVAS_EPISODE_BUSY`
- patch 工具 L1 行级事务（revision CAS + operations + delta SSE）
- `client_turn_id` 幂等（messages.metadata）
- 验收：checkpoint 不可用时，已落库画布与 Feed 仍正确；可重试 turn
- 验收：自然语言创建/修改节点，DB revision 与前端一致

### Phase 2 — 生成闭环

- `submit_node_generation` ↔ `generate_task`
- 节点 status 与创作页一致
- `generation_progress` + `canvas_patch` SSE（权威）；禁止前端轮询写节点
- **必做** `POST /canvas/{project_id}/nodes/{node_id}/generate` 轻量快捷入口
- 验收：同一 task_id 在创作页与画布节点均可查

### Phase 3 — manual + 长期记忆

- interrupt + resume API
- `AsyncPostgresStore` + pgvector setup
- manage / recall 工具 + background memory_manager
- 重复写入日志与 merge 验证
- 验收：manual 拒绝后 LLM 可调整；跨 session recall 命中

### Phase 4 — 体验与 Chat 对齐

- 项目标题生成（可选，复用 conversation_title 模式）
- Chat 迁移 SummarizationNode（独立任务，与画布同构）
- checkpoint retention 策略

---

## 16. 明确不做


| 项                                                 | 原因                                            |
| ------------------------------------------------- | --------------------------------------------- |
| Go/Python 服务拆分                                    | 单体 FastAPI 已满足                                |
| Milvus 独立知识库（早期）                                  | Skill 全量 + 后期按需                               |
| turn 内意图路由 / 关键词分支                                | 违反 Agent 规范                                   |
| history_trim 作为画布默认短期记忆                           | 官方推荐 SummarizationNode                        |
| 砍掉长期记忆任一路径                                        | 官方 Stateful 热+冷互补，靠 merge 治理                  |
| 画布状态仅存在浏览器                                        | 无权威、无协作                                       |
| Feed 与 Graph 两套互不同步的真相                            | 产品体验虚假                                        |
| 画布状态每轮注入 system prompt（skeleton / 节点列表）           | 占 context、易过期；改由 `query_canvas_nodes` 按需查     |
| **整图 `graph_json` JSONB 读改写回**                    | 节点规模上来后 IO 与锁灾难；权威在行级表                        |
| **业务表 + checkpoint 同事务**                          | 魔改 Saver；对账复杂；画布与 Chat 同构即可                   |
| `**canvas_turns` / `TurnUnitOfWork` / M0~M3 里程碑** | 平行状态机；职责已由 Redis 锁 + checkpoint + messages 覆盖 |
| 编剧 Workflow 节点迁入画布                                | 产品边界不同                                        |


---

## 17. 附录

### 17.1 thread_id 与 namespace 隔离


| 资源                | Chat                      | Canvas                |
| ----------------- | ------------------------- | --------------------- |
| checkpoint thread | `chat-{conversation_id}`  | `canvas-{project_id}` |
| 产品消息表             | `chat_messages`           | `canvas_messages`     |
| 长期记忆 namespace    | 暂不启用 / 未来 `chat_memories` | `canvas_memories`     |


### 17.2 参考

- [LangMem — Summarization](https://langchain-ai.github.io/langmem/guides/summarization/)
- [LangGraph — Add memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
- [LangMem — Core Concepts](https://langchain-ai.github.io/langmem/concepts/conceptual_guide)
- 项目内：`docs/chat-stream-agent.md`、`.cursor/rules/chat-agent.mdc`

### 17.3 架构验收清单

1. 刷新页面后画布与 Feed 从 DB 一致恢复。
2. Agent patch 后 `revision` 单调递增；409 时整笔 L1 回滚，模型收到 `revision_conflict`。
3. patch / messages 业务事务失败：revision 不变或消息未插入；不依赖 checkpoint 回滚业务。
4. 长对话触发摘要后，context 总量不超过配置上限。
5. turn 结束后台 memory 写入可日志追溯（独立事务，不参与 L2）。
6. 任意用户自然语言输入，后端日志无 intent 枚举字段。
7. manual 模式下写工具未 confirm 前 `canvas_nodes` / `canvas_edges` 无变更。
8. 同一 project 双 turn 跨 worker：后发起者收到 409，无并发写 checkpoint/画布。
9. 生成任务与创作页共用 `generate_task`；task 创建与节点回写在同一 L1 事务。

*文档维护：实现阶段每完成一个 Phase 更新对应 checklist。*