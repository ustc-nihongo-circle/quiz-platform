# 单次答题会话过期策略

每个板块仍使用配置的作答时限，当前演练为 300 秒。普通交卷保留原有 3 秒网络宽限。单次会话从 `started_at` 起满 30 分钟后强制过期，此上限没有额外宽限；新会话的 `deadline_at` 也不会超过开始后 30 分钟。

访问、提交和重新开卷均检查服务器时间。即使用户关闭页面、不再请求服务器，定时任务也会把开始至少 30 分钟且仍为 `in_progress` 的记录改成 `timed_out`。它不删除答卷，不改已交卷、已作废或已超时记录，不生成成绩。每次实际修改记录聚合审计 `stale_attempts_expired`，仅包含数量和策略时长。

## 检查与手动执行

在应用运行环境执行：

```sh
python manage.py expire_stale_attempts --dry-run
python manage.py expire_stale_attempts
```

预检输出 `eligible=N max_age_minutes=30`；执行输出 `expired=N max_age_minutes=30`。可增加 `--activity <slug>` 限定活动；省略时覆盖所有活动，包括已关闭活动中的遗留会话。重复执行不会重复修改最终状态。

## 定时运行

原生 Linux 安装使用 `nihongo-quiz-expire-attempts.service` 和同名 timer，安装脚本在应用发布后启动它。香港 Docker 部署使用 `nihongo-quiz-hk-expire-attempts.service` 和同名 timer，工作目录为 `/srv/nihongo-quiz-hk`，在现有 Web 容器内执行命令。

两种 timer 均每分钟检查一次。因此 30 分钟是服务端拒绝提交的硬上限，无人访问时数据库状态通常在下一次检查写回，约有一分钟调度延迟。主机停机或任务失败时可能更晚；恢复运行后的下一次检查会处理积压记录。查看最近执行与下一次计划：

```sh
systemctl status nihongo-quiz-hk-expire-attempts.timer
systemctl show nihongo-quiz-hk-expire-attempts.service -p Result -p ExecMainStatus
journalctl -u nihongo-quiz-hk-expire-attempts.service -n 10 --no-pager
```

原生安装把以上单元名中的 `-hk` 去掉。后台清理通过带状态条件的原子 UPDATE 与提交事务协调；若提交先完成，清理不会覆盖它的成绩。

## 回退

回退至不支持此命令的旧版本前，先停止并禁用对应 timer，然后停止仍在执行的 oneshot service，再恢复旧应用。已转为 `timed_out` 的记录保持超时，不自动恢复为进行中；上线前备份保留原状态用于调查。不要为了撤销策略整体恢复数据库，否则会丢失备份之后的新答卷。
