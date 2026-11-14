# Quiz Platform

USTC 日语协会维护的游园会答题平台。当前交接版本是 `v1.0.0`，支持单选、填空、版本化题库、随机抽题、服务端计时、答题历史和私密管理后台。代码采用 [MIT](LICENSE)，第三方素材的使用范围见 [NOTICE](NOTICE.md)。

- **接手或部署** → [v1.0.0 交接说明](docs/handoff-v1.md) · [GitHub Release](https://github.com/ustc-nihongo-circle/quiz-platform/releases/tag/v1.0.0)
- **本地改代码** → 下方的虚构题库演示 · [仓库结构](docs/STRUCTURE.md) · [测试说明](tests/README.md)
- **下一版前后端分离** → [实施顺序](docs/frontend-separation-plan.md) · [参与者 OpenAPI](docs/api/participant-v2.yaml)

## 本地开发

在新的 checkout 用 Python 3.12–3.14 和 PowerShell 启动。以下命令为本地演示生成临时密钥，并使用 `var/dev.sqlite3`。如需在后续终端继续使用同一测试库，请在仓库外保管并复用相同的身份密钥。

```powershell
python -m venv .venv
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install -e '.[dev]'

$env:DJANGO_DEBUG = '1'
$env:DJANGO_USE_SQLITE = '1'
$env:DJANGO_SECRET_KEY = & $python -c 'import secrets; print(secrets.token_urlsafe(48))'
$env:QUIZ_IDENTITY_KEYS = & $python -c 'import base64,json,secrets; print(json.dumps(dict(v1=base64.b64encode(secrets.token_bytes(32)).decode())))'
$env:QUIZ_IDENTITY_ACTIVE_KEY_ID = 'v1'
$env:QUIZ_IDENTITY_HMAC_KEY = & $python -c 'import base64,secrets; print(base64.b64encode(secrets.token_bytes(32)).decode())'

New-Item -ItemType Directory -Force var | Out-Null
& $python manage.py check
& $python manage.py migrate
& $python manage.py provision_quiz_operator demo-operator
& $python manage.py changepassword demo-operator
& $python manage.py validate_question_bank content/examples/question-bank-v1
& $python manage.py import_question_bank content/examples/question-bank-v1
& $python manage.py create_activity_edition demo "示例活动" public-example-v1 `
  --actor demo-operator --reason "本地演示" --question-count 2 --open
& $python manage.py runserver 127.0.0.1:8000
```

打开 `http://127.0.0.1:8000/participant/`，用虚构资料登记、开卷、交卷并查看「我的答题记录」。本地后台在 `/ops/`，用上面设置的管理员密码登录。`/health/ready/` 返回 `{"status":"ok","database":"ok"}` 时表示数据库已就绪。

### 验证

在同一终端运行 SQLite 快速检查。

```powershell
& $python -m pytest -m 'not postgres'
& $python -m ruff check .
```

并发验收需单独使用 PostgreSQL。在本地 Docker 启动后运行以下命令。环境变量对应仓库的开发 Compose 配置。

```powershell
docker compose up -d --wait db
$env:DJANGO_USE_SQLITE = '0'
$env:POSTGRES_DB = 'nihongo_quiz'
$env:POSTGRES_USER = 'nihongo_quiz'
$env:POSTGRES_PASSWORD = 'local-development-only'
$env:POSTGRES_HOST = '127.0.0.1'
$env:POSTGRES_PORT = '5432'
& $python -m pytest -m postgres
```

## 文档

- [部署、备份与恢复手册](docs/deployment-release.md)
- [现场管理控制台](docs/ops-console.md)
- [题库格式](content/examples/README.md)
- [v1 参与者接口](docs/frontend-contract.md)
- [往届包研究交接](docs/research-handoff.md)

真实题库及答案、参与者资料、生产配置、备份和恢复密钥通过私密渠道交接。仓库与 Release 使用虚构示例题。
