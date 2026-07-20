# 数据库迁移（P末执行）

开发期仅在仓库维护 DDL，**不要**对生产/远程库提前执行。

## 顺序（同一维护窗口）

1. 备份远程库
2. `psql "$DATABASE_URL" -f db/migrations/20250602_drop_supervisor_screenwriter.sql`
3. `psql "$DATABASE_URL" -f db/migrations/20250603_create_canvas.sql`
4. 若尚无 `generate_task`：`psql "$DATABASE_URL" -f scripts/migrate_generate_task.sql`
5. `psql "$DATABASE_URL" -f db/migrations/20250610_canvas_voice_id.sql`（canvas TTS `voice_id`）
6. 校验 `\dt`，冒烟应用

本地/CI 测试库可自行按序执行上述脚本。

```bash
conda activate dream-drama-env
```
