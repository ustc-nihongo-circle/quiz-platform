# ADR 0005：显式指定参与者入口届次

状态：已接受

## 决策

`ActivityEdition.is_participant_entry` 明确表示参与者 API 当前展示哪一届活动。数据库保证最多一届为 true。只有该届次可以进入 open。

切换入口必须填写原因并写审计。开放或暂停中的入口必须先关闭，才能切换到另一届。封存入口届次会在同一事务内清除标记。

## 原因

按“最近更新且未封存”推断会让下一届 draft 遮住正在运行或刚关闭的活动。显式选择允许多届并存，也让切换成为可审计操作。

## 影响

- `GET /api/v1/activity` 在没有入口届次时返回 `activity_unavailable`。
- `create_activity_edition --open` 会先指定入口，再开放活动。
- draft 可以提前准备，不影响参与者页面。
