# 验证分类

归档版保留已验收的测试模块路径，下一版重构时再按后端模块迁移，避免让路径整理混入运行基线变更。

| 类别 | 入口 |
| --- | --- |
| 单元及格式边界 | `test_release_artifacts.py`、`test_recovery_key_bundle.py`、`test_proxy_service_trust.py`、`test_question_banks.py` 中的纯格式校验 |
| Django 集成 | 其他根目录 `test_*.py`，包含 API、权限、计分、身份、媒体和后台 |
| PostgreSQL 并发 | `test_postgres_concurrency.py`，以及标记为 `postgres` 的用例 |
| 浏览器 | `browser/`，必须连接明确标记的虚构数据环境 |
| 性能 | `performance/burst_probe.py`，限制为回环地址和专属响应标记 |

完整运行 `pytest` 需要 PostgreSQL；本地 SQLite 快速检查使用 `pytest -m "not postgres"`，不能替代并发验收。发布前同时执行 `ruff check .`、`manage.py check` 和 `manage.py makemigrations --check --dry-run`。性能探针的夹具、版本、限流条件和持续时间必须随报告保存，不对真实成绩库施压。
