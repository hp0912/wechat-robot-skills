---
name: find-recent-chat-media
description: "从当前会话历史消息中查找最近十分钟内由当前用户发送的图片、视频或语音，并下载后上传 CDN 返回可供 AI 使用的媒体 URL。当你觉得你需要图片/视频/语音但当前上下文没有的时候使用。"
argument-hint: "需要 media_type；可选 count，最多 5。media_type 可为 image、video、voice 或 all。"
---

# Find Recent Chat Media Skill

## 描述

这是一个从历史消息中查找媒体并转换为 CDN URL 的技能。

当你觉得你需要图片/视频/语音，但当前上下文里没有可用媒体信息的时候，优先使用本技能在当前会话历史中查找最近十分钟内由当前用户发送的图片、视频或语音。

技能脚本位于 `scripts/find_recent_chat_media.py`。

## 触发条件

- 用户说「看看刚才那张图」「识别一下我刚发的图片」「这张图是什么」，但当前上下文没有图片。
- 用户说「总结刚才的视频」「分析我刚发的视频」，但当前上下文没有视频。
- 用户说「听一下刚才那条语音」「转写我刚发的语音」，但当前上下文没有语音。
- 用户明确说「前面那几张图」「刚刚发的两个视频」「最近的语音」等，需要从历史消息中补齐媒体 URL。

如果当前上下文已经有可用的媒体 URL 或引用媒体消息，不需要触发本技能。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "media_type": {
      "type": "string",
      "enum": ["image", "video", "voice", "all"],
      "description": "要查找的媒体类型。图片用 image，视频用 video，语音用 voice；上下文同时可能是多种媒体时用 all。"
    },
    "media_types": {
      "type": "array",
      "items": {
        "type": "string",
        "enum": ["image", "video", "voice"]
      },
      "description": "可选，要查找的多个媒体类型。"
    },
    "count": {
      "type": "integer",
      "minimum": 1,
      "maximum": 5,
      "description": "需要查找的媒体数量。默认 1，最多 5。"
    }
  },
  "anyOf": [{ "required": ["media_type"] }, { "required": ["media_types"] }],
  "additionalProperties": false
}
```

对应命令行参数：

- `--media_type <image|video|voice|all>` 必填或可重复传入
- `--media_types <JSON数组>` 可选
- `--count <数量>` 可选，默认 `1`，最大 `5`

## 查找规则

1. 只查找当前会话 `ROBOT_FROM_WX_ID` 的历史消息。
2. 只查找当前消息发送人 `ROBOT_SENDER_WX_ID` 发出的历史消息。
3. 只查找最近十分钟内的消息。
4. 根据 `media_type` / `media_types` 查找图片、视频、语音，可一次查找多种类型。
5. 最多返回 5 条。脚本会先取最近匹配的 N 条，再按时间升序输出，因此第一条最早，最后一条最晚。
6. 历史消息查询直接读取数据库 `messages` 表，不调用历史消息 HTTP 接口。
7. 查到消息记录后，脚本必须先调用客户端下载接口下载媒体，再调用客户端上传接口上传到 CDN，最后只把 CDN URL 返回给智能体。

## 执行步骤

1. 判断当前用户请求需要哪种媒体。如果用户说图片，传 `--media_type image`；视频传 `video`；语音传 `voice`；上下文不确定时传 `all`。
2. 根据用户表达决定 `count`。例如「那张图」传 `1`；「刚发的三张图」传 `3`；「这些图」可传 `5`。
3. 在该技能目录执行脚本，例如：

```bash
python3 scripts/find_recent_chat_media.py --media_type image --count 1
```

4. 成功时脚本输出 JSON，包含 `media_urls` 和按类型拆分的 `image_urls`、`video_urls`、`voice_urls`。
5. 智能体拿到 URL 后，再继续调用图片识别、视频理解、语音理解/转写等后续能力。

## 数据库与客户端接口依赖

脚本会直接查询数据库表 `messages`，筛选字段包括：

- `from_wxid = ROBOT_FROM_WX_ID`
- `sender_wxid = ROBOT_SENDER_WX_ID`
- `created_at` 在最近十分钟内
- `type` 为图片 `3`、语音 `34`、视频 `43`

脚本会调用以下客户端接口下载和上传媒体：

- 下载图片：`GET http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/image/download?message_id=...`
- 下载视频：`GET http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/video/download?message_id=...`
- 下载语音：`GET http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/voice/download?message_id=...`
- 上传 CDN：`POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/chat/media/upload`

上传接口为 `multipart/form-data`，表单字段：

- `message_id`: 历史消息 ID
- `media_type`: `image` / `video` / `voice`
- `extension`: 文件扩展名，可选
- `media`: 下载到的媒体文件

## 回复要求

- 成功时，使用脚本输出的 CDN URL 继续完成用户原始请求，不要把查找过程当成最终回复。
- 如果脚本输出未找到媒体，应提示用户先发送一张图片、一个视频或一条语音。
- 如果脚本返回上传或下载错误，按脚本输出向用户说明原因。
