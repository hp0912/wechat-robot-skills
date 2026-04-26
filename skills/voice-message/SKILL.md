---
name: voice-message
description: "文本转语音与语音消息发送技能。当用户想让我说话、发语音、把一段话转成语音、用某种情绪读出来时使用。支持 content、emotion、context_texts 参数，并自动把合成结果作为语音消息发给当前会话。"
argument-hint: "需要 content；可选 emotion、context_texts。context_texts 可重复传入。"
---

# Voice Message Skill

## 描述

这是一个将文本合成为语音并直接发送到当前微信会话的技能。

技能脚本位于 `cripts/voice_message.py`。

## 触发条件

- 用户想让你发语音、说一句话、用语音回复。
- 用户说「把这句话读出来」「帮我发个语音」「用开心一点的语气说」。
- 用户明确要求文本转语音。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "content": {
      "type": "string",
      "description": "要转成语音的文本内容。必须保留用户原意，不要无故扩写。最长 260 个字符。"
    },
    "emotion": {
      "type": "string",
      "description": "可选，输出语音的情绪类型。仅在用户明确要求语气、情绪或声线风格时传入。",
      "enum": [
        "happy",
        "sad",
        "angry",
        "surprised",
        "fear",
        "hate",
        "excited",
        "lovey-dovey",
        "shy",
        "comfort",
        "tension",
        "tender",
        "magnetic",
        "vocal-fry",
        "ASMR"
      ]
    },
    "context_texts": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，语音合成辅助信息。仅在需要引导语速、情绪、音量、说话方式时使用，例如“你可以说慢一点吗？”“你用很委屈的语气说”。"
    }
  },
  "required": ["content"],
  "additionalProperties": false
}
```

对应命令行参数：

- `--content <文本>` 必填
- `--emotion <情绪>` 可选
- `--context_texts <辅助文本>` 可选，可重复传入多次

## 参数抽取规则

1. `content` 必须来自用户明确想让你说出的内容，不要加入寒暄、解释或额外总结。
2. 如果用户只说“你用语音回复我”但没有提供具体要说的话，应先基于上下文生成一段简洁、自然、适合直接播报的回复，再把这段回复作为 `content`。
3. 只有当用户明确要求情绪或语气时才传 `emotion`。
4. `context_texts` 适合表达细粒度播报要求，优先用于语速、语调、音量、说话状态的补充说明。
5. `content` 超过 260 个字符时，不应该调用本技能。

## 执行步骤

1. 识别用户是否明确需要语音消息。
2. 提取 `content`，可选提取 `emotion`、`context_texts`。
3. 在仓库根目录执行：

```bash
python3 scripts/voice_message.py --content '这是一条语音消息' --emotion happy --context_texts '请自然一点'
```

4. 脚本会读取数据库中的 TTS 配置，调用语音合成接口并通过客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/voice` 直接发送语音。

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 回复要求

- 成功时，脚本输出「ended」，表示语音已直接发送，无需 AI 智能体再拼装额外消息。
- 失败时，返回脚本输出的具体错误信息。
