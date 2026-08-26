# USTC 日语协会游园会答题系统

本仓库把往届 Django 答题包重建为可安全运行、可测试、可部署并能在换届后继续维护的正式项目。

当前已完成首个参与者纵向里程碑：活动届次、参与者身份保护、版本化题库、抽题、服务端计时、幂等提交、分类最高分、参与者 JSON 接口和参与者网页已经建立。管理后台和部署流程尚未实现。

## 当前技术基线

- Python 3.12 至 3.14
- Django 5.2 LTS
- PostgreSQL 作为正式开发和部署数据库
- pytest、pytest-django 和 Ruff 用于验证
- Excel 作为社团成员编辑题库的入口，后续由导入命令验证并写入数据库

Django 5.2 是长期支持版本，官方安全支持持续到 2028 年 4 月。项目按 5.2 系列最新补丁升级，不锁死某个旧补丁版本。

## 本地启动

PowerShell 示例：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"

$env:DJANGO_DEBUG = "1"
$env:DJANGO_SECRET_KEY = "local-only-change-me"
$env:DJANGO_USE_SQLITE = "1"

# 仅示意变量名。请生成三项独立随机值，不要复制示例密钥到正式环境。
$env:QUIZ_IDENTITY_KEYS = '{"v1":"<base64-encoded-32-byte-key>"}'
$env:QUIZ_IDENTITY_ACTIVE_KEY_ID = "v1"
$env:QUIZ_IDENTITY_HMAC_KEY = "<base64-encoded-32-byte-key>"

python manage.py check
python manage.py migrate
python manage.py runserver
```

浏览器打开 `http://127.0.0.1:8000/participant/` 使用参与者页面。根路径会重定向到该页面。

进程健康检查位于 `http://127.0.0.1:8000/health/`，预期返回：

```json
{"status": "ok"}
```

SQLite 只用于骨架检查和不涉及并发的快速测试。实现业务模型后，本地集成测试和部署都应连接 PostgreSQL。

## PostgreSQL 本地开发

安装并启动 Docker Desktop 后运行：

```powershell
docker compose up -d db
docker compose ps
```

`compose.yaml` 固定 PostgreSQL 18，并通过 `pg_isready` 健康检查。不要把 Compose 中的本地开发密码用于部署环境。

快速测试与 PostgreSQL 门禁分开运行：

```powershell
$env:DJANGO_USE_SQLITE = "1"
python -m pytest -m "not postgres"

$env:DJANGO_USE_SQLITE = "0"
python -m pytest -m postgres

python -m ruff check src tests manage.py
python manage.py check
```

也可以在环境变量准备完成后运行 `scripts/verify.ps1`。PostgreSQL 不可用时，并发测试会失败，不会静默跳过。

## 题库与活动初始化

公开虚构格式样例位于 `content/examples/question-bank-v1/`。真实题库必须留在 `_private/` 或独立私密内容仓库。

```powershell
python manage.py validate_question_bank <题库目录包>
python manage.py import_question_bank <题库目录包>

python manage.py create_activity_edition demo-2026 "示例活动" <题库版本> `
  --actor <管理员用户名> --reason "首次启用" --open
```

旧库转换和审核包导出只允许写入 `_private/`：

```powershell
python manage.py convert_legacy_question_bank `
  _private/question-banks/legacy-2024.xlsx `
  _private/question-banks/legacy-2024-converted

python manage.py export_question_audit_batches `
  _private/question-banks/legacy-2024-converted `
  _private/question-banks/legacy-2024-audit
```

旧库仍是 `pending`。正式私密活动若要在逐题审查前使用，创建或切换活动时必须显式传入 `--legacy-exception`、管理员账号和原因。

## 目录

- `src/config/`：Django 项目配置。
- `src/quiz/`：答题业务代码、参与者模板和静态资源。
- `tests/`：自动化测试。
- `content/examples/`：可公开的格式说明和示例题位置。
- `docs/`：项目计划、结构、ADR、研究交接和界面基线。
- `_private/`：现役题库、未审查材料、部署本地资料和往届参考，不进入 Git。
- `docs-agent/`：本机 Agent 状态，不进入 Git。

详细职责见 [仓库结构](docs/STRUCTURE.md)，当前实施顺序见 [项目计划](docs/PROJECT_PLAN.md)，往届包证据见 [研究交接](docs/research-handoff.md)。

## 内容与公开边界

程序仓库初始保持私密。完成代码和素材溯源、许可与致谢、个人数据、密钥和 Git 历史审查后，才决定是否公开。现役题库及答案即使在程序仓库公开后也保持私密。

仓库暂不声明开源许可证。完成往届代码和素材溯源后，再选择适合的许可证并补齐 `LICENSE` 与致谢文件。
