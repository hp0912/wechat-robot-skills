---
name: image-recognition
description: "AI 图像识别工具。当用户提供图片并希望识别、描述、提取文字、分析画面内容或回答图片相关问题时使用。"
---

# Image Recognition Skill

## 描述

这是一个 AI 图像识别技能，输入一张图片 URL 或本地图片路径和识别提示词，输出模型对图片的识别、描述或分析结果。

该技能从数据库读取当前会话的聊天 AI 配置：`chat_base_url`、`chat_api_key` 和 `image_recognition_model`，并调用 OpenAI 兼容的多模态 Chat Completions 接口完成图片理解。

这个仓库里额外提供了一个可执行脚本 `scripts/image_recognition.py`，方便宿主机器人直接调用。

## 触发条件

- 用户发送图片并要求描述图片内容。
- 用户说「识别这张图」「看看图片里有什么」「分析一下这张图片」。
- 用户要求从图片中提取文字、物体、场景、人物动作、票据内容等信息。
- 用户基于图片提问，例如「这是什么」「图片里的文字是什么」「这张图哪里不对」。

## 参数说明（JSON Schema）

调用脚本时，需要通过 shell 风格参数传入，参数结构如下：

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "图像识别提示词，用户想从图片中得到什么信息。"
    },
    "image_url": {
      "type": "string",
      "description": "图片的 URL 地址、本地图片路径，或 file:// 本地图片地址。"
    }
  },
  "required": ["prompt", "image_url"],
  "additionalProperties": false
}
```

对应的命令行参数为：

- `--prompt <识别提示词>` 必填
- `--image_url <图片 URL 或本地图片路径>` 必填

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 执行步骤

1. 当用户提供图片并要求识别、描述、分析或提取信息时触发该技能。
2. 从用户输入中提取 `prompt` 和 `image_url`，不要改写用户真正想问图片的问题。
3. 在仓库根目录执行脚本，例如：

```bash
python3 scripts/image_recognition.py --prompt '请描述这张图片' --image_url 'https://example.com/image.jpg'
```

本地图片示例：

```bash
python3 scripts/image_recognition.py --prompt '请提取图片里的文字' --image_url '/tmp/example.jpg'
```

4. 脚本将图片和提示词一起发送给 OpenAI 兼容的多模态模型。远程图片会直接传 URL；本地图片会转成 `data:image/...;base64,...` 后传入 `image_url.url`。
5. 成功时，脚本输出图像识别结果文本，宿主机器人可直接作为消息回复给用户。

## 校验规则

- `prompt` 不能为空。
- `image_url` 不能为空，支持 `http://`、`https://`、`file://` 和本地图片路径。

## 回复要求

- 成功时，脚本输出图片识别结果。
- 失败时，返回脚本输出的具体错误信息。
