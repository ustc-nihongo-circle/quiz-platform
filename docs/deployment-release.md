# 部署归档版 v1.0.0

这是已用于活动的 Django 归档版本，支持单选、填空、服务端计时、成绩和私密后台。Vue、多选、全站用户中心属于后续版本。此发行包不含真实题库、参与者、配置或恢复密钥，安装后没有开放活动。

## 准备与首次启动

使用 Linux x86-64、Python 3.11 或更高版本、curl、Docker Engine 和 Docker Compose v2。服务器需要联网下载固定镜像。参考资源为应用 1 CPU / 512 MiB、数据库 0.5 CPU / 512 MiB，另为网关、维护进程、宿主系统和磁盘留空间。该配额不是并发承诺，容量见同版测试报告。

从 GitHub Release 下载 `quiz-platform-v1.0.0.zip` 和 `SHA256SUMS`，核对 ZIP 后解压到独立目录。ZIP 内 `release.json` 固定应用镜像 ID、镜像下载 SHA-256，以及 PostgreSQL/Caddy 摘要；首次初始化自动下载预构建镜像。无需在服务器构建 Python 或前端。

先在本机回环接口演示：

```sh
cd quiz-platform-v1.0.0
python3 quizctl.py init
python3 quizctl.py admin operator-name
python3 quizctl.py start
python3 quizctl.py status
```

管理员命令会交互设置密码。演示参与者入口 `http://localhost:8080/participant/`，后台 `http://localhost:19443/ops/`。回环 HTTP 仅供本机演示；公开站必须使用 HTTPS。首次初始化生成各自独立随机密钥；重复 `init` 不覆盖配置、密钥或数据。若中途失败，排除依赖或端口占用后重试。已有同名项目卷时，空目录初始化会拒绝启动，防止误接旧数据库。

域名已解析且本机 80/443 空闲时，可独立使用 Caddy 自动申请、续签证书：

```sh
python3 quizctl.py init --domain quiz.example.org --bind 0.0.0.0 --http-port 80 --https-port 443
python3 quizctl.py admin operator-name
python3 quizctl.py start
```

后台始终只监听服务器回环接口。通过 SSH `-L 19443:127.0.0.1:19443` 转发，并在专用浏览器会话中将 **后台域名** 解析到本机，再访问 `https://quiz.example.org:19443/ops/`；保持域名和端口与 `ADMIN_ORIGIN` 一致。不要把后台端口开放到公网。公网 `/ops/` 和 `/admin/` 返回 404。健康端点为 `/health/ready/`。

## 接入既有 Nginx

已有 Nginx 负责 HTTPS 时，使用不同的回环上游端口，指定有效证书文件：

```sh
python3 quizctl.py init --domain quiz.example.org --external-proxy \
  --http-port 18080 --https-port 18443 --admin-port 18081 --admin-origin-port 19443 \
  --tls-certificate /etc/letsencrypt/live/quiz.example.org/fullchain.pem \
  --tls-key /etc/letsencrypt/live/quiz.example.org/privkey.pem
python3 quizctl.py admin operator-name
python3 quizctl.py start
```

审阅生成的 `nginx.conf`，合并到现有配置，执行 `nginx -t` 后 reload。模板公开 443，后台 TLS 只在 127.0.0.1:19443。沿用现有证书续签；它不依赖数据库备份。模板会覆盖客户端传来的代理头；后端只信任网关服务的实际地址。不要信任任意来源或整个公网网段。

## 导入题库与开关活动

将私密题库目录放入包旁 `content/`（映射到容器 `/app/_private/content/`），然后预检、导入。虚构样例已随镜像提供：

```sh
python3 quizctl.py manage validate_question_bank content/examples/question-bank-v1
python3 quizctl.py manage import_question_bank content/examples/question-bank-v1
python3 quizctl.py manage create_activity_edition demo "示例活动" public-example-v1 \
  --actor operator-name --reason "演示验收" --question-count 2 --open
```

仅在确认题库来源和活动配置后开放。后台可暂停入口、关闭、封存活动，已有答卷按服务端截止处理。题库格式和后台操作分别见仓库 `docs/` 文档。v1 的 `multiple_choice` 表示单选。

```sh
python3 quizctl.py stop
python3 quizctl.py start
```

`stop` 停止本部署全部服务，包括维护进程，保留 Docker 卷、配置和备份。公开结束公告需由宿主 HTTPS 网关单独保留；停掉整套 Compose 不会自动生成公告。

## 加密备份与恢复

```sh
python3 quizctl.py backup
python3 quizctl.py restore /secure/backup.tar.gz.age --identity /secure/recovery.agekey
```

备份保存数据库、题图、应用配置、版本信息和内容哈希清单，使用 age 加密，写到 `backups/`，不自动淘汰。复制到另一台设备并独立保管 `.keys/recovery.agekey`；仅有加密备份无法恢复密钥。定期在隔离环境恢复核验。

恢复目标先用相同版本 `init` 创建独立部署；已有业务数据时必须显式加 `--replace`。恢复前验证全部载荷，替换前另做目标安全备份；恢复身份密钥但保留目标基础设施配置。成功后业务仍停止，检查后才运行 `start`。失败时保留备份和运行条件排查，不手工删除数据库卷。并发编辑题库时应暂停编辑后备份，以保持题图与数据库一致。

## 升级与回退

v1 工具支持同一 PostgreSQL 镜像下的兼容发行版升级。先暂停所有活动，等答卷全部提交或超时，将新包解压到另一个目录：

```sh
cd /srv/quiz-platform-new
python3 quizctl.py upgrade --from /srv/quiz-platform-old
```

工具检查无开放活动和在途答卷，停止旧服务、加密备份，再迁移和检查新版本。迁移或上线前检查失败会恢复旧数据库和旧服务；失败目录不能直接 `start`。旧目录和备份保留。升级后人工回退须使用旧目录及升级前备份，并评估新版本上线后的新增数据。数据库大版本升级另行制定迁移方案。

**计划中的下一版使用新数据库，不通过此命令迁入本届真实数据。**

## 验证范围

归档发行测试覆盖全新 Linux 安装、重复初始化、加密恢复、包含非原子数据修改的失败迁移回退、Nginx HTTPS 接入、私密后台边界与手机/桌面答题恢复。Django 部署检查允许未启用 HSTS 子域覆盖和 preload 的两项提示，这两项需域名所有者统一决定。生产能力以同版容量报告的已测范围为准。

维护资料：[Docker Compose 生产部署](https://docs.docker.com/compose/how-tos/production/)、[Django 部署检查](https://docs.djangoproject.com/en/5.2/howto/deployment/checklist/)、[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)。
