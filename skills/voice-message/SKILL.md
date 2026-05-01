---
name: voice-message
description: "文本转语音与语音消息发送技能。当用户想让我说话、发语音、把一段话转成语音、用某种情绪/音色/语速/方言读出来时使用。支持 content、emotion、voice、style_prompt、voice_prompt、audio_tags、context_texts 等通用参数，并自动把合成结果作为语音消息发给当前会话。"
argument-hint: "需要 content；可选 emotion、voice、style_prompt、voice_prompt、audio_tags、context_texts、speaking_rate、pitch、volume、dialect。"
---

# Voice Message Skill

## 描述

这是一个将文本合成为语音并直接发送到当前微信会话的技能。

技能脚本位于 `scripts/voice_message.py`。

## 触发条件

- 用户想让你发语音、说一句话、用语音回复。
- 用户说「把这句话读出来」「帮我发个语音」「用开心一点的语气说」。
- 用户要求指定音色、语速、音量、方言、角色感、播报风格或音频标签。
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
      "description": "可选，用户明确要求的情绪或整体风格词，例如 happy、tender、开心、委屈、慵懒、磁性。不要为了适配供应商而改写。"
    },
    "voice": {
      "type": "string",
      "description": "可选，用户明确指定的音色名、speaker 名或供应商配置中约定的 voice 名称，例如 Chloe、冰糖、mimo_default。不要把“女声”“低沉”这类描述放在这里，应放到 voice_prompt。"
    },
    "voice_prompt": {
      "type": "string",
      "description": "可选，声线/音色描述，例如“年轻女性，声音清亮，语气温柔但带一点疲惫”。适合文本音色设计，也会作为其他供应商的辅助风格提示。"
    },
    "context_texts": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，语音合成辅助信息或对话上下文。仅在需要补充语境、人物状态、说话方式时使用。"
    },
    "style_prompt": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，自然语言风格/导演提示，例如“语速稍快，尾音上扬，像刚查到好成绩一样压不住开心”。可重复传入。"
    },
    "audio_tags": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，音频标签或整体标签，例如“粤语”“唱歌”“轻笑”“深呼吸”。仅当用户明确要求标签、方言、唱歌、笑声、停顿等细粒度控制时传入。"
    },
    "speaking_rate": {
      "type": "string",
      "description": "可选，语速要求，例如“偏慢”“稍快”“像连珠炮”。"
    },
    "pitch": {
      "type": "string",
      "description": "可选，音高要求，例如“更低沉”“明亮上扬”。"
    },
    "volume": {
      "type": "string",
      "description": "可选，音量或力度要求，例如“小声耳语”“提高音量喊话”。"
    },
    "dialect": {
      "type": "string",
      "description": "可选，方言或口音要求，例如“粤语”“四川话”“东北话”“轻微台湾腔”。"
    }
  },
  "required": ["content"],
  "additionalProperties": false
}
```

对应命令行参数：

- `--content <文本>` 必填
- `--emotion <情绪/风格>` 可选
- `--voice <音色名或 speaker 名>` 可选
- `--voice_prompt <声线/音色描述>` 可选
- `--style_prompt <自然语言风格提示>` 可选，可重复传入多次
- `--audio_tags <音频标签>` 可选，可重复传入多次
- `--context_texts <辅助文本>` 可选，可重复传入多次
- `--speaking_rate <语速>` 可选
- `--pitch <音高>` 可选
- `--volume <音量>` 可选
- `--dialect <方言/口音>` 可选

## 参数抽取规则

1. `content` 必须来自用户明确想让你说出的内容，不要加入寒暄、解释或额外总结。
2. 如果用户只说“你用语音回复我”但没有提供具体要说的话，应先基于上下文生成一段简洁、自然、适合直接播报的回复，再把这段回复作为 `content`。
3. 不要判断当前使用的是哪个语音供应商，也不要为了供应商改写参数；只按用户意图提取通用参数，脚本会自动映射。
4. 只有当用户明确要求情绪或语气时才传 `emotion`。`emotion` 可以是中文或英文短词，不必限制在某个供应商枚举内。
5. 用户指定明确音色名时用 `voice`；用户描述“女声、低沉、御姐音、年轻男性”等声线质感时用 `voice_prompt`。
6. 语速、音高、音量、方言有明确要求时优先填 `speaking_rate`、`pitch`、`volume`、`dialect`；复杂演绎要求放入 `style_prompt`。
7. `audio_tags` 仅用于用户明确要求唱歌、方言、笑声、停顿、深呼吸等标签化控制时；如果用户已把标签写在 `content` 中，不要重复添加。
8. `context_texts` 适合表达上下文、场景、人物状态和补充播报要求。
9. 不要传递音色复刻音频参数。若当前消息引用了一条语音消息，脚本会通过 `ROBOT_REF_MESSAGE_ID` 自动判断并下载引用语音作为复刻样本。
10. `content` 超过 260 个字符时，不应该调用本技能。

## 执行步骤

1. 识别用户是否明确需要语音消息。
2. 提取 `content`，可选提取 `emotion`、`voice`、`voice_prompt`、`style_prompt`、`audio_tags`、`context_texts` 等通用控制参数。
3. 在仓库根目录执行：

```bash
python3 skills/voice-message/scripts/voice_message.py --content '这是一条语音消息' --emotion happy --style_prompt '请自然一点'
```

4. 脚本会读取数据库中的 TTS 配置，按当前供应商能力映射通用参数，调用语音合成接口并通过客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/voice` 直接发送语音。

## 供应商映射说明

- Doubao：`content` 写入文本字段；支持的 `emotion` 写入音频情绪参数；`voice` 可覆盖 speaker；其他风格控制会合并到 `context_texts` 辅助信息。
- MiMo V2.5：`content` 写入 `assistant` 消息；`style_prompt`、`voice_prompt`、`context_texts`、`emotion`、`speaking_rate`、`pitch`、`volume`、`dialect` 会合并为 `user` 风格/音色控制；`audio_tags` 会作为整体标签加到要合成的文本前。
- MiMo 会默认使用非流式 `wav` 输出；配置中 `stream: true` 时使用 `pcm16` 流式兼容模式并在脚本内封装为 `wav`。
- MiMo 在 `auto_model` 未关闭时，会根据 `voice_prompt` 自动选择 `mimo-v2.5-tts-voicedesign`；如果 `ROBOT_REF_MESSAGE_ID` 指向数据库中 `messages.type = 34` 的语音消息，则脚本会调用客户端接口下载该语音 wav，并自动选择 `mimo-v2.5-tts-voiceclone`。
- 引用消息下载接口为 `GET http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/voice/download?message_id={ROBOT_REF_MESSAGE_ID}`，返回 wav 后由脚本封装为 MiMo 需要的 `data:audio/wav;base64,...`。

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 回复要求

- 成功时，脚本输出「ended」，表示语音已直接发送，无需 AI 智能体再拼装额外消息。
- 失败时，返回脚本输出的具体错误信息。
