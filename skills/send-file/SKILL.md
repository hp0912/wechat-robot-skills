---
name: send-file
description: "发送本地或远程文件到当前微信会话。比如，其他技能产出了需要发送的本地文件路径时使用。发送图片应优先使用 send-image 这个技能。"
---

# 发送文件

使用 `scripts/send_file.py` 调用机器人客户端文件接口，将一个或多个本地文件、远程文件 URL 发送到当前微信会话。

## 入参

```json
{
  "type": "object",
  "properties": {
    "file_path": {
      "type": "string",
      "description": "要发送的本地文件路径，必须是机器人运行环境可访问的文件。"
    },
    "file_paths": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "要发送的多个本地文件路径。"
    },
    "file_url": {
      "type": "string",
      "description": "要发送的远程文件 URL，必须以 http 或 https 开头。"
    },
    "file_urls": {
      "type": "array",
      "items": {
        "type": "string"
      },
      "description": "要发送的多个远程文件 URL。"
    }
  },
  "anyOf": [
    { "required": ["file_path"] },
    { "required": ["file_paths"] },
    { "required": ["file_url"] },
    { "required": ["file_urls"] }
  ],
  "additionalProperties": false
}
```

可同时提供本地路径和远程 URL。对应命令行参数：

- `--file_path <本地文件路径>`：可重复传入。
- `--file_paths <JSON数组>`：一次传入多个本地文件路径。
- `--file_url <远程文件URL>`：可重复传入。
- `--file_urls <JSON数组>`：一次传入多个远程文件 URL。

## 执行

在技能目录运行脚本：

```bash
python3 scripts/send_file.py --file_path '/tmp/report.pdf'
```

发送远程文件：

```bash
python3 scripts/send_file.py --file_urls '["https://example.com/a.pdf","https://example.com/b.zip"]'
```

本地文件逐个调用 `POST /api/v1/robot/message/send/file/local`；远程文件合并调用 `POST /api/v1/robot/message/send/file/url`。接收方固定读取 `ROBOT_FROM_WX_ID`，客户端端口读取 `ROBOT_WECHAT_CLIENT_PORT`。

## 约束

- 至少提供一个非空本地路径或远程 URL。
- 本地路径必须指向已存在、非空且不超过 25MB 的文件，不能把 URL 作为本地路径传入。
- 远程 URL 必须包含有效的 `http` 或 `https` 协议及主机名；客户端下载后的文件也不能超过 25MB。
- 脚本按首次出现顺序去重；所有参数通过校验后，单个发送失败时会继续尝试其余文件，最终返回全部失败原因。

## 输出

- 全部成功时输出「文件发送成功」，无需再发送额外消息。
- 任一失败时返回具体错误信息，不要声称文件已全部发送。
