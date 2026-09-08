---
name: docx
description: "创建、读取、编辑、转换、批注、修订和校验 Word 文档，并处理简历优化、专利撰写/审查/答复/布局、签证材料填写和公文写作。提供 Word/PDF/文本资料提取、图片 OCR、模板回填及 Typst 简历 PDF 排版。用户提供 Word 文档或提出上述专业文书需求时使用；独立 PDF 操作、电子表格、Google Docs 或普通代码任务使用对应技能。"
---

# Word 与专业文书

按需读取专业指南，文件操作统一使用下表中的固定脚本。普通 Word 任务直接进入文件流程；仅咨询内容时回答用户的问题，不额外生成文件。

## 调用与运行环境

- 用 `read_skill_resource` 读取资料，传 `skill_name="docx"` 和完整的根目录相对路径，例如 `references/resume-writing.md`。所有指南中的 `references/`、`scripts/`、`assets/` 都以本 skill 根目录为基准，不用 `../`，也不把业务指南当成独立 skill 激活。
- 当前机器人通过 `execute_skill_script` 执行固定脚本，传 `skill_name="docx"`、`script_path="scripts/create_document.py"` 和脚本参数。不要执行 shell、临时 Python/JavaScript 代码，或把 `python3`、`uv run`、系统命令作为脚本参数。
- 只执行下表的入口，不执行目录或以 `_` 开头的内部模块。LibreOffice、Pandoc、Poppler、Typst、ZIP 操作由入口脚本在内部调用。
- 每次检查返回 JSON 的 `ok`；失败时按 `error` 调整参数或报告失败。校验还需 `status: valid`、`issue_count: 0`。
- 保留用户源文件。最终文档写入 `/usr/local/src/word/`，缓存、资料与中间文件写入 `/usr/local/src/word/tmp/<任务名>/`，文件参数使用绝对路径。
- 容器依赖在镜像构建时安装，任务中不安装包。需要核对依赖、特定字体或排查字体替换时运行 `scripts/inspect_environment.py`，参考 `references/fonts.md`。
- 不在回复或文件名中复述带敏感查询参数的完整下载 URL。

## 专业指南

只读取本次需要的指南，再按其中索引加载细节。

| 用户需求 | 指南 | 内容 |
| --- | --- | --- |
| 简历、CV、职位定制、模板填写、成就梳理 | `references/resume-writing.md` | 保留模板、内容优化、ATS 检查、STAR 辅导 |
| 权利要求、说明书、技术交底、审查意见、专利布局、FTO | `references/patent-writing.md` | 撰写、审查、答复、策略；专利生成预设 |
| 签证表格、申请说明信、行程单、邀请函、在职证明 | `references/visa-documents.md` | 字段核对、资料一致性、支持材料模板 |
| 起诉状、答辩状、法律公告、会议通知、会议纪要 | `references/official-documents.md` | 文书要素与模板；按适用场景核对格式 |
| Word 详细参数、JSON 结构、高级 XML 操作 | `references/word-operations.md` | 所有 Word 操作的统一接口 |
| 用户要求 Typst 排版，或从零创建 PDF 简历 | `references/typst.md` | 固定编译入口、简历模板和 PDF 检查 |

## 文件流程

1. **确定交付物**：用户明确要求的格式优先，其次沿用原模板，未指定时按需求选择。Word 交付真实 DOCX；Word 的 PDF 副本用转换脚本。PDF 源资料不保证能恢复成与原版完全一致的可编辑 Word，重建时说明版式差异。
2. **读取源资料**：远程 Word 先用 `download_document.py`，其他任务附件用 `download_attachment.py`（上限 25 MiB）。DOC/DOTX 先转换 DOCX。编辑、总结前用 `inspect_document.py` 读取正文、表格、章节、页眉脚、批注和修订，并按游标续读。PDF/TXT/MD/HTML 资料用 `extract_source.py`，其参数见下节。
3. **按需 OCR**：Word 图片或扫描页使用 `ocr_document.py`，只采用 `usable_for_summary: true` 的文字，不覆盖可靠原生文本。PDF 返回 `needs_ocr: true` 时，通过可用的 `pdf` skill 对指定页 OCR；不要直接读取二进制文件假定已有正文。
4. **组织内容**：专业文书按对应指南处理；保留来源、真实事实、用户明确的格式和已有授权。缺失关键事实时询问，不编造姓名、指标、日期、依据或正式签发状态。
5. **生成或回填**：无模板用 `create_document.py`；有模板用 `edit_document.py`；批注用 `add_comment.py`。需要保留模板时避免重建整份文件。普通跨 Run 替换保留未修改片段样式；替换后检查分页与内容溢出。
6. **处理修订**：依据用户已经明确的意图保留或接受原修订，未明确才询问。仅文本替换需要显示修订时，可使用 `edit_document.py --track-changes --author <作者>`；其范围与限制见操作指南，不把结构或样式修改伪装为修订。
7. **校验与渲染**：创建/修改 DOCX 后运行 `validate_document.py --check-convert`，再用 `render_document.py` 渲染全部页面并逐页检查；按 `next_page` 继续到 `has_more: false`。检查修订、批注、页眉脚、表格、图片、目录、乱码和空白页。Typst 产物按对应指南检查 PDF。
8. **交付**：核对用户要求和承诺的内容已覆盖，适用的来源放入文档并在关键数据旁标注；模板不允许新增来源章节时另附来源说明。用户要求向当前微信会话交付文件时，激活可用的 `send-file` skill 发送最终文件；未授权发送时只报告已生成的文件。没有对应交付工具时如实说明，不调用不存在的通知或画布工具。

## 可执行脚本

| 脚本 | 用途 |
| --- | --- |
| `scripts/inspect_environment.py` | 检查依赖与精确字体家族；`--font` 可重复传入 |
| `scripts/download_document.py` | 安全下载并校验 HTTPS Word 文件 |
| `scripts/download_attachment.py` | 下载任务附件，限制 25 MiB |
| `scripts/inspect_document.py` | 分段读取 Word 正文、表格、样式、批注、修订 |
| `scripts/extract_source.py` | 分页读取 PDF 及 TXT/MD/HTML 支持资料 |
| `scripts/ocr_document.py` | 提取 Word 图片/扫描页中的可靠文字 |
| `scripts/create_document.py` | JSON 创建 DOCX；`--preset patent` 生成专利文档 |
| `scripts/edit_document.py` | 模板回填、替换、插入删除、调整页面；可记录文本替换修订 |
| `scripts/add_comment.py` | 为精确文本范围添加批注 |
| `scripts/accept_changes.py` | 接受全部已有修订并保留原文件 |
| `scripts/convert_document.py` | Word 格式转换及 PDF/Markdown/文本导出 |
| `scripts/unpack_document.py` | 安全解包，仅用于常规接口不能完成的 XML 编辑 |
| `scripts/pack_document.py` | 安全打包 OOXML 目录 |
| `scripts/validate_document.py` | 检查 ZIP、XML、关系、批注、修订元数据与可渲染性 |
| `scripts/render_document.py` | 分批生成 Word 逐页 PNG/PDF |
| `scripts/compile_typst.py` | 编译任务内 Typst 源文件，或使用内置简历模板生成 PDF |

## 支持资料提取

调用 `scripts/extract_source.py`，例如：

```text
--input '/usr/local/src/word/tmp/<任务名>/resume.pdf' --page 1
```

- 输入支持 PDF/TXT/MD/HTML/HTM，最大 25 MiB。Word 使用 `inspect_document.py`，旧格式先转换。
- PDF 每次读取一页；`--columns auto|1|2` 控制单/双栏，默认保守检测中央栏间距。表格从正文中排除后另列，避免重复。复杂跨栏布局须按原页核对阅读顺序；精细表格单元格提取可使用 `pdf` skill。
- `--max-chars` 为 256–12000，默认 12000；`has_more: true` 时把 `next_page` 作为 `--page`，`next_offset` 作为 `--start-offset` 继续。TXT/MD/HTML 使用逻辑页 1 和字符偏移续读。
- `usable_for_summary: false` 的文本不能作为可靠事实；扫描或乱码 PDF 的 `needs_ocr: true` 表示需对该页补 OCR。`has_images` 本身不是执行 OCR 的理由。
