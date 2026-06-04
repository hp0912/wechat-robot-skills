---
name: web-page
description: "网页内容读取、自动化交互和截图工具。当用户提供网页链接并希望了解页面内容、点击按钮、填写表单、等待页面变化，或需要截取整个网页/可视区域/指定元素/指定区域时使用。"
argument-hint: "需要 url；mode 可为 content 或 screenshot；自动化可传 actions/actions_file；截图可选 screenshot_mode、selector、x、y、width、height。"
---

# Web Page Skill

## 描述

这是一个本地网页读取、自动化交互和截图技能。它使用基础镜像中的 Chromium 以 headless 模式打开网页，通过 Chrome DevTools Protocol 在本地完成页面渲染、点击、输入、表单操作、正文抽取和截图，不调用外部 AI 接口。

技能脚本位于 `scripts/web_page.ts`，依赖基础镜像提供的 Node.js 24+ 和全局安装的 `tsx`（用于直接运行 TypeScript）以及 Chromium。基础镜像中已配置 `CHROME_BIN=/usr/bin/chromium` 和 `CHROME_PATH=/usr/bin/chromium` 时，无需额外安装浏览器。

## 触发条件

- 用户发来网页链接，并问「这个网页说了什么」「帮我看看这个链接」「总结一下这个页面」。
- 用户要求读取网页正文、标题、描述、主要内容或页面中的链接。
- 用户要求打开网页后点击按钮/链接、填写输入框、选择下拉框、勾选复选框/单选框、提交表单、删除某个 DOM 元素、等待某个元素出现或滚动页面。
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
    },
    "actions": {
      "type": "array",
      "description": "页面打开后、抽取内容或截图前要顺序执行的自动化动作数组。也可通过命令行传 JSON 字符串。",
      "items": {
        "type": "object"
      }
    },
    "actions_file": {
      "type": "string",
      "description": "包含 actions JSON 的本地文件路径。适合动作较多或命令行不方便转义时使用。"
    },
    "action_timeout_ms": {
      "type": "integer",
      "description": "单个自动化动作默认超时时间，默认 15000。"
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
- `--actions '<JSON数组>'` 可选，页面打开后顺序执行的自动化动作
- `--actions_file <本地JSON路径>` 可选，从文件读取自动化动作
- `--action_timeout_ms <毫秒>` 可选，单个动作默认超时时间，默认 `15000`
- `--output <本地PNG路径>` 可选，仅截图模式使用
- `--send <auto|true|false>` 可选，仅截图模式使用，默认 `auto`

## 自动化动作

`actions` 是一个 JSON 数组，脚本会在页面 `load` 并等待 `wait_ms` 后，按顺序执行这些动作，然后再进行正文抽取或截图。

通用字段：

- `type`：动作类型，必填。
- `selector`：CSS 选择器。多数表单动作必填。
- `text`：按可见文本、`aria-label`、`title`、`placeholder` 等匹配元素。`click`、`remove` 和 `scroll_to` 可用。
- `exact`：文本是否精确匹配，默认 `false`。
- `index`：匹配到多个元素时使用第几个，从 `0` 开始，默认 `0`。
- `timeout_ms`：当前动作超时时间，默认使用 `action_timeout_ms`。
- `wait_ms_after`：动作完成后的额外等待毫秒数，默认 `300`。
- `wait_for_navigation`：点击或按键后是否等待页面 load，默认 `false`。

支持的动作：

- `click`：点击元素。需要 `selector` 或 `text`。
- `fill`：清空并填写输入框、文本域或可编辑元素。需要 `selector` 和 `value`。
- `type`：向当前焦点或指定元素追加输入文本。需要 `text`，可选 `selector`。
- `press`：按键。需要 `key`，例如 `Enter`、`Tab`、`Escape`、`Backspace`、`ArrowDown`。
- `select`：设置 `<select>` 的值。需要 `selector` 和 `value`。
- `check` / `uncheck`：勾选或取消勾选复选框/单选框。需要 `selector`。
- `remove`：删除匹配到的 DOM 元素。需要 `selector` 或 `text`，可用 `index` 选择第几个匹配项。
- `wait`：固定等待。需要 `ms`。
- `wait_for_selector`：等待元素状态。需要 `selector`，可选 `state` 为 `attached`、`visible`、`hidden`、`detached`，默认 `visible`。
- `scroll`：按偏移滚动页面。可选 `x`、`y`，默认 `x=0,y=600`。
- `scroll_to`：滚动到坐标或元素。可传 `x/y`，或传 `selector` / `text`。

示例：点击链接后读取跳转页面内容。

```bash
tsx scripts/web_page.ts \
  --url 'https://example.com' \
  --mode content \
  --actions '[{"type":"click","text":"Learn more","wait_for_navigation":true}]'
```

示例：填写并提交表单。

```bash
tsx scripts/web_page.ts \
  --url 'https://httpbin.org/forms/post' \
  --mode content \
  --actions '[{"type":"fill","selector":"input[name=custname]","value":"Alice"},{"type":"check","selector":"input[name=size]","index":2},{"type":"check","selector":"input[name=topping]","index":1},{"type":"click","text":"Submit order","wait_for_navigation":true}]'
```

示例：交互后截图指定元素。

```bash
tsx scripts/web_page.ts \
  --url 'https://example.com' \
  --mode screenshot \
  --screenshot_mode selector \
  --selector 'body' \
  --actions '[{"type":"scroll_to","selector":"body"}]'
```

示例：截图前删除页面遮罩或弹窗。

```bash
tsx scripts/web_page.ts \
  --url 'https://example.com' \
  --mode screenshot \
  --actions '[{"type":"remove","selector":".modal-backdrop"},{"type":"remove","text":"Accept cookies"}]'
```

## 执行步骤

### 读取网页内容

1. 当用户想了解网页内容、总结页面、回答页面相关问题时，使用 `content` 模式。
2. 在该技能目录执行脚本，例如：

```bash
tsx scripts/web_page.ts --url 'https://example.com' --mode content
```

3. 脚本会输出页面标题、最终 URL、meta 描述、主要标题、正文文本和主要链接。
4. 智能体必须基于脚本输出回答用户问题；如果用户要求总结，不要把原始抽取过程当成最终回复。

### 自动化后读取或截图

1. 当用户要求先点击、填写、提交、删除页面元素、滚动或等待页面变化时，构造 `actions`。
2. 如果动作会触发页面跳转，给对应动作设置 `wait_for_navigation: true`。
3. 如果需要等待异步渲染结果，使用 `wait_for_selector` 或设置更长的 `wait_ms_after`。
4. 自动化动作完成后，继续按 `mode=content` 输出页面内容，或按 `mode=screenshot` 输出/发送截图。

### 截图网页

1. 当用户要求截图时，使用 `screenshot` 模式。
2. 截整个页面：

```bash
tsx scripts/web_page.ts --url 'https://example.com' --mode screenshot --screenshot_mode full
```

3. 截当前可视区域：

```bash
tsx scripts/web_page.ts --url 'https://example.com' --mode screenshot --screenshot_mode viewport --width 1365 --height 900
```

4. 截指定元素：

```bash
tsx scripts/web_page.ts --url 'https://example.com' --mode screenshot --screenshot_mode selector --selector 'main'
```

5. 截指定页面区域：

```bash
tsx scripts/web_page.ts --url 'https://example.com' --mode screenshot --screenshot_mode region --x 0 --y 300 --width 800 --height 600
```

6. 在机器人环境中，脚本默认会调用客户端接口 `POST http://127.0.0.1:{ROBOT_WECHAT_CLIENT_PORT}/api/v1/robot/message/send/image/local` 发送截图；如果缺少机器人环境变量，则只输出本地截图路径。

## 校验规则

- `url` 不能为空，且必须是 `http` 或 `https` 地址。
- `mode` 只能是 `content` 或 `screenshot`。
- `screenshot_mode` 只能是 `full`、`viewport`、`selector` 或 `region`。
- `selector` 截图模式必须传 `selector`。
- `region` 截图模式必须传 `x`、`y`、`width`、`height`，并且宽高必须大于 0。
- `wait_ms`、`max_chars`、视口宽高必须大于 0。
- `actions` 必须是 JSON 数组，或包含 `actions` 数组的 JSON 对象。
- `actions_file` 必须是可读取的本地 JSON 文件。
- `action_timeout_ms` 和单个动作的 `timeout_ms` 必须大于 0。
- 自动化动作必须符合对应类型的必填字段要求，例如 `fill` 必须传 `selector` 和 `value`，`click` / `remove` 必须传 `selector` 或 `text`。
- Chromium 路径优先读取 `CHROME_BIN`，其次读取 `CHROME_PATH`，再回退到常见系统路径。

## 回复要求

- `content` 模式成功时，智能体应基于脚本输出给用户总结、解释或回答问题。
- 自动化动作失败时，脚本会输出具体失败在第几个 action 及失败原因。
- `screenshot` 模式成功且已发送图片时，脚本输出「页面截图已发送」。
- `screenshot` 模式成功但未发送图片时，脚本输出本地 PNG 路径，智能体可继续用 `send-local-image` 技能发送。
- 失败时，返回脚本输出的具体错误信息。
