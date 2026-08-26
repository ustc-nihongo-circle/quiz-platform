# 公开示例题库

本目录以后只保存格式示例和完成来源、许可、致谢审查的退役题。现役题库和答案必须放在 `_private/question-banks/`，不得复制到本目录或 Git 历史。

`question-bank-v1/` 是完全虚构且可公开发布的最小目录包：

- `workbook.xlsx` 固定包含 `metadata`、`questions`、`sampling_rules` 三张表。
- `assets/` 保存题目引用的图片；工作簿只能使用 `assets/...` POSIX 相对路径。
- `metadata` 采用 `key/value` 行，必填 `format_version`、`bank_version`、`title`、`source_label`。
- `questions` 同时演示单选题和填空题。单选题填写 A-D 与 `correct_option`；填空题在
  `accepted_answers` 中每行填写一个可接受答案。
- `review_status` 只能为 `pending/verified/rejected`。`private_event_allowed` 与
  `public_release_allowed` 分别控制私密活动和公开发布；允许公开发布时审查状态必须为 `verified`。
- `sampling_rules` 为每个板块和抽题池给出正整数默认配额与唯一显示顺序，配额不能超过池内题数。

可运行 `python manage.py validate_question_bank content/examples/question-bank-v1` 验证格式。
