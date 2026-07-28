---
name: send-image
description: "发送本地或远程图片到当前微信会话。用户要求发送、转发或分享一个或多个已有图片文件/图片 URL，或其他技能产出了需要发送的本地图片路径时使用；不要用于生成或修改图片。"
---

# 发送图片

使用 `scripts/send_image.py` 调用机器人客户端接口，将一个或多个本地图片文件、远程图片 URL 发送到当前微信会话。

## 入参

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "要发送的本地图片路径，必须是机器人运行环境可访问的文件。"
    },
    "file_paths": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "要发送的多个本地图片路径。"
    },
    "image_url": {
      "type": "string",
      "description": "要发送的远程图片 URL，必须以 http 或 https 开头。"
    },
    "image_urls": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "要发送的多个远程图片 URL。"
    }
  },
  "anyOf": [
    { "required": ["file_path"] },
    { "required": ["file_paths"] },
    { "required": ["image_url"] },
    { "required": ["image_urls"] }
  ],
  "additionalProperties": false
}
```

可同时提供本地路径和远程 URL。对应命令行参数：

- `--file_path <本地图片路径>`：可重复传入。
- `--file_paths <JSON数组>`：一次传入多个本地图片路径。
- `--image_url <远程图片URL>`：可重复传入。
- `--image_urls <JSON数组>`：一次传入多个远程图片 URL。

## 执行

在技能目录运行脚本：

```bash
python3 scripts/send_image.py --file_path '/tmp/example.png'
```

发送远程图片：

```bash
python3 scripts/send_image.py --image_urls '["https://example.com/a.png","https://example.com/b.jpg"]'
```

本地图片逐个调用 `POST /api/v1/robot/message/send/image/local`；远程图片合并调用 `POST /api/v1/robot/message/send/image/url`。接收方固定读取 `ROBOT_FROM_WX_ID`，客户端端口读取 `ROBOT_WECHAT_CLIENT_PORT`。

## 约束

- 至少提供一个非空本地路径或远程 URL。
- 本地路径必须指向已存在且非空的文件，不能把 URL 作为本地路径传入。
- 远程 URL 必须包含有效的 `http` 或 `https` 协议及主机名。
- 脚本按首次出现顺序去重；所有参数通过校验后，单个发送失败时会继续尝试其余图片，最终返回全部失败原因。

## 输出

- 全部成功时输出「图片发送成功」，无需再发送额外消息。
- 任一失败时返回具体错误信息，不要声称图片已全部发送。
