# 类型化契约与依赖约束

## 依赖方向

主业务依赖方向固定为：

```text
server 各域 domain
  <- app/contracts
  <- server 各域应用 / agent（经 ports）
  <- server.api、外部集成
```

各域 `domain` 不得依赖 FastAPI、Tortoise、LangChain、网关客户端或业务 service。
禁止用关键词、正则或文本启发式路由用户意图；Tool / Turn 边界使用结构化类型，不得新增字符串协议。

## 契约生成

后端 Pydantic contract 是 HTTP、Canvas、Generation、Gateway 和 SSE 的唯一协议源。

```bash
make contracts
make contracts-check
```

生成物：

- `contracts/schema/*.json`

生成文件禁止手工修改。`make contracts-check` 会检查仓库内 JSON Schema 是否与
当前 Pydantic 模型一致。

## 跨仓库同步

前端仓库不执行本仓库的 Python 脚本，也不通过相对路径读取后端源码。协议变更按以下
顺序提交：

1. 在后端修改 Pydantic contract，并运行 `make contracts`。
2. 后端 CI 通过 `make contracts-check`，然后发布或确定一个不可变的后端 Git SHA。
3. 将该 SHA 对应的 `contracts/schema/*.json` 同步到前端仓库的 vendored contract 目录。
4. 在前端重新生成并检查 TypeScript declaration，再提交前端变更。

前端可以组合生成类型形成 UI 状态，但不得重复声明协议枚举。后端变更在对应前端
contract 更新可用之前不得部署；回滚也必须选择协议兼容的前后端镜像组合。

## 开发数据库重建

项目尚未上线，不保留 Canvas 旧字段、alias 或兼容 migration。重建开发库：

```bash
ENV=dev DATABASE_URL='postgresql://...' make db-reset
```

该命令会删除开发库的 `public` schema 和全部数据，再一次性执行最终 `db/schema.sql`。
命令会拒绝 `ENV=production`。

## CI 边界

后端 CI 独立运行 Python 3.12 编译、Ruff、mypy 边界检查、contract 漂移检查和
backend Docker 构建。前端测试与 TypeScript 构建由前端仓库负责，后端 CI 不安装
Node.js，也不访问前端工作区。当前 Ruff 阻断语法和无效控制流规则
`E9,F63,F7`；其余历史 lint 债务不得用自动全库修复混入拆仓提交。

## 类型检查边界

```bash
make type-check
```

当前 mypy 阻断各域 domain、Pydantic contract 和生成能力规则。
使用 `--follow-imports=skip`，避免旧 ORM、FastAPI middleware 和第三方框架类型债务污染新边界。
扩大检查范围时必须先清零对应模块错误，不通过全局 `ignore_errors` 制造虚假的全项目通过。
