---
name: send-emoji
description: "发送微信表情技能。使用表情包聊天可以活跃气氛，也可以缓解尴尬，你可以在适当的时候发送表情包活跃聊天氛围，也可以在遇到不方便回答或者不想回答的问题时发送一个表情包来巧妙地回避。"
argument-hint: "需要 name，如 [开心]、[快乐]；可重复传入发送多个表情。ended 标志表示发送表情后结束对话。"
---

# Send Emoji Skill

## 描述

这是一个向当前微信会话发送表情的技能。

技能脚本位于 `scripts/send_emoji.py`，内部维护了表情名称到微信表情标识（Md5 / TotalLen）的映射表，对 AI 模型完全透明——模型只需传入表情名称（如 `[开心]`），脚本自动查找对应的 Md5 和 TotalLen 并调用客户端接口发送。

## 可用表情

| 名称         |
| ------------ |
| `[调皮]`     |
| `[无语]`     |
| `[爱心]`     |
| `[安慰]`     |
| `[嘲笑]`     |
| `[傻瓜]`     |
| `[厉害]`     |
| `[生气]`     |
| `[开心]`     |
| `[打你]`     |
| `[禁止色色]` |

> 表情映射表在脚本 `EMOJI_MAP` 中维护，新增表情直接在脚本中添加即可，无需修改 SKILL.md。

## 触发条件

- Agent 根据上下文判断需要用表情回应时，从可用表情列表中选取合适的。
- 用户要求发送某个微信表情。
- 用户说「发个表情」「来个表情包」「发个 xxx 表情」。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "name": {
      "type": "string",
      "description": "表情名称，如 [开心]、[快乐]。必须是可用表情列表中的名称。"
    },
    "names": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，多个表情名称数组。"
    },
    "ended": {
      "type": "boolean",
      "description": "是否结束当前对话。当 Agent 已经完成表情发送、要说的话已说完、要做的事已做完时，设置为 true。"
    }
  },
  "anyOf": [{ "required": ["name"] }, { "required": ["names"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--name <表情名称>` 必填或可重复传入，如 `--name '[开心]'`
- `--names <JSON数组>` 可选，用于一次传入多个名称，如 `--names '["[开心]", "[快乐]"]'`
- `--ended` 可选标志

```bash
python3 scripts/send_emoji.py --name '[开心]'
```

发送多个表情：

```bash
python3 scripts/send_emoji.py --name '[开心]' --name '[快乐]'
```

当 Agent 认为任务已完成、对话可以结束时，加上 `--ended` 标志：

```bash
python3 scripts/send_emoji.py --name '[开心]' --ended
```

4. 脚本在内部查找 EMOJI_MAP，获取对应的 Md5 和 TotalLen，调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/emoji` 发送表情。

## 校验规则

- `name` 必须存在于脚本内置的 EMOJI_MAP 中，否则脚本会报错并列出可用表情。
- 环境变量 `ROBOT_WECHAT_CLIENT_PORT` 和 `ROBOT_FROM_WX_ID` 必须已配置。

## ended 行为

- 当 `--ended` 传入时，脚本在正常输出末尾追加打印独立一行 `ended`。
- Agent 检测到输出以 `ended` 结尾时，会自动退出 Agent 循环结束对话。

## 回复要求

- 成功时，脚本输出「表情发送成功」，表示表情已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 如果传入 `--ended`，输出末尾会追加 `ended`，Agent 会自动结束对话。
- 不要发送`表情发送成功啦` `已经发送表情啦` `工具调用成功` 这类无意义的回复，如果你只有这类话要说，请在输出末尾加上 `ended`，让 Agent 结束对话。
- 失败时，返回脚本输出的具体错误信息（含可用表情列表）。
