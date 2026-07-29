---
name: export-chat-history
description: "将当前微信群聊指定日期或时间范围内的聊天记录导出为 Excel 并发送到当前群聊。当用户要求导出、下载、整理或备份今天、昨天或某段时间的群聊记录、聊天记录、群消息时使用。"
---

# 导出群聊记录

运行 `scripts/export_chat_history.py`，查询当前微信群聊的聊天记录，生成 Excel 工作簿并直接发送到当前群聊。

## 入参

```json
{
  "type": "object",
  "properties": {
    "date": {
      "type": "string",
      "description": "按自然日导出，格式为 YYYY-MM-DD。"
    },
    "start_time": {
      "type": "string",
      "description": "自定义范围的开始时间（包含），格式为 YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss。"
    },
    "end_time": {
      "type": "string",
      "description": "自定义范围的结束时间（不包含），格式为 YYYY-MM-DD HH:mm 或 YYYY-MM-DD HH:mm:ss。"
    }
  },
  "additionalProperties": false
}
```

遵守以下时间规则：

- 使用 Asia/Shanghai 时区。
- 用户未指定时间时，不传时间参数，默认导出今天 00:00 至脚本执行时的记录。
- 用户说“今天”时传当天 `--date`；说“昨天”时传昨天的 `--date`。
- 自定义范围必须同时传 `--start_time` 和 `--end_time`，结束时间必须晚于开始时间。
- `--date` 不能与自定义范围同时使用。

## 执行

导出今天：

```bash
python3 scripts/export_chat_history.py --date 2026-07-29
```

导出自定义范围：

```bash
python3 scripts/export_chat_history.py --start_time '2026-07-28 09:00' --end_time '2026-07-28 18:00'
```

未指定时间时：

```bash
python3 scripts/export_chat_history.py
```

脚本由 `execute_skill_script` 在技能根目录运行。运行时自动读取客户端注入的以下环境变量：

- `ROBOT_FROM_WX_ID`：当前群聊 ID。
- `ROBOT_WX_ID`：机器人自身微信 ID，用于排除机器人消息。
- `ROBOT_WECHAT_CLIENT_PORT`：机器人客户端端口，用于发送 Excel。
- `ROBOT_CODE`、`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`：机器人实例数据库连接。

## 查询与导出规则

- 仅允许导出当前群聊 `ROBOT_FROM_WX_ID`，不接受外部传入群 ID。
- 复用客户端群聊总结的查询语义：查询 `messages`，联表 `chat_room_members` 取得群成员备注或昵称，排除机器人自身发送的消息。
- 导出普通文本消息，以及引用消息、网页分享和文件消息中可读的标题或描述。
- 按消息时间、消息 ID 升序排列，结束时间不包含在结果中。
- Excel 包含“导出说明”和“聊天记录”两个工作表；聊天记录列为序号、发送时间、发送人、发送人微信 ID、消息内容。
- 单次最多导出 50,000 条记录；超过上限时停止并提示缩短时间范围。
- 生成后直接调用客户端本地文件发送接口；发送成功后删除临时文件。

## 结果处理

- 仅当脚本返回 JSON 且 `ok` 为 `true` 时，告知用户导出成功；脚本已经发送文件，不要再次调用发送文件技能。
- 没有符合条件的记录时，说明该时间范围没有可导出的聊天记录，并建议用户调整日期或范围。
- 查询、生成或发送失败时，按脚本错误说明原因，不要声称文件已发送。
