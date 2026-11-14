# 游园会答题网站 v1.0.0 交接

交接对象是接手部署、换题或维护网站的同学。先用虚构题库预演，再按活动安排导入本届私密题库。 [源码标签](https://github.com/ustc-nihongo-circle/quiz-platform/tree/v1.0.0) · [v1.0.0 Release](https://github.com/ustc-nihongo-circle/quiz-platform/releases/tag/v1.0.0)

## 在 Linux 本机复现

准备 Linux x86-64、Python 3.11 或以上、curl、Docker Engine 和 Docker Compose v2。发行安装器按 OCI 索引摘要校验 Docker 的镜像 ID，演示环境选用 Docker 的 containerd 镜像存储。首次初始化需要联网下载镜像。Windows 源码开发和快速演示见 [README](../README.md)。 [Docker 镜像存储](https://docs.docker.com/engine/storage/containerd/)

1. 从 Release 下载 `quiz-platform-v1.0.0.zip` 和 `SHA256SUMS`。在下载目录校验 ZIP。确认输出为 `quiz-platform-v1.0.0.zip: OK` 后再解压。

   ```sh
   grep '  quiz-platform-v1\.0\.0\.zip$' SHA256SUMS | sha256sum -c -
   ```

2. 进入单独的部署目录，初始化并设置演示管理员密码。安装器会校验下载的镜像并为这套部署生成独立密钥。

   ```sh
   cd quiz-platform-v1.0.0
   python3 quizctl.py init
   python3 quizctl.py admin demo-operator
   python3 quizctl.py start
   python3 quizctl.py status
   ```

3. 导入镜像自带的虚构题库，并将演示活动设为参与者入口。

   ```sh
   python3 quizctl.py manage validate_question_bank content/examples/question-bank-v1
   python3 quizctl.py manage import_question_bank content/examples/question-bank-v1
   python3 quizctl.py manage create_activity_edition demo "示例活动" public-example-v1 \
     --actor demo-operator --reason "交接验收" --question-count 2 --open
   ```

4. 打开 `http://localhost:8080/participant/`，用虚构资料登记，例如显示名「演示」、编号 `PB24000001`、邮箱 `demo@example.invalid`。开卷后填一题并刷新，核对剩余时间和草稿，再交卷并查看「我的答题记录」。在 `http://localhost:19443/ops/` 登录，核对该答卷。部署、恢复和停服命令见 [发行手册](deployment-release.md)。

## 交接给下一届

| 要做的事 | 从哪里开始 |
| --- | --- |
| 换本届题目 | 按 [题库格式](../content/examples/README.md) 制作 Excel 和题图目录，用管理命令预检、导入和设置题量。后台按届次管理入口、启用题库和活动状态。 |
| 换 v1 视觉 | 颜色、字体和角色样式在 [participant.css](../src/quiz/static/quiz/participant.css)，品牌文案和角色插槽在 [participant.html](../src/quiz/templates/quiz/participant.html)。在源码中修改并重建发行产物，手机与桌面各验一次答题流程。素材使用范围见 [NOTICE](../NOTICE.md)。 |
| 管理及查记录 | 每人使用独立操作员账号。后台查看答卷和审计记录，身份字段揭示及导出按 [现场管理手册](ops-console.md) 执行。 |
| 接手备份 | 将加密备份和恢复密钥分开交接，记录对应版本，在独立实例验证恢复。真实题库、答案、参与者资料和旧服务器归档继续私密保管。 |

## 正式活动前核对

- 对照本届主机和网络，更新并重建依赖镜像，复核 Django 5.2 LTS 安全补丁、HTTPS、后台私密入口和备份恢复。当前归档镜像锁定 Django 5.2.17。 [依赖锁](../deploy/container/requirements.lock) · [Django 支持版本](https://www.djangoproject.com/download/)
- 用正式部署同等配置和虚构数据测一次集中开卷与交卷。旧实测在原环境通过 20 人同步持续档，50 人探测延迟超标。 [容量实测](capacity-v1.md)
- 交接登记发行标签、部署日期、活动届次、题库版本、备份位置和恢复验收结果。管理员密码和恢复密钥通过各自的私密渠道保管。

下一版独立前端、活动主题和发布追溯的实现见 [拆分计划](frontend-separation-plan.md)。
