---
name: voice-message
description: "文本转语音与语音消息发送技能。当用户想让我说话、发语音、把一段话转成语音、用某种情绪/音色/语速/方言读出来时使用。支持 content、emotion、voice、style_prompt、voice_prompt、audio_tags、context_texts 等通用参数，并自动把合成结果作为语音消息发给当前会话。"
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
9. 不要传递音色复刻音频参数。若当前消息引用了一条语音消息，脚本会通过 `ROBOT_REF_MESSAGE_ID`（数据库 `messages.id`）自动判断并下载引用语音作为复刻样本。
10. `content` 超过 260 个字符时，不应该调用本技能。

## 音频标签控制

通过在文本中嵌入风格标签与音频标签，直接对语音进行精细控制。开头是整体风格标签，中间可以插入细粒度控制标签。

在目标文本开头添加 `(风格)` 标签，即可指定语音的发音风格。支持同时设置多种风格，将多个风格名称置于同一对括号内，分隔符不限。

支持的括号格式： 可使用半角 `()`、全角 `（）` 或 `[]`。

### 格式示例

```
风格类型	风格示例
基础情绪	开心/悲伤/愤怒/恐惧/惊讶/兴奋/委屈/平静/冷漠
复合情绪	怅然/欣慰/无奈/愧疚/释然/嫉妒/厌倦/忐忑/动情
整体语调	温柔/高冷/活泼/严肃/慵懒/俏皮/深沉/干练/凌厉
音色定位	磁性/醇厚/清亮/空灵/稚嫩/苍老/甜美/沙哑/醇雅
人设腔调	夹子音/御姐音/正太音/大叔音/台湾腔
方言	   东北话/四川话/河南话/粤语
角色扮演	孙悟空/林黛玉
唱歌	   唱歌
```

样例:

- (怅然)这么多年过去了，再走过那条街，心里一下子空了一块。

- (慵懒)再让我睡五分钟……就五分钟，真的，最后一次。

- (磁性)夜已经深了，城市还在呼吸。我是今晚陪你的人，欢迎收听《午夜电台》。

- (东北话)哎呀妈呀，这天儿也忒冷了吧！你说这风，嗖嗖的，跟刀子似的，割脸啊！

- (粤语)呢个真係好正啊！食过一次就唔会忘记！

- (唱歌)原谅我这一生不羁放纵爱自由，也会怕有一天会跌倒，Oh no。背弃了理想，谁人都可以，哪会怕有一天只你共我。

在此基础上，我们还支持在文本中任意位置插入 [音频标签]。通过 [音频标签] ，你可以对声音进行细粒度控制，精准调节语气、情绪和表达风格——无论是低声耳语、放声大笑，还是带点小情绪的小吐槽，也可以灵活插入呼吸声，停顿，咳嗽等，都能轻松实现。语速同样可以灵活调整，让每句话都有它该有的节奏。

```
风格类型	风格示例
语速与节奏	吸气/深呼吸/叹气/长叹一口气/喘息/屏息
情绪状态	  紧张/害怕/激动/疲惫/委屈/撒娇/心虚/震惊/不耐烦
语音特征	  颤抖/声音颤抖/变调/破音/鼻音/气声/沙哑
哭笑表达	  笑/轻笑/大笑/冷笑/抽泣/呜咽/哽咽/嚎啕大哭
```

样例:

- （紧张，深呼吸）呼……冷静，冷静。不就是一个面试吗……（语速加快，碎碎念）自我介绍已经背了五十遍了，应该没问题的。加油，你可以的……（小声）哎呀，领带歪没歪？

- （极其疲惫，有气无力）师傅……到地方了叫我一声……（长叹一口气）我先眯一会儿，这班加得我魂儿都要散了。

- 如果我当时……（沉默片刻）哪怕再坚持一秒钟，结果是不是就不一样了？（苦笑）呵，没如果了。

- （寒冷导致的急促呼吸）呼——呼——这、这大兴安岭的雪……（咳嗽）简直能把人骨头冻透了……别、别停下，走，快走。

- （提高音量喊话）大姐！这鱼新鲜着呢！早上刚捞上来的！哎！那个谁，别乱翻，压坏了你赔啊？！

### 特别注意

- 只有`mimo-v2.5-tts`模型支持唱歌模式

- 唱歌请求使用预置音色模型；音色设计、音色复刻均不支持唱歌。用户同时要求引用语音克隆和唱歌时，说明该组合不受支持。

- 如需体验更佳的唱歌风格，必须在目标文本最开头添加 `(唱歌)` 标签，格式为：`(唱歌)歌词`。歌词 建议采用中文，可获得更优合成效果。标签内标识支持以下取值，效果等效：`唱歌`、`sing`、`singing`

## 执行步骤

1. 识别用户是否明确需要语音消息。
2. 提取 `content`，可选提取 `emotion`、`voice`、`voice_prompt`、`style_prompt`、`audio_tags`、`context_texts` 等通用控制参数。
3. 在仓库根目录执行：

```bash
python3 scripts/voice_message.py --content '这是一条语音消息' --emotion happy --style_prompt '请自然一点'
```

4. 脚本会读取数据库中的 TTS 配置，按当前供应商能力映射通用参数，调用语音合成接口并通过客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/voice` 直接发送语音。

## 供应商映射说明

- Doubao：`content` 写入文本字段；支持的 `emotion` 写入音频情绪参数；`voice` 可覆盖 speaker；其他风格控制会合并到 `context_texts` 辅助信息。
- MiMo V2.5：`content` 写入 `assistant` 消息；`style_prompt`、`voice_prompt`、`context_texts`、`emotion`、`speaking_rate`、`pitch`、`volume`、`dialect` 会合并为 `user` 风格/音色控制；`audio_tags` 会作为整体标签加到要合成的文本前。
- MiMo 默认使用非流式 `wav` 输出；配置中 `stream: true` 时使用 `pcm16` 并在脚本内封装为 24 kHz、单声道 `wav`。普通朗读支持低延迟流式；音色设计、复刻当前在推理完成后以流式格式返回结果。
- 引用语音消息时，按 `messages.id` 查询引用消息并检查 `type = 34`，下载 wav 后选择 `mimo-v2.5-tts-voiceclone`。例如引用一条语音并要求「用这个声音说：晚上好」。也可在后台配置固定的 `voice_clone_audio` 样本。
- 上下文明确指定预置 `voice` 时使用普通朗读模型；`auto_model` 开启时，上下文的 `voice_prompt` 选择音色设计模型。这些明确要求优先于配置中的默认复刻样本和音色描述。
- 引用消息下载接口为 `GET http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/voice/download?message_id={ROBOT_REF_MESSAGE_ID}`，返回 wav 后由脚本封装为 MiMo 需要的 `data:audio/wav;base64,...`。

## MiMo 配置

管理后台文本转语音配置中的 `mimo` 提供以下参数，不需要填写 `model`。模型由脚本根据上下文提取的音色描述、复刻音频等信息自动选择。

```json
{
  "base_url": "https://api.xiaomimimo.com/v1",
  "api_key": "",
  "voice": "mimo_default",
  "audio_format": "wav",
  "stream": false,
  "timeout": 300,
  "auto_model": true,
  "voice_prompt": "",
  "style_prompt": [],
  "context_texts": [],
  "audio_tags": [],
  "emotion": "",
  "speaking_rate": "",
  "pitch": "",
  "volume": "",
  "dialect": "",
  "voice_clone_audio": "",
  "voice_clone_mime_type": "audio/mpeg"
}
```

- `base_url`、`api_key` 用于语音合成请求；留空时沿用聊天接口的对应配置。`timeout` 是请求超时秒数，必须大于 0。
- `voice`、`voice_prompt`、`emotion`、`speaking_rate`、`pitch`、`volume`、`dialect`、`audio_tags` 是默认语音控制值，上下文明确指定时优先使用上下文参数；`style_prompt` 和 `context_texts` 会与上下文提供的内容合并。
- `audio_format` 控制非流式输出格式；`stream: true` 时使用 `pcm16` 并封装为 `wav`。PCM 采样率按接口协议处理。
- `voice_clone_audio` 支持 MP3/WAV 的 Base64 或音频 data URL，Base64 内容不能超过 10 MB。仅填写 Base64 时，使用 `voice_clone_mime_type` 指定类型：`audio/mpeg`、`audio/mp3` 或 `audio/wav`。引用语音优先于配置样本，格式错误、数据为空或超限时会在调用 MiMo 前报错。
- `auto_model` 默认开启，优先使用上下文的音色要求，再按配置样本、配置音色描述、普通朗读的顺序选择；关闭时使用普通朗读模型。引用语音始终选择音色复刻；唱歌使用普通朗读模型，与引用语音克隆冲突时会明确报错。

协议依据：[小米 MiMo V2.5 语音合成官方文档](https://mimo.mi.com/docs/zh-CN/quick-start/usage-guide/audio/speech-synthesis-v2.5)。

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 回复要求

- 成功时，脚本输出「ended」，表示语音已直接发送，无需 AI 智能体再拼装额外消息。
- 失败时，返回脚本输出的具体错误信息。
