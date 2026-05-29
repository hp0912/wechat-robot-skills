---
name: send-local-image
description: "发送本地图片技能。当你需要将本地图片文件发送到当前微信会话时使用。"
argument-hint: "需要 file_path；可重复传 file_path 发送多张本地图片。"
---

# Send Local Image Skill

## 描述

这是一个发送本地图片文件到当前微信会话的技能。

技能脚本位于 `scripts/send_local_image.py`，会直接调用机器人客户端接口发送图片，不会修改或生成图片。

## 触发条件

- 当你有本地图片地址，需要将图片发送到当前微信会话时触发。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "要发送的本地图片文件路径。必须是机器人运行环境可访问的路径。"
    },
    "file_paths": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "可选，要发送的多个本地图片文件路径。"
    }
  },
  "anyOf": [{ "required": ["file_path"] }, { "required": ["file_paths"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--file_path <本地图片路径>` 必填或可重复传入
- `--file_paths <JSON数组>` 可选，用于一次传入多个本地图片路径

## 执行步骤

1. 仅当用户明确要发送已有本地图片文件时触发该技能。
2. 从用户输入或上下文中提取本地图片路径，不要把远程 URL 当成本地路径。
3. 在仓库根目录执行脚本，例如：

```bash
python3 scripts/send_local_image.py --file_path '/tmp/example.png'
```

4. 脚本会调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/local` 将图片发送给当前会话。

## 校验规则

- `file_path` 不能为空。
- 传入的路径必须指向一个已存在的本地文件。
- 远程 `http` 或 `https` 图片地址应使用 `send-remote-image` 技能。

## 回复要求

- 成功时，脚本输出「图片发送成功」，表示图片已通过客户端接口直接发送，无需 AI 智能体再做额外处理。
- 失败时，返回脚本输出的具体错误信息。
