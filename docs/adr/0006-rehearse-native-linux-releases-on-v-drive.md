# ADR 0006：在 V 盘使用原生 Linux 服务演练发布

状态：已接受

## 决策

本地预发布使用 `Ubuntu-Quiz-Lab` WSL 2 发行版，存储位于外接 V 盘。Ubuntu 内运行 PostgreSQL 18、Gunicorn、Caddy 和 systemd，不安装独立 Docker Engine。

Docker Desktop 继续承担 Windows 开发和 PostgreSQL 并发门禁。两套环境不共享数据库。Linux 演练只使用虚构数据。

## 原因

Docker 官方不建议在启用 Docker Desktop WSL 后端时，同时在普通 WSL 发行版中安装另一套 Docker Engine。原生服务可以演练 Linux 用户、权限、systemd、反向代理、逻辑备份和恢复，也不会破坏已经验证的 Docker Desktop 环境。

## 影响

- 发布包从干净 Git 提交生成，并记录 SHA-256。
- 数据迁移使用 `pg_dump -Fc` 和 `pg_restore`，不复制 Docker VHDX 或 PostgreSQL 数据目录。
- V 盘备份必须另存一份到 E 盘或其他设备，才能算恢复副本。
- 拔盘前必须停止两套环境并执行只读预检。
