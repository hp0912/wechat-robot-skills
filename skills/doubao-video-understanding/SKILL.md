---
name: doubao-video-understanding
description: "豆包视频解析理解工具。当用户提供一个视频链接并希望获得视频的详细描述、总结或理解时使用。"
argument-hint: "需要 prompt、video_url；可选 fps、max_tokens。"
---

# Doubao Video Understanding Skill

## 描述

这是一个 AI 视频解析理解技能，输入一个视频链接，输出视频的详细描述、总结，或对视频内容的理解。

脚本会先从数据库读取当前会话的图像 AI 配置开关，再读取对应的 `image_recognition_model` 作为理解模型，并使用环境变量中的 `ARK_API_KEY` 调用 Ark 多模态对话接口完成视频分析。

这个仓库里额外提供了一个可执行脚本 `scripts/video_understanding.py`，方便宿主机器人直接调用。

## 触发条件

- 用户发来一个视频链接，并要求描述视频内容。
- 用户说「总结这个视频」「帮我理解这个视频」「分析一下这个视频讲了什么」。
- 用户希望获取视频的详细描述、核心摘要、主题理解。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "可选的分析指令。默认会要求模型输出详细描述、总结和理解。"
    },
    "video_url": {
      "type": "string",
      "description": "需要解析的视频链接，必须是 https 地址。"
    },
    "fps": {
      "type": "integer",
      "description": "抽帧频率，可选，默认 2。"
    },
    "max_tokens": {
      "type": "integer",
      "description": "模型输出最大 token 数，可选，默认 800。"
    }
  },
  "required": ["prompt", "video_url"],
  "additionalProperties": false
}
```

对应的命令行参数为：

- `--prompt <分析指令>` 必填
- `--video_url <视频链接>` 必填，必须是 `https` 地址
- `--fps <抽帧频率>` 可选
- `--max_tokens <最大输出 token 数>` 可选

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 执行步骤

1. 当用户提供视频链接并要求描述、总结或理解时触发该技能。
2. 提取 `prompt` 用户需求和 `video_url` 视频链接。可选提取 `fps`、`max_tokens`。
3. 在仓库根目录执行脚本，例如：

```bash
python3 scripts/video_understanding.py --prompt '请描述这个视频' --video_url 'https://example.com/demo.mp4'
```

4. 脚本会从数据库读取 `image_ai_enabled` 和 `image_recognition_model`。模型读取顺序为：当前会话覆盖配置优先，其次全局配置；如果表字段不存在，则回退到 `image_ai_settings` JSON 中的同名字段。
5. 脚本调用 `https://ark.cn-beijing.volces.com/api/v3/chat/completions`，将视频链接和分析指令一起发送给视觉模型。
6. 成功时，脚本输出文本结果，宿主机器人可直接作为消息回复给用户。

## 校验规则

- `prompt` 不能为空。
- `video_url` 不能为空，且必须是 `https` 链接。
- `fps` 必须大于 0。
- `max_tokens` 必须大于 0。
- 环境变量 `ARK_API_KEY` 必须存在。
- 数据库里必须开启图像 AI 能力，并能解析出 `image_recognition_model`。

## 回复要求

- 成功时，脚本输出视频理解结果。
- 失败时，返回脚本输出的具体错误信息。
