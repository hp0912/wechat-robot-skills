---
name: send-remote-image
description: "发送远程图片技能。当你需要将一个或多个远程图片 URL 发送到当前微信会话时使用。"
argument-hint: "需要 image_url；可重复传 image_url 发送多张远程图片。"
---

# Send Remote Image Skill

## 描述

这是一个发送远程图片 URL 到当前微信会话的技能。

技能脚本位于 `scripts/send_remote_image.py`，会直接调用机器人客户端接口发送图片，不会下载、修改或生成图片。

## 触发条件

- 当你有远程图片 URL，需要将图片发送到当前微信会话时触发。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "image_url": {
      "type": "string",
      "description": "要发送的远程图片 URL，必须以 http 或 https 开头。"
    },
    "image_urls": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，要发送的多个远程图片 URL。"
    }
  },
  "anyOf": [{ "required": ["image_url"] }, { "required": ["image_urls"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--image_url <远程图片URL>` 必填或可重复传入
- `--image_urls <JSON数组>` 可选，用于一次传入多个远程图片 URL

## 执行步骤

1. 仅当用户明确要发送已有远程图片 URL 时触发该技能。
2. 从用户输入或上下文中提取图片 URL，不要把本地路径当成远程图片。
3. 在仓库根目录执行脚本，例如：

```bash
python3 scripts/send_remote_image.py --image_url 'https://example.com/image.png'
```

4. 脚本会调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/url` 将图片发送给当前会话。

## 校验规则

- `image_url` 不能为空。
- 图片 URL 必须以 `http` 或 `https` 开头。
- 本地图片文件路径应使用 `send-local-image` 技能。

## 回复要求

- 成功时，脚本输出「图片发送成功」，表示图片已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回脚本输出的具体错误信息。
