# HTML/CSS 设计排版接口

先读 [设计规范](design.md)。简单文本使用 `create_pdf.py`；图文报告、封面、页眉页脚、目录、公式和流程图使用 `create_design_pdf.py`。

## 调用

通过 `write_file` 将内容写为 UTF-8 `.html`，以 `<!doctype html>` 声明标准模式，并包含 `<meta charset="utf-8">`。通过 `execute_skill_script` 调用 `scripts/create_design_pdf.py`，不直接调用 Node 或 Chromium：

```text
--input '/usr/local/src/pdf/tmp/task/report.html' --output '/usr/local/src/pdf/report.pdf'
```

可选 `--css <本地CSS>`、`--page-size A4|LETTER`、`--expected-pages <1–200>`、`--timeout <10–600>`（默认 180）和 `--overwrite`。HTML/CSS 单文件不超过 2 MiB。先读取 `assets/report.html` 了解结构，按实际内容编写；模板中的示例文字不可直接交付。

`assets/design.css` 由固定渲染器自动注入，提供变量、标题、六种封面、三线表、代码、引用、图注与目录。用户 HTML 内的 CSS 可覆盖默认值，`--css` 最后应用。不需要手动加载该样式或任何 JS 库。

## 静态内容与本地资源

- 只写静态 HTML/CSS/SVG。禁止 `<script>`、事件属性、iframe/object/embed、`javascript:` URL 和运行任意 JS。不能提供 Python、Bash 或 Node 代码让脚本代为执行。
- 图片、CSS 和自备字体放在 HTML 同目录及其子目录，用相对路径引用；上级目录、符号链接越界、`file:` 和外部资源会被拒绝。资源单个不超过 25 MiB。
- HTTP(S) 引用链接可以保留为可点击参考来源，但不能用作图片、CSS、字体或脚本下载入口。所需远程素材先由 `download_attachment.py` 下载。
- Paged.js、KaTeX、Mermaid 和 KaTeX 字体从镜像读取，没有 CDN 例外。渲染器使用独立浏览器上下文并限制资源访问。
- 使用语义化 `h1/h2/p/table/figure/figcaption`，对引用设置真实 `id` 和 `href="#..."`。自定义计数器跨页需核验；普通页码、目录目标页码可使用 Paged.js 支持的 counter/target-counter，不笼统禁止 CSS counter。

## 公式与流程图

不加载脚本、不写初始化代码。固定接口识别以下结构：

```html
<p>公式为 <span class="math-inline">E=mc^2</span>。</p>
<div class="math-display">\sum_{i=1}^{n} x_i</div>
<div class="mermaid">flowchart LR
 A[资料] --> B[本地 OCR]
 B --> C[疑难复核]
 C --> D[PDF]</div>
```

公式内容为 KaTeX 数学标记，Mermaid 为图形描述语言，不是任意程序入口。Mermaid 使用 neutral 主题与 strict 安全模式；不支持在图中插入配置指令。公式/图形解析失败则返回错误，不带着未渲染内容输出成功。

数据图表可用静态 SVG 或已生成图片。长公式应分行；大表/大图应调整结构、命名横向页或拆分，不能一律缩成不可读的小图。

## 分页与检查

- 默认 A4 内页与全页封面都使用实际物理尺寸；不采用原脚本固定 `scale: 1.5` 的补偿。
- 先等待图片、字体和图形完成，再等待 Paged.js 的完成 Promise。超时、外部资源、图片失败、横向溢出或页数不符时不发布结果。
- PDF 实际页数必须与分页引擎相同。`--expected-pages` 不匹配时明确失败，不裁掉页面或缩短内容冒充满足要求。
- 返回 `page_count`、每页文字/图形统计、warnings 和 `requires_visual_review`。空白页提示结合上下文判断；少字的图表页不直接判坏。
- 用 `inspect_pdf.py` 和 `extract_text.py` 核对最终文件，再用 `render_pdf.py` 查看全部页。自动检测不能发现所有纵向裁切、语义错误、缺字或表单外观问题。
