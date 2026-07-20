# Phase H 手工验收清单

运行环境：`conda activate dream-drama-env`

## 前置

1. 执行 DB 迁移：`psql ... -f scripts/migrate_drop_chat_retrieval_pipeline.sql`
2. 重建 sandbox 镜像：`docker build -t dream-drama-chat-sandbox:latest -f deploy/chat-sandbox/Dockerfile deploy/chat-sandbox`
3. 重启后端（SkillRegistry 启动时加载）

## 1. 正向（文件 / docx）

- [ ] 上传 `.docx` 附件
- [ ] turn_input manifest 仅含 `attachments/...` + `mime=...`，无 sidecar txt 引导
- [ ] 模型先 `read_file skills/docx/SKILL.md`
- [ ] 再 `execute_python` 按 skill 调用 scripts
- [ ] 最后 `publish_file` 交付 docx（非 txt）

## 2. 负面（progressive disclosure — 门禁）

- [ ] 同一 docx，用户只说「改这段文字」，不提 read skill
- [ ] **期望**：模型仍先 read skill 的 SKILL.md
- [ ] 若跳过 read skill → 修订 `core_policy.md` / skill 索引 / turn_input 前置条件，重测直到稳定

## 3. 非文件（通用 agent）

- [ ] 无附件，prompt：「用 execute_python 算 1..100 平方和，写入 output/result.txt 再 publish」
- [ ] 验证原语链不依赖附件/manifest

## 单测（自动化）

```bash
conda activate dream-drama-env
python -m pytest tests/chat/test_skill_registry.py tests/chat/test_workspace_session.py tests/chat/test_file_ops_guards.py tests/chat/test_turn_input.py -q
```
