# 管理控制台与 Linux 实验验收

最后更新：2026-08-27

## 验收基线

- 分支：`aster/ops-console-lab`
- 已部署应用提交：`328f204aa0097f0d85d07f7cc10ab98c1c29d5a3`
- 发布包：`nihongo-quiz-328f204aa009.zip`
- 发布包 SHA-256：`43f94b6b9e92b6c5e8a5118215762994d1af3ac52bad7f036eafcc8a1ae9d109`
- 当前 Linux 链接：`/srv/nihongo-quiz/current` → `releases/328f204aa009`
- 实验数据：公开虚构题库和虚构参与者；没有复制开发库、往届 CSV 或真实参与者资料。

## 代码门禁

按固定顺序执行 `scripts/verify.ps1`：

1. SQLite：83 passed，8 deselected。
2. PostgreSQL 18：8 passed，83 deselected。包含原有五组 50 路并发，以及暂停/开始、提交/作废和入口切换。
3. Ruff：通过。
4. Django check：通过。
5. 迁移漂移：无变化。
6. collectstatic：133 个文件。

生产配置 `manage.py check --deploy` 返回 0，只报告 `security.W004`。实验环境按 ADR 保持
`SECURE_HSTS_SECONDS=0`，所以该警告是预期结果；公网 HTTPS 验证前不提高 HSTS。

## 浏览器门禁

在 Ubuntu 内使用 Playwright 1.62.0 和 Chromium 151，经真实 Caddy HTTPS、Gunicorn、
Django Session/CSRF 与 PostgreSQL 18 完成：

- 1440×900、390×844、360×800 的文档宽度分别等于视口宽度。
- 可见按钮、链接、输入框和其标签点击区均不小于 44×44。
- 三个 context 均启用减少动画偏好；控制台 0 error、0 warning，page error 为 0。
- 登录、监控、检索、完整身份字段、刷新复位、统计/身份 CSV、暂停和恢复开放通过。
- 兑奖词新增/停用、虚构题库 V1/V2 切换、作废、去身份化和状态恢复通过。
- 截图与度量保存在 Git 忽略的 `output/playwright/ops-lab/linux-final/`。

Windows 侧浏览器没有作为最终证据。当前 Hyper-V WSL 默认入站为 Block，安全审查拒绝了
未经用户单独确认的持久 18443 放行；因此改在 Ubuntu 内完成同一真实浏览器链路。

## Linux 发布与数据门禁

- Ubuntu 24.04、PostgreSQL 18.6、Caddy 2.11.4、Gunicorn 23.0.0 均由 systemd 管理。
- PostgreSQL 使用 5433，避免与 Docker Desktop PostgreSQL 18 的 5432 冲突。
- 发布脚本拒绝脏工作区，记录提交、迁移和 SHA-256；Shell 脚本强制 LF，构建器再次检查归档。
- 已从 `328f204` 的前一发布回滚并重新切回当前发布，两次数据库就绪检查均通过。
- WSL 终止、完整 `wsl --shutdown` 和重启后，服务自启动且数据库数据仍在。
- systemd 日备服务直接执行成功；V/E 两份里程碑备份的所有清单项均通过 SHA-256。
- 两次恢复均写入全新 `_restorecheck` 数据库；迁移、对象计数、身份解密和媒体 0700 权限通过。
- 50 个独立 Cookie/CSRF 会话经 HTTPS 同时提交，50/50 成功；并发阶段 0.869 秒，最慢请求 0.753 秒。
- 安全拔盘预检在 Docker Desktop、Ubuntu 服务和全部 WSL 关闭后返回通过；未自动脱机或物理拔盘。

## 未自动执行

- 没有创建 Windows Hyper-V WSL 防火墙入站规则，也没有信任 Caddy 根证书。
- 没有创建需要人工输入密码的日常 Linux sudo 用户。
- 没有删除恢复检查数据库；等待人工复核后按运行手册处理。
- 没有连接 remote、推送、部署朋友服务器、修改 DNS 或处理真实参与者数据。
