---
name: send-mention-message
description: "发送艾特/@/提及消息技能。当需要在当前群聊中艾特某个人或某些人，或用户要求你艾特某个/某些群成员时使用。"
argument-hint: "需要 mention；可选 content。mention 可重复传入多个昵称或备注。"
---

# Send Mention Message Skill

## 描述

这是一个在当前微信群聊中发送艾特消息的技能。

技能脚本位于 `scripts/send_mention_message.py`，会根据用户提供的昵称或备注在当前群成员表里查找未退群成员，得到微信 ID 后调用机器人客户端文本消息接口发送带 `at` 数组的消息。

## 触发条件

- 需要艾特、@、提及某个群成员或多个群成员。
- 用户要求「帮我艾特下 xxx」「@ 一下 xxx」「提一下 xxx 和 yyy」。
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
    "content": {
      "type": "string",
      "description": "要发送的文本内容，可选。只艾特不附加正文时可以为空字符串。"
    }
  },
  "anyOf": [{ "required": ["mention"] }, { "required": ["mentions"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--mention <昵称或备注>` 必填或可重复传入
- `--mentions <JSON数组>` 可选，用于一次传入多个昵称或备注
- `--content <文本内容>` 可选

## 成员匹配规则

1. 只在当前群聊 `ROBOT_FROM_WX_ID` 对应的 `chat_room_members` 记录中查找。
2. 只匹配 `is_leaved` 为空或 `0` 的成员，已经退群的成员不能被艾特。
3. 使用用户给出的昵称或备注做模糊查询，字段优先级为 `remark`，然后是 `nickname`。
4. 查询到候选成员后，优先选择 `remark` 完全等于输入值的成员。
5. 如果没有完全相等的 `remark`，选择 `nickname` 完全等于输入值的成员。
6. 如果没有完全相等结果，选择第一个 `remark` 包含输入值的成员。
7. 如果仍未命中，选择第一个 `nickname` 包含输入值的成员。

## 执行步骤

1. 判断用户是否需要在群聊中艾特某人或某些人。
2. 从用户输入中提取要艾特的昵称或备注，写入 `mention` 或 `mentions`；如用户要求附带正文，写入 `content`。
3. 在该技能目录执行脚本，例如：

```bash
python3 scripts/send_mention_message.py --mention '张三' --content '看一下这个'
```

4. 脚本会查询数据库表 `chat_room_members`，找到当前群内未退群成员的微信 ID。如果数据库没查询到这个人，你可能需要查询你的记忆，看看有没有一个人的别称叫这个名字。
5. 脚本调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/text` 发送消息，请求体包含 `to_wxid`、`content`、`at`。

## 校验规则

- `ROBOT_FROM_WX_ID` 必须是群聊 ID，通常以 `@chatroom` 结尾。
- 至少提供一个 `mention`。
- 每个要艾特的人都必须能在当前群内匹配到未退群成员。
- 如果同一个微信 ID 被多个昵称命中，只会艾特一次。

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 回复要求

- 成功时，脚本输出「艾特消息发送成功」，表示消息已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回脚本输出的具体错误信息。
