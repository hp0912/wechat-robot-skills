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

调用脚本时，需要通过第一个命令行参数传入 JSON 字符串，结构如下：

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

## 环境变量

- `ROBOT_CODE`：机器人实例编码，用作数据库名称。
- `ROBOT_FROM_WX_ID`：微信消息来源（群聊 ID 或好友微信 ID），用于判断查询群聊配置还是好友配置。
- `MYSQL_HOST`：MySQL 数据库地址。
- `MYSQL_PORT`：MySQL 数据库端口。
- `MYSQL_USER`：MySQL 数据库用户名。
- `MYSQL_PASSWORD`：MySQL 数据库密码。

## 依赖安装

- 在执行 `text-to-image/scripts/text_to_image.py` 之前，必须先安装依赖。
- 建议优先执行安装脚本：`python3 skills/text-to-image/scripts/bootstrap.py`
- 该脚本会自动执行：`python3 -m pip install -r skills/text-to-image/scripts/requirements.txt`
- 如果不使用安装脚本，也可以直接执行：`python3 -m pip install -r skills/text-to-image/scripts/requirements.txt`
- 如果环境里已经安装过这些依赖，重复安装通常不会报错，只会做已满足检查。

## 执行步骤

1. 当用户输入绘图相关内容时触发该技能。
2. 从用户输入中提取 prompt（画图提示词），不对提示词做总结或修改。可选提取 model、negative_prompt、ratio、resolution 参数。
3. 在执行脚本前，先安装依赖：`python3 skills/text-to-image/scripts/bootstrap.py`，或直接执行 `python3 -m pip install -r skills/text-to-image/scripts/requirements.txt`。
4. 将参数组装为 JSON 字符串，在仓库根目录下执行本地脚本：`python3 text-to-image/scripts/text_to_image.py '<JSON参数>'`。
5. 脚本内部执行逻辑：
   - 连接 MySQL 数据库（数据库名 = `ROBOT_CODE`）。
   - 查询 `global_settings` 表获取全局绘图配置（`image_ai_enabled`、`image_ai_settings`）。
   - 如果 `ROBOT_FROM_WX_ID` 以 `@chatroom` 结尾，查询 `chat_room_settings` 表（`WHERE chat_room_id = ?`）覆盖全局配置；否则查询 `friend_settings` 表（`WHERE wechat_id = ?`）覆盖全局配置。
   - 检查绘图功能是否开启（`image_ai_enabled`）。
   - 解析 `image_ai_settings` JSON，根据选择的模型提取对应配置（JiMeng / DouBao / Z-Image）。
   - 调用对应的绘图 API 生成图片。
   - 输出图片 URL。
6. 如果脚本执行失败，回复兜底文案：`AI 绘图暂时不可用，请稍后再试。`

## 回复要求

- 成功时，脚本输出 `<wechat-robot-image-url>图片URL</wechat-robot-image-url>` 格式，直接发送图片，不要额外追加解释文字。
- 失败时，使用固定兜底文案回复。
