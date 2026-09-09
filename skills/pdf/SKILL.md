---
name: pdf
description: "读取、OCR、创建、设计排版、转换和处理 PDF。支持本地文件与 HTTPS PDF、扫描件和任务图像，以原生文本提取及本地 OCR 为主，模型辅助疑难复核；提供 HTML/CSS 出版排版、文本生成、Office/LaTeX 导出、表单、页面与元数据操作。用户要求阅读或总结 PDF、识别扫描件、制作报告/简历/提案 PDF 或编辑现有 PDF 时使用；Office 主文档编辑由对应 skill 处理。"
---

# PDF 读取、设计与处理

## 运行约定

- 当前机器人不能直接运行 Bash、Python、Node.js 或系统命令。只通过 `execute_skill_script` 调用下列真实存在的固定脚本；参数是文件、文本、页码或受控 JSON，不能传 shell 命令、`-c`、`eval`、代码片段或解释器命令。固定脚本可在内部调用预置引擎，调用者不直接执行底层程序。
- `scripts/_pdf_common.py`、`scripts/_render_html.cjs` 是内部实现，不直接执行；不调用原 pdf-skill 的 shell 安装器、通用 Python/Node CLI，不创建临时可执行脚本。
- 依赖只在基础镜像构建时安装。任务中不运行 pip/npm/apt、不下载浏览器、TeX 包或 OCR 模型；缺失时报告具体依赖和镜像需更新，不能假装处理成功。
- 最终 PDF 写入 `/usr/local/src/pdf/`；缓存、源 HTML/JSON、预览放在 `/usr/local/src/pdf/tmp/<任务名>/`。传绝对路径，保留用户源文件。`--overwrite` 仅用于本任务已生成的旧产物。
- 每次检查返回 JSON；`ok: false` 先处理原因。`requires_visual_review`、`needs_review` 或 warnings 需要实际核验，程序运行成功不代表内容和版式通过。
- HTTPS PDF 用 `download_pdf.py`，其他素材用 `download_attachment.py`。不在回复中复述敏感 URL 查询参数；加密 PDF 请用户提供已解密副本，密码不进入工具参数。

## 按任务读取

| 任务 | 指南 |
| --- | --- |
| 阅读、总结、扫描页、图片文字、图表辅助识别 | [读取与 OCR](references/reading-and-ocr.md) |
| 创建报告、提案、简历、学术或品牌 PDF | [设计规范](references/design.md) + [HTML 创建接口](references/creation.md) |
| Office/LaTeX 导出，PDF 内容重建为 Office | [转换](references/conversion.md) |
| 表单填写、裁剪、嵌入图片、元数据 | [编辑](references/editing.md) |
| 下载、分段提取、表格、简单文本 PDF、合并/拆分/旋转、渲染与清理 | [原有操作接口](references/operations.md) |
| 镜像缺包或能力边界 | [依赖说明](references/dependencies.md) |

## 固定脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/download_pdf.py` | 安全下载并校验不超过 25 MiB 的 HTTPS PDF |
| `scripts/download_attachment.py` | 下载不超过 25 MiB 的任务附件 |
| `scripts/inspect_pdf.py` | 页数、尺寸、加密、元数据与表单数量 |
| `scripts/extract_text.py` | 多引擎正文提取、逐页质量检测和字符游标 |
| `scripts/ocr_text.py` | 对指定 PDF 页执行离线 OCR，标记局部疑难区域 |
| `scripts/ocr_image.py` | 对单张任务图片执行离线 OCR |
| `scripts/extract_tables.py` | 原生 PDF 表格分页提取 |
| `scripts/render_pdf.py` | 按页输出 PNG，供版式检查或疑难辅助复核 |
| `scripts/create_pdf.py` | 简单文本/Markdown 生成 PDF |
| `scripts/create_design_pdf.py` | 静态 HTML/CSS、图表和公式设计排版 |
| `scripts/convert_to_pdf.py` | Office 文件导出 PDF |
| `scripts/compile_latex.py` | 仅用镜像缓存资源编译 LaTeX |
| `scripts/edit_pdf.py` | 表单、裁剪、元数据和嵌入图片 |
| `scripts/manage_pdf.py` | 合并、拆分、旋转 |
| `scripts/cleanup_pdf_temp.py` | 清理本任务临时目录 |

## 工作原则

1. 阅读先检查 PDF，再提取可靠原生文本；扫描页或图片文字以本地 OCR 为主。只有低置信度、手写、复杂表格/公式、阅读顺序冲突或非文本图形理解需要时，才用大模型复核相关页/区域。不要把整个扫描件直接交给大模型代替 OCR。
2. 创建设计先确认读者、用途、内容与输出限制，按需选择封面、配色和字体层级。用户模板、品牌、大纲、语言与篇幅优先；不强加独立封面，不为凑页数填充或删除内容。
3. 简单文字选 `create_pdf.py`；需要封面、图文、页眉页脚、交叉引用、数学公式时选 `create_design_pdf.py`。通过 `write_file` 写静态内容文件，不写可执行代码。HTML 禁止脚本、事件处理程序和外部资源；公式、流程图由固定引擎本地处理。
4. 编辑现有 PDF 保留内容与结构。裁剪不等于脱敏；表单字段值写入不等于外观正确；PDF 转 Office 应按提取/OCR 后重建来规划，不能承诺无损逆转换。
5. 创建、转换或修改后重新检查页数、文本与关键数字，再渲染全部相关页逐页核验封面、字体、表格、公式、图表、页码、裁切和空白页。要求精确页数时使用 `--expected-pages`。修正后检查最新产物，才交付。
6. 内容引用可核验。用户提供的材料可直接引用；新增时效、专业或不确定事实使用当前可用搜索工具查证，不编造统计、论文或参考文献。图片识别的猜测与原文分开标记。
