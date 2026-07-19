---
name: send-mention-message
description: "在当前微信群聊中发送真正的艾特/@/提及消息。用户要求 @ 某个或多个群成员、@所有人、提醒全体成员、通知群里所有人时使用；支持附带正文并可在发送后结束当前 Agent 对话。"
---

# Send Mention Message Skill

## 描述

在当前微信群聊中发送艾特消息，支持指定一个或多个成员，也支持微信原生的 `@所有人`。

技能脚本位于 `scripts/send_mention_message.py`。指定成员时，根据昵称或备注查询当前群内未退群成员；@所有人时直接使用客户端协议值 `notify@all`。两种模式最终都会调用文本消息接口发送真正的 `at` 数组，不要把 `@昵称` 或 `@所有人` 当普通正文拼接。

## 触发条件

- 需要艾特、@、提及某个群成员或多个群成员。
- 用户要求「帮我艾特下 xxx」「@ 一下 xxx」「提一下 xxx 和 yyy」。
- 用户要求「@所有人」「提醒全体成员」「通知群里所有人」。
- 需要在群聊里点名提醒某人。

私聊场景一般不触发本技能；脚本会校验 `ROBOT_FROM_WX_ID` 必须是群聊 ID。

## 入参规范

```json
{
  "type": "object",
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
      "description": "要发送的文本内容，可选。只艾特不附加正文时可以为空字符串。"
    },
    "ended": {
      "type": "boolean",
      "description": "是否结束当前对话。当 Agent 已经完成艾特和消息发送、要说的话已说完、要做的事已做完时，设置为 true。"
    }
  },
  "anyOf": [{ "required": ["mention"] }, { "required": ["mentions"] }, { "required": ["all"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--mention <昵称或备注>` 指定成员时使用，可重复传入
- `--mentions <JSON数组>` 指定成员时可选，用于一次传入多个昵称或备注
- `--all`（也支持 `--mention-all`）可选，用于真正 @所有人，不能与 mention 参数同用
- `--content <文本内容>` 可选
- `--ended` 可选标志。当 Agent 已完成艾特和消息发送、要说的话已说完时传入。

## 成员匹配规则

仅指定成员时执行以下匹配；`--all` 不查询成员表：

1. 只在当前群聊 `ROBOT_FROM_WX_ID` 对应的 `chat_room_members` 记录中查找。
2. 只匹配 `is_leaved` 为空或 `0` 的成员，已经退群的成员不能被艾特。
3. 使用用户给出的昵称或备注做模糊查询，字段优先级为 `remark`，然后是 `nickname`。
4. 查询到候选成员后，优先选择 `remark` 完全等于输入值的成员。
5. 如果没有完全相等的 `remark`，选择 `nickname` 完全等于输入值的成员。
6. 如果没有完全相等结果，选择第一个 `remark` 包含输入值的成员。
7. 如果仍未命中，选择第一个 `nickname` 包含输入值的成员。

## 执行步骤

1. 判断用户是否需要在群聊中艾特某人或某些人。
2. 指定成员时，把用户原话中的昵称或备注写入 `mention`/`mentions`；@所有人时设置 `all: true` 并使用 `--all`。如用户要求附带正文，写入 `content`。
3. 在该技能目录执行脚本，例如：

```bash
python3 scripts/send_mention_message.py --mention '张三' --content '看一下这个'
```

用户要求 @所有人时传 `--all`，不要把“所有人”当成员昵称查询：

```bash
python3 scripts/send_mention_message.py --all --content '请大家查看群公告'
```

当 Agent 认为任务已完成、对话可以结束时，加上 `--ended` 标志：

```bash
python3 scripts/send_mention_message.py --mention '张三' --content '看一下这个' --ended
```

4. 指定成员时，脚本查询数据库表 `chat_room_members` 并解析微信 ID；`--all` 时跳过数据库查询，直接发送 `at: ["notify@all"]`。如果指定成员未命中，可以查询记忆里是否记录了对方的别称。
5. 脚本调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/text` 发送消息，请求体包含 `to_wxid`、`content`、`at`。

## 校验规则

- `ROBOT_FROM_WX_ID` 必须是群聊 ID，通常以 `@chatroom` 结尾。
- 至少提供一个 `mention`/`mentions`，或者传 `--all`。
- `--all` 必须独占，不能再指定成员。
- 每个要艾特的人都必须能在当前群内匹配到未退群成员。
- 如果同一个微信 ID 被多个昵称命中，只会艾特一次。

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## ended 行为

- 当 `--ended` 传入时，脚本在正常输出末尾追加打印独立一行 `ended`。
- `ended` 字符串必须位于输出的最末尾，前面不能跟其他字符。
- Agent 检测到输出以 `ended` 结尾时，会自动退出 Agent 循环。

## 回复要求

- 成功时，脚本输出「艾特消息发送成功」或「艾特所有人消息发送成功」，表示消息已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 如果传入 `--ended`，输出末尾会追加 `ended`，Agent 会自动结束对话。
- 失败时，返回脚本输出的具体错误信息。
