---
name: pptx
description: "创建、读取、编辑、复制页面、转换、校验和渲染本地或远程 HTTPS PowerPoint 演示文稿与模板，并按需识别页面图片、截图和图表中的文字。用户提到 PPT、PPTX、PowerPoint、演示文稿、幻灯片、路演稿、汇报材料、演讲者备注、模板、版式或图片文字 OCR，或提供 HTTPS 演示文稿地址、.pptx、.potx、.ppsx、.ppt 文件时使用；支持安全下载、结构化创建、保留 Run 格式的文本替换、页面删除/重排/复制、Markdown 提取、本地 RapidOCR、旧格式转换、受控 OOXML 解包/打包、关系与图表校验及逐页视觉检查。若主要交付物不是演示文稿且不需要读取或修改 PPT 内容，则不要使用。"
---

# PowerPoint 演示文稿处理

## 强制执行规则

当前智能体不能直接执行 shell、任意 Python、Node.js 或系统命令。只能通过 `execute_skill_script` 调用本 Skill 中真实存在的固定 Python 脚本。

- 只调用下表列出的可执行脚本；不执行 `scripts/` 目录、`scripts/_pptx_common.py`、`scripts/_presentation_builder.js` 或 `scripts/_icon_renderer.js`。
- 不把 `python3`、`node`、`soffice`、`libreoffice`、`pdftoppm`、`zip`、`unzip`、`rm` 或其他系统命令作为脚本参数。
- PptxGenJS、React Icons、Sharp、LibreOffice、Poppler 和 ZIP 操作只允许由固定脚本在内部调用。
- 每次检查脚本返回的 JSON；只有 `ok` 为 `true` 时才继续。`validate_presentation.py` 还必须返回 `status: valid`、`issue_count: 0`。
- 只在需要读取图片、截图或视觉图表中的文字时调用 `ocr_presentation.py`。只使用 `slides[]` 中 `usable_for_summary: true` 的 `text`；低置信度结果不得作为可靠正文。
- 不覆盖用户提供的源文件。最终结果写入 `output/pptx/`，中间产物写入 `tmp/pptx/<任务名>/`。
- 远程地址只交给 `download_presentation.py`；不要在回复、日志摘要或文件名中复述可能含敏感查询参数的完整 URL。
- 环境已预置全部依赖，不安装软件包，也不提示用户安装依赖。

## 脚本清单

| 脚本 | 用途 | 底层能力 |
| --- | --- | --- |
| `scripts/download_presentation.py` | 下载并校验远程 HTTPS 演示文稿 | Python `urllib`、安全 OOXML 解析、`python-pptx` |
| `scripts/inspect_presentation.py` | 分段读取页面、文本、表格、图表、图片和备注 | `python-pptx`、安全 OOXML 解析 |
| `scripts/ocr_presentation.py` | 按页识别图片、截图和视觉图表中的文字 | RapidOCR、ONNX Runtime、LibreOffice、Poppler |
| `scripts/extract_presentation.py` | 提取整份演示文稿为 Markdown | `markitdown[pptx]` |
| `scripts/create_presentation.py` | 按受控 JSON 创建专业 PPTX | PptxGenJS |
| `scripts/edit_presentation.py` | 文本替换、删除/重排页面、修改属性 | `python-pptx` |
| `scripts/duplicate_slide.py` | 复制现有 PPTX 页面并维护包关系 | `lxml`、Python `zipfile` |
| `scripts/render_icon.py` | 把允许的 React Icons 图标渲染为 PNG | React、React DOM、React Icons、Sharp |
| `scripts/convert_presentation.py` | `.ppt/.potx/.ppsx/.pptx` 转 PPTX 或 PDF | LibreOffice |
| `scripts/unpack_presentation.py` | 安全解包 OOXML 供高级编辑 | Python `zipfile` |
| `scripts/pack_presentation.py` | 把 OOXML 目录安全打包为演示文稿 | Python `zipfile`、`python-pptx` |
| `scripts/validate_presentation.py` | 校验 ZIP、XML、关系、页面、图表、边界和可渲染性 | `defusedxml`、`lxml`、`python-pptx`、LibreOffice |
| `scripts/render_presentation.py` | 把演示文稿渲染为逐页 PNG、联系表和 PDF | LibreOffice、Poppler、Pillow |

## 标准流程

1. 输入是 HTTPS 地址时，先调用 `download_presentation.py` 下载到本次任务临时目录；本地文件直接进入下一步。
2. 旧版 `.ppt` 先调用 `convert_presentation.py` 转为 `.pptx`。需要把 `.potx/.ppsx` 当普通演示文稿编辑时，也先转为 `.pptx`。
3. 编辑、总结或套用模板前先调用 `inspect_presentation.py`；内容较多时再调用 `extract_presentation.py` 获取完整 Markdown。
4. 需要读取截图、扫描页或图片中的文字时，从 `image_slides` 选择相关页调用 `ocr_presentation.py`。不要默认 OCR 全部页面，也不要用 OCR 覆盖可靠的原生文本。
5. 从零创建使用 `create_presentation.py`；常规文本与页面编辑使用 `edit_presentation.py`；复用模板页面使用 `duplicate_slide.py`。
6. 只有固定脚本不能完成的精细模板编辑，才使用 `unpack_presentation.py` → 最小化编辑 OOXML → `pack_presentation.py`。
7. 创建或修改后必须调用 `validate_presentation.py --check-render`。基于模板制作时同时传 `--original <原模板>`。
8. 再调用 `render_presentation.py --contact-sheet` 渲染全部页面；若 `has_more: true`，用 `next_slide` 继续，直到检查完整份演示文稿。
9. 逐页检查内容、顺序、溢出、重叠、间距、对齐、对比度、占位文本和备注。发现问题后修复并重新校验、重新渲染受影响页面。
10. 文件结构、内容和视觉检查全部通过后才交付。

## 下载远程演示文稿

只接受 HTTPS 地址。完整保留 URL 及查询参数传给脚本，但不要在回复或输出文件名中暴露查询参数。

```text
--url 'https://example.com/deck.pptx?signature=...' --output 'tmp/pptx/<任务名>/source.pptx'
```

可选参数：

- `--timeout <1-600>`：连接和读取超时秒数，默认 `60`。
- `--max-bytes <字节数>`：默认 100 MiB，最高 512 MiB。
- `--overwrite`：只覆盖本次任务生成的旧缓存。

`output` 扩展名必须与远程内容的真实格式一致，支持 `.pptx/.potx/.ppsx/.ppt`。脚本限制重定向只能继续使用 HTTPS，流式限制大小，先写临时文件，再原子发布。

## 检查和提取内容

调用结构检查：

```text
--input 'source.pptx' --start-slide 1 --max-slides 30
```

常用参数：

- `--include-runs`：需要检查局部字体、粗体、字号或跨 Run 文本替换时使用。
- `--max-shapes <1-1000>`、`--max-table-cells <1-10000>`：限制单次结构输出。
- `--max-chars <1000-1000000>`：限制 JSON 中返回的正文量。

重点检查 `slide_size`、`slides[].layout`、形状边界、表格、图表系列、图片类型、`slides[].media`、`image_slides`、`speaker_notes`、批注摘要和 `external_relationships`。`media.image_count` 表示页面中的图片数量，`media.image_area_ratio` 是图片大致占页比例，`media.native_text_char_count` 是可直接提取的原生文字量。长演示文稿根据 `selection.has_more` 和 `next_slide` 分段读取。

需要连续正文时调用：

```text
--input 'source.pptx' --output 'tmp/pptx/<任务名>/content.md'
```

Markdown 适合检查遗漏、错字和顺序，不代表页面版式。

## 识别图片中的文字

只有图片、截图、扫描页或视觉图表中的文字对任务有意义时才调用：

```text
--input 'source.pptx' --slides '2,5-6'
```

`slides` 必须明确指定页码，单次最多 4 页。脚本先通过 LibreOffice 和 Poppler 临时渲染选定页面，再使用本地 RapidOCR 识别；渲染图片会自动删除，不联网，也不调用大模型识图。

脚本会根据原生文本框的位置和文字相似度过滤重复内容，因此 `slides[].text` 只返回原生文本之外的可靠图片文字。检查：

- `status: good` 且 `usable_for_summary: true`：可以把 `text` 补充到同页原生文本中。
- `status: no_image_text`：未发现额外图片文字，不是错误。
- `status: sparse` 或 `low_confidence`：不要使用返回文字；根据 `needs_review` 人工核验。
- `filtered_native_line_count`：被识别为原生文本并去重的 OCR 行数。
- `picture_count`、`chart_count` 和 `image_area_ratio`：用于理解本页视觉内容规模。

默认 260 DPI。小字可使用 `--dpi 300-400`；单页预计像素过大时降低 DPI。可用 `--max-chars` 控制输出；若 `has_more: true`，根据 `next_slide`、`next_offset` 和 `remaining_slides` 继续。只有 `next_offset > 0` 时才传 `--start-offset`，且此时 `slides` 只能包含该页。

## 创建演示文稿

调用：

```text
--output 'output/pptx/result.pptx' --spec '<JSON对象>'
```

内容较长时先把 JSON 写入任务临时目录，再传 `--spec-file`。目标是本次任务旧产物且确认可覆盖时才传 `--overwrite`。

顶层结构：

```json
{
  "layout": "LAYOUT_WIDE",
  "properties": {
    "title": "2026 年产品路线图",
    "author": "示例公司",
    "subject": "产品规划"
  },
  "theme": {
    "head_font": "Noto Sans CJK SC",
    "body_font": "Noto Sans CJK SC",
    "language": "zh-CN"
  },
  "slides": []
}
```

支持布局：

| `layout` | 画布尺寸 |
| --- | --- |
| `LAYOUT_WIDE` | 13.333 × 7.5 英寸 |
| `LAYOUT_16X9` | 10 × 5.625 英寸 |
| `LAYOUT_4X3` | 10 × 7.5 英寸 |

每页使用：

```json
{
  "background": "0F172A",
  "speaker_notes": "本页讲解约 45 秒。",
  "elements": []
}
```

坐标和尺寸 `x/y/w/h` 均使用英寸。颜色必须是不带 `#` 的 6 位十六进制值；不要把透明度拼入 8 位颜色，透明度使用 `transparency: 0-100`。所有可见元素必须位于画布内。

### 文本

```json
{
  "type": "text",
  "text": "从洞察到增长",
  "options": {
    "x": 0.7,
    "y": 0.6,
    "w": 8.8,
    "h": 0.7,
    "fontFace": "Noto Sans CJK SC",
    "fontSize": 30,
    "bold": true,
    "color": "F8FAFC",
    "margin": 0,
    "breakLine": false
  }
}
```

局部格式使用 `runs`：

```json
{
  "type": "text",
  "runs": [
    {"text": "收入 ", "options": {"bold": true}},
    {"text": "+28%", "options": {"bold": true, "color": "22C55E"}}
  ],
  "options": {
    "x": 0.8,
    "y": 2.0,
    "w": 4.0,
    "h": 0.6,
    "fontSize": 24,
    "margin": 0
  }
}
```

列表不要输入字面量 `•`。每个列表项使用独立 Run，并设置 `bullet: true`；除最后一项外设置 `breakLine: true`，项目间距用 `paraSpaceAfter`。

### 形状、图片和图标

形状：

```json
{
  "type": "shape",
  "shape": "roundRect",
  "options": {
    "x": 0.8,
    "y": 1.7,
    "w": 3.6,
    "h": 2.2,
    "fill": {"color": "E0F2FE"},
    "line": {"color": "BAE6FD", "width": 1},
    "shadow": {"type": "outer", "color": "0F172A", "opacity": 0.15, "blur": 2, "angle": 45, "distance": 1}
  }
}
```

常用形状名包括 `rect`、`roundRect`、`ellipse`、`line`、`chevron`、`triangle` 和 `hexagon`。阴影 `offset/distance` 不得为负数；向上投影时改变角度。

图片：

```json
{
  "type": "image",
  "path": "/absolute/path/chart.png",
  "options": {"x": 7.2, "y": 1.4, "w": 5.2, "h": 4.8}
}
```

需要图标时先调用：

```text
--library fi --name FiTrendingUp --color 2563EB --size 256 --output 'tmp/pptx/<任务名>/trend.png'
```

允许的图标库：`fa6`、`fi`、`hi2`、`io5`、`lu`、`md`、`ri`、`tb`。把生成的 PNG 作为普通图片元素插入。

### 图表

```json
{
  "type": "chart",
  "chart_type": "bar",
  "data": [
    {
      "name": "收入",
      "labels": ["Q1", "Q2", "Q3", "Q4"],
      "values": [120, 148, 176, 215]
    }
  ],
  "options": {
    "x": 0.8,
    "y": 1.6,
    "w": 6.0,
    "h": 4.7,
    "catAxisLabelFontSize": 12,
    "valAxisLabelFontSize": 11,
    "showLegend": false,
    "showValue": true,
    "dataLabelPosition": "outEnd",
    "chartColors": ["2563EB"]
  }
}
```

支持 `area/bar/bar3d/bubble/bubble3d/doughnut/line/pie/radar/scatter`。PowerPoint 原生支持的图表必须保留为可编辑图表；只有桑基图、网络图等没有对应原生类型的可视化才使用图片。堆积条形图或柱形图的数据标签只能使用 `ctr/inEnd/inBase`，不能使用 `outEnd`。

### 表格

```json
{
  "type": "table",
  "rows": [
    [
      {"text": "指标", "options": {"bold": true, "color": "FFFFFF", "fill": "1E3A8A"}},
      {"text": "本期", "options": {"bold": true, "color": "FFFFFF", "fill": "1E3A8A"}}
    ],
    ["收入", "2,150 万元"],
    ["增长率", "28.0%"]
  ],
  "options": {
    "x": 0.8,
    "y": 1.8,
    "w": 6.2,
    "h": 2.2,
    "border": {"type": "solid", "color": "CBD5E1", "pt": 1},
    "fontFace": "Noto Sans CJK SC",
    "fontSize": 14,
    "margin": 0.08
  }
}
```

## 编辑现有演示文稿

调用：

```text
--input 'source.pptx' --output 'output/pptx/edited.pptx' --spec '<JSON对象>'
```

JSON 顶层只有 `operations`，按数组顺序执行：

| `operations[].type` | 关键字段 |
| --- | --- |
| `replace_text` | `find`、`replace`；可选 `slides/match_case/whole_word/count/required/include_notes` |
| `set_properties` | `properties`；其中 `revision` 必须是正整数 |
| `delete_slides` | `slides[]`，页码从 1 开始 |
| `reorder_slides` | `order[]`，必须完整且不重复地列出当前全部页码 |

示例：

```json
{
  "operations": [
    {
      "type": "replace_text",
      "find": "2025 年",
      "replace": "2026 年",
      "match_case": true,
      "required": true
    },
    {
      "type": "reorder_slides",
      "order": [1, 3, 2, 4]
    }
  ]
}
```

文本替换会处理同一段落内跨多个 Run 的匹配，并尽量保留替换起点和结尾的格式。输入含批注、ActiveX、宏或嵌入对象时，常规编辑脚本会停止，改用受控 OOXML 流程，避免静默丢失内容。输入含外部链接时默认停止；只有用户明确接受风险后才传 `--allow-external-links`。

## 复制模板页面

只对 `.pptx` 使用：

```text
--input 'template.pptx' --output 'tmp/pptx/<任务名>/expanded.pptx' --slide 2 --after 4
```

`slide` 是复制来源，`after` 是插入位置；省略 `after` 时紧跟来源页插入。脚本会更新页面清单、关系和内容类型，并移除不能安全共享的备注/批注关系。

复制页仍可能与原页共享图表、SmartArt 或嵌入对象部件。若返回的 `shared_relationships` 非空，修改这些对象前先做 OOXML 级独立复制；否则改动一页可能同时影响另一页。

## 高级 OOXML 编辑

固定编辑脚本无法表达且确实需要精细模板操作时：

1. 调用 `unpack_presentation.py --input <pptx> --output-dir <空目录>`。
2. 先完成页面复制、删除和重排，再修改页面内容。
3. 只最小化编辑相关 XML；不要重排、格式化或重写无关部件。
4. 每个列表项保留独立的 `<a:p>`；保留相邻 `<a:pPr>` 以继承缩进与间距；不要在文本中写字面量项目符号。
5. 有前后空格的 `<a:t>` 设置 `xml:space="preserve"`。
6. 调用 `pack_presentation.py --input-dir <目录> --output <新pptx>`。
7. 立即调用 `validate_presentation.py --original <源pptx> --check-render`。

不得手工复制单个 `slideN.xml`。新页面必须同时登记到 `ppt/presentation.xml`、`ppt/_rels/presentation.xml.rels` 和 `[Content_Types].xml`，并处理页面关系。

## 设计和排版要求

- 先确定与主题相关的配色和一个贯穿全稿的视觉母题。一个主色承担约三分之二视觉权重，搭配 1–2 个辅助色和一个强调色。
- 封面、章节页和结尾页可以使用深色背景，内容页使用浅色背景；同一套演示文稿保持一致。
- 每页至少包含一种有信息作用的视觉元素：图片、图表、图标、流程、时间线或重点数字。不要只放标题和大段项目符号。
- 交替使用双栏、卡片网格、半幅图片、对比栏和流程布局；不要连续复用同一种版式。
- 中文正文优先使用 `Noto Sans CJK SC`，拉丁正文优先使用 Arial 或 Calibri。字体会在最终用户的 PowerPoint 中渲染，LibreOffice 预览可能发生替换；非预置字体至少保留约 10% 宽度余量。
- 标题通常为 32–44 pt，分区标题 20–26 pt，正文 14–18 pt，注释 10–12 pt。正文左对齐；只对短标题或数字做居中。
- 页面边缘至少留 0.5 英寸，内容块间距至少 0.3 英寸。同类元素使用一致的栅格、间距和对齐。
- 文本框需要与图形边缘精确对齐时设置 `margin: 0`；字符间距使用 `charSpacing`，不要使用无效的 `letterSpacing`。
- 不用标题下划线、整页装饰色条或卡片单侧色边充当“设计感”。优先使用留白、轻微底色、阴影、图片裁切和图标层次。
- 不默认使用与主题无关的蓝色或米黄色；不交付低对比、越界、截断或互相重叠的元素。
- 演讲者备注只写入 `speaker_notes`，不要伪装成页面上的隐藏文本。

## 校验与视觉检查

结构校验：

```text
--input 'output/pptx/result.pptx' --check-render
```

模板派生结果：

```text
--input 'output/pptx/result.pptx' --original 'template.pptx' --check-render
```

必须满足：

- `status: valid`
- `issue_count: 0`
- `archive.missing_required_parts` 和 `duplicate_members` 为空
- `render.pdf_pages` 与 `slide_count` 一致

警告也必须逐项评估，特别是 `placeholder_text`、空页面、孤立页面部件和外部关系。

渲染全部页面：

```text
--input 'output/pptx/result.pptx' --output-dir 'tmp/pptx/<任务名>/rendered' --contact-sheet --include-pdf
```

默认 150 DPI、单次最多 30 页。可用 `--start-slide/--end-slide/--max-slides` 分批，复杂图表或小字可把 `--dpi` 提高到 180–220。联系表用于快速检查整体节奏，逐页 PNG 用于最终 QA。

逐页检查：

- 文本是否被截断、溢出或因字体替换异常换行。
- 图形、文字、页脚和来源是否重叠。
- 页面边距、列宽、卡片尺寸、基线和间距是否一致。
- 图标与文字是否有足够对比度，图表标签是否可读。
- 模板占位内容、示例数据和多余装饰是否全部清除。
- 页面顺序、标题层级、图表数值、演讲者备注和来源是否正确。

第一次渲染发现问题是正常的；修复后必须重新运行结构校验，并重新生成受影响页面的预览。
