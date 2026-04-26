---
name: beauty
description: "当用户发送「999」时触发。调用美女图片接口获取图片链接，再调用本地微信机器人发图接口把图片发给当前用户。"
argument-hint: "无需参数，直接调用即可"
---

# Beauty Skill

## 描述

这是一个用于获取美女图片并直接发送给当前用户的技能。

当用户发送 `999` 时，调用外部接口获取图片链接，再调用本地微信机器人接口把图片发出去。

这个仓库里额外提供了一个可执行脚本 `scripts/beauty.py`，方便宿主机器人直接调用。

## 触发条件

- 用户发送 `999`

## 接口信息

- 获取图片地址：`https://api.pearktrue.cn/api/today_wife`
- 请求方式：`GET`
- 发图接口：`http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/url`
- 请求方式：`POST`
- 本地脚本：`scripts/beauty.py`
- 获取图片返回示例：

```json
{
  "code": 200,
  "msg": "获取成功",
  "data": {
    "image_url": "https://api.pearktrue.cn/api_assets/wife/9a6a9c38-7d6e-464f-8930-eb9dac41cde9.webp",
    "role_name": "初音未来、巡音流歌",
    "width": 2480,
    "height": 3508
  },
  "api_source": "官方API网:https://api.pearktrue.cn/"
}
```

- 关键字段：`data.image_url`，表示需要发送出去的图片链接。

## 环境变量

- `ROBOT_WECHAT_CLIENT_PORT`：本地微信机器人服务端口。
- `ROBOT_FROM_WX_ID`：当前消息来源用户的 wxid。

## 执行步骤

1. 当用户发送 `999` 时触发该技能。
2. 在仓库根目录下执行本地脚本：`python3 scripts/beauty.py`。
3. 脚本内部发送 `GET` 请求到 `https://api.pearktrue.cn/api/today_wife`。
4. 脚本解析返回的 JSON，并提取 `data.image_url`。
5. 脚本从环境变量中读取 `ROBOT_WECHAT_CLIENT_PORT` 和 `ROBOT_FROM_WX_ID`。
6. 脚本发送 `POST` 请求到 `http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/url`，请求体为：

```json
{
  "to_wxid": "{ROBOT_FROM_WX_ID}",
  "image_urls": ["image_url"]
}
```

7. 如果任一步骤失败，回复兜底文案：`今天的美女图片暂时没拿到，等我再找找。`

## 回复要求

- 成功时，直接发送图片，不要额外追加解释文字。
- 失败时，使用固定兜底文案回复。
