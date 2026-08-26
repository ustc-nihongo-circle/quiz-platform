# 仓库结构与职责

最后更新：2026-08-25

## 根目录

- `README.md` 说明项目定位、状态和本地启动方法。
- `AGENTS.md` 是仓库级 Agent 规则入口。
- `CONTEXT.md` 统一领域术语。
- `pyproject.toml` 声明 Python 版本、运行依赖和验证工具。
- `.env.example` 只列配置名称和非敏感示例，不保存真实值。

## 程序与测试

- `src/config/` 保存 Django 配置、URL 和进程入口。
- `src/quiz/` 保存答题领域模型、身份保护、题库工具、答题服务、JSON 接口、参与者模板、静态资源和管理命令。
- `tests/` 保存单元、集成、并发和浏览器测试。
- `content/examples/` 只保存可公开的格式示例与退役题。

## 文档

- `docs/PROJECT_PLAN.md` 是当前认可的实施顺序和完成条件。
- `docs/research-handoff.md` 保存往届包的消敏证据和迁移边界。
- `docs/frontend-contract.md` 是参与者模板与后端接口之间的正式契约。
- `docs/adr/` 保存难以逆转且需要解释的长期决策。
- `docs/references/current-ui/` 保存不含个人数据的旧界面基线。

## 本机私密区域

- `_private/question-banks/` 保存现役题库和答案。
- `_private/legacy-reference/` 保存已移除个人数据的往届代码与素材参考。
- `_private/design-inputs/` 保存海报原稿和未完成许可审查的设计输入。
- `_private/deployment-local/` 保存服务器清单和本地部署备注，不保存可避免复制的私钥正文。
- `docs-agent/` 保存本机 Agent 状态和任务记录。

`_private/` 和 `docs-agent/` 均由 `.gitignore` 从仓库历史中排除。
