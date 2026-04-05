---
name: text-to-image
description: "AI绘图工具，当用户想通过文本生成图像时，可以调用该工具。根据用户输入内容提取画图提示词，选择合适的模型进行绘图，返回生成的图片。"
argument-hint: "需要 prompt 参数（画图提示词），可选 model（模型）、negative_prompt（反向提示词）、ratio（宽高比）、resolution（分辨率）"
---

# Text To Image Skill

## 描述

这是一个 AI 文生图技能，当用户想通过文本描述生成图像时触发。支持多个绘图模型：即梦（JiMeng）、豆包（DouBao）、造相（Z-Image）。

从数据库中读取绘图配置（API 密钥、Base URL 等），根据用户选择的模型调用对应的绘图 API，返回生成的图片 URL。

这个仓库里额外提供了一个可执行脚本 `text-to-image/scripts/text_to_image.py`，方便宿主机器人直接调用。

## 触发条件

- 用户想画图、生成图片
- 用户说「画一张……」「生成一张……的图片」「帮我画……」
- 用户提到「文生图」「AI绘图」「AI画图」
- 用户描述了想要生成的图片内容

## 参数说明（JSON Schema）

调用脚本时，需要通过 shell 风格参数传入，参数结构如下：

```json
{
  "type": "object",
  "properties": {
    "prompt": {
      "type": "string",
      "description": "根据用户输入内容，提取出的画图提示词，但是不要对提示词进行总结。"
    },
    "model": {
      "type": "string",
      "description": "画图模型选择（可选）：即梦4.5(jimeng-4.5) / 即梦4.6(jimeng-4.6) / 即梦5.0(jimeng-5.0) / 豆包4.5(doubao-seedream-4.5) / 豆包4.0(doubao-seedream-4.0) / 豆包文生图(doubao-seedream-3.0-t2i) / 豆包图生图(doubao-seededit-3.0-i2i) / 造相基础版(Z-Image) / 造相蒸馏版(Z-Image-Turbo) / 造相图片编辑(Qwen-Image-Edit-2511)，默认: 空(none)。",
      "enum": [
        "none",
        "jimeng-4.5",
        "jimeng-4.6",
        "jimeng-5.0",
        "doubao-seedream-4.5",
        "doubao-seedream-4.0",
        "doubao-seedream-3.0-t2i",
        "doubao-seededit-3.0-i2i",
        "Z-Image",
        "Z-Image-Turbo",
        "Qwen-Image-Edit-2511"
      ],
      "default": "none"
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
  "required": ["prompt"],
  "additionalProperties": false
}
```

对应的命令行参数为：

- `--prompt <画图提示词>` 必填
- `--model <模型名>` 可选
- `--negative_prompt <反向提示词>` 可选
- `--ratio <宽高比>` 可选
- `--resolution <分辨率>` 可选

## 依赖安装

- 脚本首次运行时会自动创建虚拟环境并安装依赖，无需手动执行。
- 如需手动重新安装，可执行：`python3 text-to-image/scripts/bootstrap.py`

## 执行步骤

1. 当用户想通过文本描述生成图像时触发该技能。
2. 从用户输入中提取 prompt（画图提示词），不对提示词做总结或修改。可选提取 model、negative_prompt、ratio、resolution 参数。
3. 将参数组装为 shell 风格命令行参数，在仓库根目录下执行本地脚本，例如：`python3 text-to-image/scripts/text_to_image.py --prompt '一只可爱的猫咪在花园里玩耍' --model jimeng-5.0`。
4. 脚本生成图片后会自动调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/url` 将图片发送给用户，成功时输出「图片发送成功」。

## 回复要求

- 成功时，脚本输出「图片发送成功」，表示图片已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回具体的失败信息。
