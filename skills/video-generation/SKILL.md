---
name: video-generation
description: "AI 视频生成工具。当用户想生成视频、文生视频、图生视频、让图片动起来、指定首帧尾帧生成视频时使用。支持纯文本生成视频，或使用 1 张图片作为首帧、2 张图片作为首帧和尾帧。"
argument-hint: "需要 prompt；可选 model、file_paths、ratio、resolution、duration。file_paths 最多 2 个。"
---

# Video Generation Skill

## 描述

这是一个 AI 视频生成技能，覆盖两类常见场景：

- 文生视频：用户只提供文本描述。
- 图生视频：用户提供 1 张首帧图，或 2 张首尾帧图，再结合提示词生成视频。

当前实现对接即梦视频接口，从数据库中的绘图配置读取 `base_url`、`sessionid` 等信息。脚本生成成功后会直接调用机器人客户端接口发送视频，不再输出固定的 XML 视频标签。

## 触发条件

- 用户想生成视频、做一段短视频、让画面动起来。
- 用户说「生成一个视频」「做个视频」「把这张图做成视频」「首帧是这张图」「尾帧用这张图」。
- 用户提到「文生视频」「图生视频」「首帧尾帧视频」「AI 视频生成」。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "根据用户输入的文本内容，提取出生成视频的提示词，但是不要对提示词进行修改。"
    },
    "model": {
      "type": "string",
      "description": "视频模型选择，可选，默认 none。",
      "enum": [
        "none",
        "jimeng-video-seedance-2.0",
        "jimeng-video-3.5-pro",
        "jimeng-video-veo3",
        "jimeng-video-veo3.1",
        "jimeng-video-sora2",
        "jimeng-video-3.0-pro",
        "jimeng-video-3.0",
        "jimeng-video-3.0-fast"
      ],
      "default": "none"
    },
    "file_paths": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "用于视频首尾帧的图片地址列表，可选。0 个表示文生视频，1 个表示首帧图生视频，2 个表示首尾帧图生视频。最多 2 个。"
    },
    "ratio": {
      "type": "string",
      "description": "视频比例，可选，默认 4:3。",
      "default": "4:3"
    },
    "resolution": {
      "type": "string",
      "description": "视频分辨率，可选，默认 720p。",
      "default": "720p"
    },
    "duration": {
      "type": "integer",
      "description": "视频时长，单位秒，可选，默认 5。",
      "default": 5
    }
  },
  "required": ["prompt"],
  "additionalProperties": false
}
```

对应的命令行参数为：

- `--prompt <提示词>` 必填
- `--model <模型名>` 可选
- `--file_paths <图片地址>` 可选，可重复传入 0 到 2 次
- `--ratio <比例>` 可选
- `--resolution <分辨率>` 可选
- `--duration <秒数>` 可选

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 执行步骤

1. 当用户想生成视频时触发该技能。
2. 从用户输入中提取 `prompt`，不要改写提示词本身。
3. 根据上下文可选提取 `model`、`file_paths`、`ratio`、`resolution`、`duration`。
4. 如果用户没有明确指定模型，默认使用 `jimeng-video-3.0-fast`。
5. 在仓库根目录执行脚本，例如：

```bash
python3 scripts/video_generation.py --prompt '海边日落，镜头缓慢推进' --file_paths 'https://example.com/start.jpg'
```

6. 脚本生成视频后会自动调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/video/url` 将视频发送给用户，成功时输出「ended」。

## 校验规则

- `prompt` 不能为空。
- `file_paths` 最多只能有 2 个。
- 目前只支持即梦视频模型。
- 若数据库里关闭了 AI 绘图能力或即梦配置不可用，脚本会直接返回明确错误。

## 回复要求

- 成功时，脚本输出「ended」，表示视频已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回脚本输出的具体错误信息。
