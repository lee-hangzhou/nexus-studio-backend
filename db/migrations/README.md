# 数据库迁移

开发期仅在仓库维护 DDL，**不要**对生产/远程库提前执行。所有命令必须使用
`-v ON_ERROR_STOP=1`，禁止忽略中间 DDL 失败后继续执行。

## 顺序（同一维护窗口）

1. 备份目标数据库并停止应用写入
2. `20250602_drop_supervisor_screenwriter.sql`
3. `20250603_create_canvas.sql`
4. 若尚无 `generate_task`：`scripts/migrate_generate_task.sql`
5. `20250604_canvas_assets_workflow.sql`
6. `20250606_add_query_indexes.sql`
7. `20250610_canvas_voice_id.sql`
8. `20260720_generate_task_logical_delete.sql`
9. `20260725_project_episode_covers.sql`
10. 执行结构与数据不变量校验，再启动应用

`20260725_project_episode_covers.sql` 是单事务、可重复执行的升级：

- 为存量项目创建第 1 集
- 将旧 Canvas `project_id` 按项目映射为 `episode_id`
- 保留 Canvas revision、节点、边、消息和操作数据
- 移除旧 Canvas 数据库关联约束，关联由代码事务维护
- 发现无法解析的项目 owner 或孤儿 Canvas 数据时整个回滚

本地/CI 测试库可按序执行上述脚本。生产数据库必须另行获得明确授权。

```bash
conda activate dream-drama-env
```
