# V 盘 Ubuntu 预发布演练

最后更新：2026-08-26

本手册用于本地 Linux 学习和发布演练。它不会部署朋友服务器，也不使用真实参与者数据。

## 1. Windows 预检与安装

外接盘身份保存在 Git 忽略的 `_private/deployment-local/v-drive.json`。先运行只读检查：

```powershell
.\scripts\check-v-drive.ps1 -Mode startup
```

首次安装 Ubuntu：

```powershell
.\scripts\install-ubuntu-lab.ps1
wsl --list --verbose
wsl --distribution Ubuntu-Quiz-Lab
```

WSL 首次启动会要求人工创建 Linux 用户和密码。不要使用项目管理员密码、数据库密码或 Windows 密码。

若 `systemd` 不是 PID 1，在 Ubuntu 中写入以下配置后退出，再从 PowerShell 终止发行版：

```ini
[boot]
systemd=true
```

```powershell
wsl --terminate Ubuntu-Quiz-Lab
```

重新进入后运行 `ps -p 1 -o comm=`，预期输出 `systemd`。

## 2. 准备原生 Linux 服务

把经过提交的发布包或当前只含公开代码的工作副本放入 Ubuntu 可读位置。运行：

```bash
sudo ./deploy/scripts/bootstrap-ubuntu.sh
```

首次运行会安装 PostgreSQL 18、Caddy、Python 和系统依赖，建立 `/srv/nihongo-quiz/`，然后生成：

```text
/srv/nihongo-quiz/shared/env/production.env.example
```

复制为 `production.env`，生成三项独立身份密钥、Django 密钥和数据库密码。文件权限必须保持 `0600`。不要把填写后的文件复制回仓库。

再次运行 `bootstrap-ubuntu.sh`。脚本会建立数据库、systemd unit 和本地 Caddy 配置。

Docker Desktop 已占用 Windows 的 5432 时，Ubuntu 的 PostgreSQL 集群可能自动选择 5433。`create-lab-env.sh` 会从 `pg_lsclusters` 读取实际端口，不要手工假定为 5432。

## 3. 构建和安装发布包

发布包只允许从干净提交生成：

```powershell
.\scripts\build-release.ps1
```

命令在 `dist/` 生成 ZIP 和 JSON 清单。把 ZIP 及清单传入 Ubuntu，核对清单中的 SHA-256，然后运行：

```bash
sudo /path/to/release/deploy/scripts/install-release.sh \
  /path/to/nihongo-quiz-<commit>.zip \
  <archive_sha256>
```

脚本建立独立虚拟环境，运行 Django check、迁移和 collectstatic，最后原子切换 `current` 链接并重启 Gunicorn。

本地入口是：

```text
https://quiz.localhost:18443/
```

Caddy 使用本地 CA。将 Caddy 根证书导入 Windows 信任存储属于人工步骤。导入前核对证书来自当前 `Ubuntu-Quiz-Lab`，不要信任其他来源的根证书。

## 4. 初始化虚构数据

本地演练只导入 `content/examples/question-bank-v1/`。不得导入往届参与者 CSV、真实联系方式或现役活动数据。

```bash
cd /srv/nihongo-quiz/current
set -a
source /srv/nihongo-quiz/shared/env/production.env
set +a

.venv/bin/python manage.py import_question_bank content/examples/question-bank-v1
.venv/bin/python manage.py provision_quiz_operator lab-operator
sudo -u nihongo-quiz .venv/bin/python manage.py changepassword lab-operator
```

随后使用 `create_activity_edition --open` 建立虚构届次。该命令会先指定参与者入口，再开放活动。

## 5. 健康检查

```bash
systemctl status postgresql nihongo-quiz caddy
curl --fail http://127.0.0.1:18080/health/live/
curl --fail http://127.0.0.1:18080/health/ready/
curl --fail --cacert /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt \
  https://quiz.localhost:18443/health/ready/
```

就绪端点必须返回 `{"status":"ok","database":"ok"}`。

## 6. 备份与恢复演练

普通日备：

```bash
sudo systemctl start nihongo-quiz-backup.service
sudo journalctl -u nihongo-quiz-backup.service --since today
```

里程碑快照不参与七份日备轮换：

```bash
sudo -u nihongo-quiz /srv/nihongo-quiz/current/deploy/scripts/backup.sh before-open
```

Linux 主备份目录强制使用 `0700`。复制到 E 盘 NTFS 忽略目录时，DrvFs 可能不支持
POSIX `chmod`；脚本会明确告警并继续，副本访问边界由 Windows ACL 负责。
Linux 内的环境、媒体、备份和恢复目录使用 `0700`，应用与备份服务使用
`UMask=0077`。只有发布源码和 `collectstatic` 产物向 Caddy 保留只读遍历权限。

每份备份包含数据库自定义格式转储、私密媒体、发布版本、迁移列表和 SHA-256。密钥不进入备份。脚本还必须把备份复制到 E 盘私密目录，否则返回失败。

恢复演练只能写入以 `_restorecheck` 结尾的新数据库：

```bash
sudo /srv/nihongo-quiz/current/deploy/scripts/restore-check.sh \
  /srv/nihongo-quiz/backups/before-open-<timestamp> \
  nihongo_quiz_restorecheck
```

脚本只允许 root 执行，由本机 `postgres` 角色创建隔离数据库并把所有者设为应用角色；
应用运行账号本身不获得 `CREATEDB` 权限。

恢复成功后核对迁移、虚构参与者数量、答题数量、题图和身份解密，再人工删除恢复检查数据库。

## 7. 应用回滚

```bash
sudo /srv/nihongo-quiz/current/deploy/scripts/rollback-release.sh <release-id>
```

该命令只回滚应用代码，不回滚数据库迁移。只有迁移保持向后兼容时才允许使用。

## 8. 安全拔除与恢复

拔盘前在 Ubuntu 中停止服务：

```bash
sudo systemctl stop nihongo-quiz caddy postgresql
```

回到 PowerShell：

```powershell
wsl --terminate Ubuntu-Quiz-Lab
# 退出 Docker Desktop
wsl --shutdown
.\scripts\check-v-drive.ps1 -Mode removal
```

只有预检显示无运行 WSL 和 Docker/WSL 进程后，才通过 Windows 安全删除硬件或把已核对的磁盘脱机。

重新连接后先运行 startup 预检。需要开发数据库时启动 Docker Desktop；需要 Linux 演练时启动 `Ubuntu-Quiz-Lab`。启动后必须重新检查服务和两个健康端点。
