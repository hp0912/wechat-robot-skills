---
name: send-complex-message
description: "在当前微信会话中发送纯文本、引用回复或群聊艾特/@/提及消息，也可按时间、关键词、消息类型和发送人查询当前群聊或私聊最近24小时内的聊天记录。用户要求发送、艾特、引用回复或查找近期历史消息时使用，可在发送后结束当前 Agent 对话。"
---

# Send Complex Message Skill

## 描述

本技能是当前微信会话中纯文本、艾特和引用回复的统一发送入口。仅艾特时不需要正文；引用消息必须有正文，也可以附带成员艾特参数。艾特支持指定一个或多个成员，也支持微信原生的 `@所有人`。

技能脚本位于 `scripts/send_complex_message.py`。加上 `--query-history` 时只查询当前会话历史消息；发送模式统一调用客户端的 `/message/send/refermessage` 接口。`refer_message_id` 是可选参数：不传时由客户端调用普通文本消息方法，传入时发送引用消息。指定成员时，根据昵称或备注查询当前群内未退群成员；@所有人时使用客户端协议值 `notify@all`，不要把 `@昵称` 或 `@所有人` 当普通正文拼接。

本技能不带引用 ID 时通过普通文本消息实现原生艾特，支持只艾特而不附加正文。带引用 ID 时，客户端接收 `at` 并显示艾特名称，但尚未实现引用消息中的原生艾特提醒，不能把引用发送成功表述为已经提醒成员。

## 触发条件

- 用户要求将一段话分成多条发送。
- 用户要求「引用这条消息回复」「引用我刚才的话说收到」「回复我引用的那条消息」。
- 需要发送微信原生引用回复，而不是在普通文本中复述原文。
- 需要艾特、@、提及某个群成员或多个群成员。
- 用户要求「帮我艾特下 xxx」「@ 一下 xxx」「提一下 xxx 和 yyy」。
- 用户要求「@所有人」「提醒全体成员」「通知群里所有人」。
- 需要在群聊里点名提醒某人。
- 用户要求查询、搜索当前群聊或私聊的近期聊天记录，或需要查找历史消息的 `messages.id` 以便引用，特别注意的是，如果需要查询最近发送的图片、视频等文件，你应该使用 find-recent-chat-media 这个技能，本技能(send-complex-message) 不提供下载文件的能力。
- 其它时候不应该使用本技能

不带艾特的纯文本和引用回复可用于私聊和群聊；只要提供艾特参数，`ROBOT_FROM_WX_ID` 就必须是群聊 ID。

## 历史聊天记录查询

使用 `--query-history` 进入只读查询模式。会话固定取系统注入的 `ROBOT_FROM_WX_ID`：群聊只能查询该群，私聊只能查询与当前好友的私聊，包含该会话双方的收发消息。查询同时限定 `from_wxid` 和 `is_chat_room`，不能按发送人跨群或跨私聊搜索，也不能修改会话环境变量来切换查询对象。

所有时间范围必须落在**执行查询时的最近 24 小时内**。24 小时是最大回溯范围，不是固定查询时长；可以查询最近半小时、2 小时，或这 24 小时内任意更短的起止区间。超过上限、落在过去更早日期、包含未来或起止倒置的时间范围会报错，不会静默扩大或改写用户指定的范围。

| 查询参数                      | 说明                                                                                                            |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------- |
| `--query-history`             | 必须提供，不能与发送参数或 `--ended` 同用                                                                       |
| `--hours <小时数>`            | 最近多少小时，支持小数，`0 < hours <= 24`，精度为整秒且至少 1 秒；如 `0.5` 表示最近 30 分钟                     |
| `--start-time <时间>`         | 起点，支持 Unix 秒或北京时间 `YYYY-MM-DD HH:mm[:ss]`                                                            |
| `--end-time <时间>`           | 终点，格式同上；与起点均为包含边界                                                                              |
| `--keyword <关键词>`          | 对 `content` 和 `display_full_content` 做包含匹配；可重复，多个关键词必须全部命中，`%`、`_`、反斜杠按字面量匹配 |
| `--message-type <类型>`       | 消息类型编号或名称，可重复，多个类型匹配任一即可；省略时查询所有类型                                            |
| `--app-msg-type <子类型编号>` | 可重复，如 `57` 引用、`6` 文件、`5` 链接；自动限定消息类型为 `49`，不能与其他消息类型组合                       |
| `--sender-wxid <微信ID>`      | 只在当前会话内按发送人微信 ID 精确过滤                                                                          |
| `--limit <条数>`              | 单页条数，默认 `50`，范围 `1..200`                                                                              |
| `--offset <偏移量>`           | 分页偏移量，默认 `0`，必须非负                                                                                  |

`--hours` 与 `--start-time`/`--end-time` 互斥。未指定时间参数时默认查询最近 24 小时；只传起点时终点为现在，只传终点时起点为现在减 24 小时。

类型名称支持 `text=1`、`image=3`、`voice=34`、`card=42`、`video=43`、`emoji=47`、`location=48`、`app=49`、`system=10000`、`recall=10002`，其他类型可直接传数字编号。不同类别的过滤条件同时生效。

查询最近 30 分钟包含“安排”的文本消息：

```bash
python3 scripts/send_complex_message.py --query-history --hours 0.5 --keyword '安排' --message-type text
```

查询最近 6 小时某人发送的引用消息：

```bash
python3 scripts/send_complex_message.py --query-history --hours 6 --sender-wxid 'wxid_zhangsan' --app-msg-type 57 --limit 20
```

按明确起止时间查询（先根据用户指定的区间设置两个 Unix 秒时间戳，且必须在最近 24 小时内）：

```bash
python3 scripts/send_complex_message.py --query-history --start-time "$START_TIMESTAMP" --end-time "$END_TIMESTAMP" --message-type image --message-type video
```

查询成功时输出 JSON 对象，包含当前会话、实际 `start_time`/`end_time`（Unix 秒）、`count`、`has_more`、`next_offset` 和 `messages`。消息按 `created_at DESC, id DESC` 排列，每条包含数据库主键 `id`、会话及发送人微信 ID、消息类型/子类型、正文、显示内容、撤回标记和发送时间（Unix 秒）。`messages: []` 表示没有匹配记录。`has_more: true` 时可以保留过滤条件，使用返回的 `next_offset` 作为 `--offset` 继续查询，不能把单页结果表述为全部记录。

查询不发送微信消息、不输出 `ended`，也不要求配置客户端端口。需要引用查询结果时，先根据消息内容、发送人和时间确定目标，再单独以返回的 `id` 调用发送模式。不能绕过脚本直接查询其他会话或更早记录。

## 发送入参规范

`refer_message_id` 不全局必填，只在用户明确要求引用时传入，值为 `messages.id`。仅文本、仅艾特时省略该参数，不查询引用目标，也不从环境变量自动补齐引用 ID。

| 发送方式   | 艾特参数               | `refer_message_id` | 正文 `content`     |
| ---------- | ---------------------- | ------------------ | ------------------ |
| 仅艾特     | 指定成员或 `all: true` | 不传               | 可省略或为空       |
| 仅文本     | 不传                   | 不传               | 必填且不能是纯空白 |
| 仅引用     | 不传                   | 传入 `messages.id` | 必填且不能是纯空白 |
| 引用并艾特 | 指定成员或 `all: true` | 传入 `messages.id` | 必填且不能是纯空白 |

不引用时也支持正文加艾特。下方 schema 仅描述发送模式，没有全局必填字段，组合校验由 schema 和脚本共同约束；查询模式使用上方查询参数。

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": [],
  "properties": {
    "mention": {
      "type": "string",
      "description": "要艾特的群成员昵称或备注。按用户原话提取，不要改写。"
    },
    "mentions": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "要艾特的多个群成员昵称或备注。"
    },
    "all": {
      "type": "boolean",
      "description": "是否 @所有人。用户明确要求 @所有人或通知全体成员时设为 true，不能与 mention/mentions 同时使用。"
    },
    "content": {
      "type": "string",
      "description": "要发送的文本内容。纯文本和引用回复必须提供非空正文；只艾特不附加正文时可以省略或为空字符串。"
    },
    "refer_message_id": {
      "type": "integer",
      "minimum": 1,
      "maximum": 9223372036854775807,
      "description": "可选，仅引用时传入被引用消息的数据库主键 messages.id。仅文本、仅艾特时省略；传入后 content 必须为非空白文本。"
    },
    "ended": {
      "type": "boolean",
      "description": "是否结束当前对话。当 Agent 已经完成消息发送、要说的话已说完、要做的事已做完时，设置为 true。"
    }
  },
  "anyOf": [
    {
      "required": ["content"],
      "properties": { "content": { "pattern": "\\S" } }
    },
    {
      "required": ["mention"],
      "properties": { "mention": { "pattern": "\\S" } }
    },
    {
      "required": ["mentions"],
      "properties": {
        "mentions": { "contains": { "type": "string", "pattern": "\\S" } }
      }
    },
    {
      "required": ["all"],
      "properties": { "all": { "const": true } }
    }
  ],
  "dependencies": {
    "refer_message_id": {
      "required": ["content"],
      "properties": { "content": { "minLength": 1, "pattern": "\\S" } }
    }
  },
  "additionalProperties": false
}
```

对应命令行参数：

- `--refer-message-id <messages.id>` 可选，传入后发送引用消息，必须同时提供非空 `--content`
- `--mention <昵称或备注>` 指定成员时使用，可重复传入
- `--mentions <JSON数组>` 指定成员时可选，用于一次传入多个昵称或备注
- `--all`（也支持 `--mention-all`）可选，用于真正 @所有人，不能与 mention 参数同用
- `--content <文本内容>` 纯文本和引用消息必填，单独艾特时可省略或为空
- `--ended` 可选标志。当 Agent 已完成消息发送、要说的话已说完时传入。

## 引用消息的选择

- 仅在用户要求引用时选择消息，传给脚本的引用 ID 只使用 `messages.id`。
- 引用当前用户触发本次对话的消息时，使用环境变量 `ROBOT_MESSAGE_ID` 中的消息主键。
- 用户要求回复他引用的原消息时，使用 `ROBOT_REF_MESSAGE_ID` 中的消息主键；该值为空或 `0` 表示没有引用目标，不能改为引用当前消息。
- 引用其他历史消息时，使用本脚本 `--query-history`，在当前会话最近 24 小时内按时间、关键词、类型或发送人查找，按用户描述确认原消息后取 `id`。不要使用 `msg_id`、`client_msg_id` 或 XML 中的 `svrid`。
- 引用目标不明确时先确认，不能猜测消息 ID。不要因为上下文存在引用消息就自动发送引用回复。
- 脚本接收明确的 `--refer-message-id`，不会自动选择最近一条消息；仅引用且不指定成员时不需要查询成员表。

## 成员匹配规则

仅指定成员时执行以下匹配；`--all` 不查询成员表：

1. 只在当前群聊 `ROBOT_FROM_WX_ID` 对应的 `chat_room_members` 记录中查找。
2. 只匹配 `is_leaved` 为空或 `0` 的成员，已经退群的成员不能被艾特。
3. 使用用户给出的昵称或备注做模糊查询，字段优先级为 `remark`，然后是 `nickname`。
4. 查询到候选成员后，优先选择 `remark` 完全等于输入值的成员。
5. 如果没有完全相等的 `remark`，选择 `nickname` 完全等于输入值的成员。
6. 如果没有完全相等结果，选择第一个 `remark` 包含输入值的成员。
7. 如果仍未命中，选择第一个 `nickname` 包含输入值的成员。

## 发送执行步骤

1. 判断用户需要纯文本、仅艾特、引用回复，还是引用时同时艾特。纯文本和引用回复必须准备非空正文 `content`；引用时按上面的规则确定 `refer_message_id`。
2. 如需指定成员，把用户原话中的昵称或备注写入 `mention`/`mentions`；@所有人时设置 `all: true` 并使用 `--all`。
3. 在该技能目录执行脚本，例如：

仅艾特某人，不附加正文：

```bash
python3 scripts/send_complex_message.py --mention '张三' --ended
```

仅发送文本，不艾特、不引用：

```bash
python3 scripts/send_complex_message.py --content '收到，我来处理' --ended
```

引用当前用户消息回复：

```bash
python3 scripts/send_complex_message.py --refer-message-id "$ROBOT_MESSAGE_ID" --content '收到，我来处理' --ended
```

回复用户引用的原消息（先确认 `ROBOT_REF_MESSAGE_ID` 大于 `0`）：

```bash
python3 scripts/send_complex_message.py --refer-message-id "$ROBOT_REF_MESSAGE_ID" --content '同意这个安排'
```

引用已确认的历史消息并附带成员显示（当前客户端不产生原生艾特提醒）：

```bash
python3 scripts/send_complex_message.py --refer-message-id 12345 --mention '张三' --content '请看一下这个'
```

艾特群成员并附加正文：

```bash
python3 scripts/send_complex_message.py --mention '张三' --content '看一下这个'
```

用户要求 @所有人时传 `--all`，不要把“所有人”当成员昵称查询：

```bash
python3 scripts/send_complex_message.py --all --content '请大家查看群公告'
```

当 Agent 认为任务已完成、对话可以结束时，加上 `--ended` 标志：

```bash
python3 scripts/send_complex_message.py --mention '张三' --content '看一下这个' --ended
```

4. 指定成员时，脚本查询数据库表 `chat_room_members` 并解析微信 ID；`--all` 时跳过数据库查询，使用 `at: ["notify@all"]`。如果指定成员未命中，可以查询记忆里是否记录了对方的别称。
5. 脚本通过 `X-Private-Token` 请求头传递环境变量 `ROBOT_CLIENT_PRIVATE_TOKEN`，统一调用 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/refermessage`。请求体包含 `to_wxid`、`content`、`at`；只有引用时才添加 `refer_message_id`。不引用时，客户端直接转入普通文本发送方法；纯文本的 `at` 为空数组，仅艾特的 `content` 为空字符串。

`to_wxid` 固定取当前会话 `ROBOT_FROM_WX_ID`。技能在客户端内执行，无需携带管理后台的机器人实例 query `id`。

## 校验规则

- `ROBOT_FROM_WX_ID` 必须配置；带艾特时必须以 `@chatroom` 结尾。
- 没有艾特且正文为空或纯空白时不能发送；空的成员名称、空成员数组和 `all: false` 都不算有效艾特。
- 仅艾特时，`content` 可直接为空，由客户端根据 `at` 生成艾特正文，不添加额外话语。
- 传入 `refer_message_id` 时，必须是 int64 范围内的正整数，引用正文不能是空字符串或纯空白；不引用时直接省略 ID，不能为满足校验而猜测或自动填入消息 ID。
- `--all` 必须独占，不能再指定成员。
- 每个要艾特的人都必须能在当前群内匹配到未退群成员。
- 如果同一个微信 ID 被多个昵称命中，只会艾特一次。

## 依赖安装

- 查询历史记录或指定成员、需要查询数据库时，脚本会自动创建虚拟环境并安装依赖，使用 `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD` 连接 `ROBOT_CODE` 对应的机器人数据库；纯文本、仅引用或 `--all` 不需要安装数据库依赖。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## ended 行为

- 当 `--ended` 传入且客户端返回业务状态 `code: 200` 时，脚本在正常输出末尾追加打印独立一行 `ended`。
- `ended` 字符串必须位于输出的最末尾，前面不能跟其他字符。
- Agent 检测到输出以 `ended` 结尾时，会自动退出 Agent 循环。

## 回复要求

- 成功时，脚本输出「文本消息发送成功」「引用消息发送成功」「艾特消息发送成功」或「艾特所有人消息发送成功」，表示消息已通过客户端接口直接发送，不要再重复发送正文。
- HTTP 200 不等于业务成功，必须检查响应中的 `code`。当前引用接口成功时可能返回 `data: null`，不能因没有消息对象重发。
- 如果传入 `--ended`，输出末尾会追加 `ended`，Agent 会自动结束对话。
- 失败时，返回脚本输出的具体错误信息，不输出 `ended`。引用消息不存在、原消息内容不完整或原发送人不存在时，按客户端错误说明原因；请求超时或发送结果不确定时，不自动重试，以免重复发送。
