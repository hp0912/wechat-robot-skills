---
name: web-page
description: "网页内容读取和截图工具。当用户提供网页链接并希望了解页面内容、总结页面讲了什么，或需要截取整个网页/可视区域/指定元素/指定区域时使用。"
argument-hint: "需要 url；mode 可为 content 或 screenshot；截图可选 screenshot_mode、selector、x、y、width、height。"
---

# Web Page Skill

## 描述

这是一个本地网页读取和截图技能。它使用基础镜像中的 Chromium 以 headless 模式打开网页，通过 Chrome DevTools Protocol 在本地完成页面渲染、正文抽取和截图，不调用外部 AI 接口。

技能脚本位于 `scripts/web_page.js`，依赖基础镜像提供的 Node.js 24+ 和 Chromium。基础镜像中已配置 `CHROME_BIN=/usr/bin/chromium` 和 `CHROME_PATH=/usr/bin/chromium` 时，无需额外安装浏览器。

## 触发条件

- 用户发来网页链接，并问「这个网页说了什么」「帮我看看这个链接」「总结一下这个页面」。
- 用户要求读取网页正文、标题、描述、主要内容或页面中的链接。
- 用户要求「截图这个网页」「截整个页面」「截当前可视区域」「截页面里某个区域」。
- 用户提供 CSS 选择器并要求截取对应元素，例如「截取 `.article` 这一块」。

## 入参规范

```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "description": "需要打开的网页链接，必须是 http 或 https 地址。"
    },
    "mode": {
      "type": "string",
      "enum": ["content", "screenshot"],
      "description": "content 表示抽取网页内容；screenshot 表示截图。默认 content。",
      "default": "content"
    },
    "screenshot_mode": {
      "type": "string",
      "enum": ["full", "viewport", "selector", "region"],
      "description": "截图模式。full 截整个页面，viewport 截当前可视区域，selector 截指定 CSS 选择器元素，region 截指定页面坐标区域。默认 full。",
      "default": "full"
    },
    "selector": {
      "type": "string",
      "description": "当 screenshot_mode 为 selector 时必填，表示要截图的 CSS 选择器。"
    },
    "x": {
      "type": "number",
      "description": "当 screenshot_mode 为 region 时必填，区域左上角 x 坐标，单位为 CSS 像素，相对于页面左上角。"
    },
    "y": {
      "type": "number",
      "description": "当 screenshot_mode 为 region 时必填，区域左上角 y 坐标，单位为 CSS 像素，相对于页面左上角。"
    },
    "width": {
      "type": "number",
      "description": "region 截图宽度，或浏览器视口宽度。截图区域必须大于 0。"
    },
    "height": {
      "type": "number",
      "description": "region 截图高度，或浏览器视口高度。截图区域必须大于 0。"
    },
    "max_chars": {
      "type": "integer",
      "description": "content 模式下最多输出的正文字数，默认 16000。"
    },
    "wait_ms": {
      "type": "integer",
      "description": "页面 load 之后额外等待的毫秒数，默认 1500。遇到前端渲染较慢的网站可调大。"
    }
  },
  "required": ["url"],
  "additionalProperties": false
}
```

对应命令行参数：

- `--url <网页链接>` 必填，必须是 `http` 或 `https` 地址
- `--mode <content|screenshot>` 可选，默认 `content`
- `--screenshot_mode <full|viewport|selector|region>` 可选，默认 `full`
- `--selector <CSS选择器>` 可选，`selector` 截图模式必填
- `--x <数字> --y <数字> --width <数字> --height <数字>` 可选，`region` 截图模式必填
- `--max_chars <数字>` 可选，默认 `16000`
- `--wait_ms <毫秒>` 可选，默认 `1500`
- `--output <本地PNG路径>` 可选，仅截图模式使用
- `--send <auto|true|false>` 可选，仅截图模式使用，默认 `auto`

## 执行步骤

### 读取网页内容

1. 当用户想了解网页内容、总结页面、回答页面相关问题时，使用 `content` 模式。
2. 在该技能目录执行脚本，例如：

```bash
node scripts/web_page.js --url 'https://example.com' --mode content
```

3. 脚本会输出页面标题、最终 URL、meta 描述、主要标题、正文文本和主要链接。
4. 智能体必须基于脚本输出回答用户问题；如果用户要求总结，不要把原始抽取过程当成最终回复。

### 截图网页

1. 当用户要求截图时，使用 `screenshot` 模式。
2. 截整个页面：

```bash
node scripts/web_page.js --url 'https://example.com' --mode screenshot --screenshot_mode full
```

3. 截当前可视区域：

```bash
node scripts/web_page.js --url 'https://example.com' --mode screenshot --screenshot_mode viewport --width 1365 --height 900
```

4. 截指定元素：

```bash
node scripts/web_page.js --url 'https://example.com' --mode screenshot --screenshot_mode selector --selector 'main'
```

5. 截指定页面区域：

```bash
node scripts/web_page.js --url 'https://example.com' --mode screenshot --screenshot_mode region --x 0 --y 300 --width 800 --height 600
```

6. 在机器人环境中，脚本默认会调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/local` 发送截图；如果缺少机器人环境变量，则只输出本地截图路径。

## 校验规则

- `url` 不能为空，且必须是 `http` 或 `https` 地址。
- `mode` 只能是 `content` 或 `screenshot`。
- `screenshot_mode` 只能是 `full`、`viewport`、`selector` 或 `region`。
- `selector` 截图模式必须传 `selector`。
- `region` 截图模式必须传 `x`、`y`、`width`、`height`，并且宽高必须大于 0。
- `wait_ms`、`max_chars`、视口宽高必须大于 0。
- Chromium 路径优先读取 `CHROME_BIN`，其次读取 `CHROME_PATH`，再回退到常见系统路径。

## 回复要求

- `content` 模式成功时，智能体应基于脚本输出给用户总结、解释或回答问题。
- `screenshot` 模式成功且已发送图片时，脚本输出「页面截图已发送」。
- `screenshot` 模式成功但未发送图片时，脚本输出本地 PNG 路径，智能体可继续用 `send-local-image` 技能发送。
- 失败时，返回脚本输出的具体错误信息。
