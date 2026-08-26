# 参与者前端接口契约

最后更新：2026-08-26

## 使用方式

参与者页面位于 `/participant/`，根路径重定向到该页面。模板与 `/api/v1/` 接口同源部署。浏览器使用 Django Session Cookie，不保存独立访问令牌。页面响应设置 CSRF Cookie，随后在写请求的 `X-CSRFToken` 请求头中回传该值。

模板只注入当前 Session 的非敏感参与者 `id/display_name` 和最近一轮脱敏答题状态，用于刷新或新标签页恢复。它不注入编号、联系方式、身份密文、正确答案或填空标准答案。后续状态变化仍通过本文件定义的 JSON 接口完成。

所有时间均为带时区的 ISO 8601 字符串。接口只接受 `application/json`。示例题目、编号和联系方式均为虚构数据。

## 错误格式

```json
{
  "error": {
    "code": "validation_error",
    "message": "请检查提交字段。",
    "field_errors": {"contact": ["请输入有效联系方式。"]},
    "retryable": false
  }
}
```

- `400` 表示字段或答案载荷无效。
- `401` 表示缺少参与者 Session。
- `403` 表示 CSRF 校验失败。
- `404` 表示当前活动或授权范围内的答题记录不存在。
- `409` 表示活动状态、身份恢复、超时或题库状态冲突。

## 活动

### `GET /api/v1/activity`

返回当前未封存活动及板块。该请求同时设置 CSRF Cookie。

```json
{
  "activity": {
    "code": "demo-2026",
    "title": "虚构示例活动",
    "status": "open",
    "categories": [
      {
        "code": "demo",
        "title": "虚构板块",
        "question_count": 2,
        "time_limit_seconds": 300
      }
    ]
  }
}
```

前端必须处理 `draft/open/paused/closed`。`archived` 活动不会成为当前活动。

## 参与者 Session

### `POST /api/v1/participant-session`

```json
{
  "display_name": "示例昵称",
  "identifier": "PB24000001",
  "contact": "13800138000"
}
```

首次登记返回 `201`，再次进入返回 `200`。两种响应形状相同：

```json
{
  "participant": {
    "id": "c2c43cb2-292d-4bb4-adb5-49c76c42c8ae",
    "display_name": "示例昵称"
  },
  "created": true
}
```

编号已存在但联系方式不匹配时返回 `participant_recovery_required`。响应不会说明哪一字段存在，也不会回传编号、联系方式、摘要或密文。再次进入不会覆盖首次登记的显示名。

### `DELETE /api/v1/participant-session`

清除当前参与者 Session，成功返回 `204`。

## 答题记录

### `POST /api/v1/attempts`

```json
{"category_code": "demo"}
```

创建答题返回 `201`。参与者已有未超时答题时返回同一记录和 `200`。暂停或关闭状态下不能创建新记录，但仍可返回既有进行中记录。

```json
{
  "attempt": {
    "id": "c8a30238-c715-416c-9fe5-b7c343f5a7fb",
    "status": "in_progress",
    "category": {"code": "demo", "title": "虚构板块"},
    "started_at": "2026-08-25T12:00:00+08:00",
    "deadline_at": "2026-08-25T12:05:00+08:00",
    "question_count": 2,
    "questions": [
      {
        "id": "3a07375a-bc1f-49fa-a2bb-4ed690278f06",
        "position": 1,
        "prompt": "这是一道虚构示例题。",
        "type": "single_choice",
        "image_url": null,
        "options": [
          {"id": "A", "text": "选项 A"},
          {"id": "B", "text": "选项 B"},
          {"id": "C", "text": "选项 C"},
          {"id": "D", "text": "选项 D"}
        ]
      }
    ]
  }
}
```

题目顺序、选项顺序、板块、题库版本和截止时间由服务端保存。响应不包含正确选项或可接受填空答案。

### `GET /api/v1/attempts/current`

返回当前进行中答题。超过截止时间和 3 秒宽限后，第一次读取会把记录改为 `timed_out` 并返回该状态；之后不再把它视为当前答题。

### `GET /api/v1/attempts/{attempt_id}`

返回当前参与者自己的指定答题记录。其他参与者的 UUID 统一按不存在处理。

### `PUT /api/v1/attempts/{attempt_id}/submission`

```json
{
  "answers": [
    {"item_id": "3a07375a-bc1f-49fa-a2bb-4ed690278f06", "answer": "A"}
  ]
}
```

省略的题目按未答处理。选择题答案只能为 `A/B/C/D`，填空答案最长 500 字。提交其他答题记录的题目 ID 或重复题目 ID 会整体拒绝。

首次有效提交封存结果。重复提交返回首次结果；载荷变化不会覆盖答案。超过截止时间和 3 秒宽限返回 `attempt_expired`。

结果在原题目上增加 `answer` 与 `correct`，并增加以下字段：

```json
{
  "score": 1,
  "score_rate": 0.5,
  "category_high_score": 1,
  "reward_phrase": null
}
```

结果只说明每题对错，不返回正确选项或填空标准答案。
