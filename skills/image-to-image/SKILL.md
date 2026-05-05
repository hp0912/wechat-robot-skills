---
name: image-to-image
description: "图片修改、图生图工具。基于输入的一张或多张图片，结合文本提示词生成新的图片。支持图片混合、风格转换、内容合成等多种创作模式。输入是文字+图片的组合，输出是图片。"
argument-hint: "需要 prompt（提示词）和 images（图片链接列表），可选 model（模型）、negative_prompt（反向提示词）、ratio（宽高比）、resolution（分辨率）"
---

# Image To Image Skill

## 描述

这是一个 AI 图生图技能，基于输入的一张或多张图片，结合文本提示词生成新的图片。支持图片混合、风格转换、内容合成等多种创作模式。

支持多个绘图模型：即梦（JiMeng）、豆包（DouBao）、造相（Z-Image）、OpenAI GPT Image。

从数据库中读取绘图配置（API 密钥、Base URL 等），根据用户选择的模型调用对应的绘图 API，返回生成的图片 URL。

这个仓库里额外提供了一个可执行脚本 `scripts/image_to_image.py`，方便宿主机器人直接调用。

## 触发条件

- 用户想基于图片生成新图片
- 用户说「把这张图变成……」「把图片修改成……」「风格转换」「图片合成」
- 用户提到「图生图」「图片编辑」「图片修改」
- 用户发送了一张或多张图片，并附带修改、合成、风格转换等描述

## 参数说明（JSON Schema）

调用脚本时，需要通过 shell 风格参数传入，参数结构如下：

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "根据用户输入的文本内容，提取出图片混合、风格转换、内容合成等等的提示词，但是不要对提示词进行修改。"
    },
    "model": {
      "type": "string",
      "description": "画图模型选择（可选）：即梦4.5(jimeng-4.5) / 即梦4.6(jimeng-4.6) / 即梦5.0(jimeng-5.0) / 豆包图生图(doubao-seededit-3.0-i2i) / 造相基础版(Z-Image) / 造相蒸馏版(Z-Image-Turbo) / 造相图片编辑(Qwen-Image-Edit-2511) / OpenAI GPT Image(gpt-image-2)，默认: 空(none)。",
      "enum": [
        "none",
        "jimeng-4.5",
        "jimeng-4.6",
        "jimeng-5.0",
        "doubao-seededit-3.0-i2i",
        "Z-Image",
        "Z-Image-Turbo",
        "Qwen-Image-Edit-2511",
        "gpt-image-2"
      ],
      "default": "none"
    },
    "images": {
      "type": "array",
      "items": { "type": "string" },
      "description": "用于图片编辑、图片混合、风格转换、内容合成等的图片链接列表，至少需要一张图像。"
    },
    "negative_prompt": {
      "type": "string",
      "description": "用于描述图像中不希望出现的元素或特征的文本，可选。"
    },
    "ratio": {
      "type": "string",
      "description": "图像的宽高比，可选，默认16:9。",
      "default": "16:9"
    },
    "resolution": {
      "type": "string",
      "description": "图像的分辨率，可选，默认2k。",
      "default": "2k"
    }
  },
  "required": ["prompt", "images"],
  "additionalProperties": false
}
```

对应的命令行参数为：

- `--prompt <提示词>` 必填
- `--images <图片链接>` 必填，可重复传入多张图片，如 `--images url1 --images url2`
- `--model <模型名>` 可选
- `--negative_prompt <反向提示词>` 可选
- `--ratio <宽高比>` 可选
- `--resolution <分辨率>` 可选

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 scripts/bootstrap.py`

## 执行步骤

1. 当用户发送图片并附带修改、合成、风格转换等描述时触发该技能。
2. 从用户输入中提取 prompt（提示词），不对提示词做总结或修改。提取 images（图片链接列表）。可选提取 model、negative_prompt、ratio、resolution 参数。
3. 将参数组装为 shell 风格命令行参数，在仓库根目录下执行本地脚本，例如：`python3 scripts/image_to_image.py --prompt '把这张图变成油画风格' --images 'https://example.com/img1.jpg' --images 'https://example.com/img2.jpg' --model jimeng-5.0`。
4. 脚本生成图片后会自动调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/url` 将图片发送给用户，成功时输出「图片发送成功」。

## 回复要求

- 成功时，脚本输出「图片发送成功」，表示图片已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回具体的失败信息。
