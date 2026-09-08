# 简历写作与优化

文件操作统一遵循主入口，参数见 `references/word-operations.md`。用户给了 Word 模板时保留原文件和样式；PDF 可作为内容与版式参考，但不能声称提取文字就保留了原 PDF 版式。

## 选择工作流

| 需求 | 流程 | 详细指南 |
| --- | --- | --- |
| 优化/职位定制/检查问题 | 读取完整简历 → 对照 JD → 改进内容 → 按用户要求回填或给建议 | `references/resume/optimization.md` |
| 填空白模板/从零撰写 | 收集必需资料 → 组织板块 → 回填或创建 | `references/resume/building.md` |
| 不知道写什么/缺少成果 | 对话梳理职责、行动与影响 → STAR 表述 | `references/resume/coaching.md` |

先确认用户已有的目标职位、语言、模板和篇幅要求，不重复询问已经给出的信息。数字和成就必须来自用户资料或可说明口径的估算；不能用范例代替事实，不把“参与”无依据改成“主导”。ATS 仅检查关键词和结构，不承诺固定评分。

## 文件处理

- DOCX：`scripts/inspect_document.py`，需要局部格式时加 `--include-runs`；长文档按游标读完。PDF：`scripts/extract_source.py`，核对多栏顺序与 OCR 状态。
- 回填：把改动整理为 `replace_text` 操作交给 `scripts/edit_document.py`，不要将整段内容塞进第一个文本片段。
- 同一占位符出现多次时，先检查目标段落或表格上下文。无法唯一定位时使用操作指南的最小 XML 编辑流程，不能全局替换所有下划线。
- 无模板新建 DOCX：`scripts/create_document.py`。仅需 PDF、且需要独立排版时，可用内置 Typst 简历模板，见 `references/typst.md`。
- 已生成 DOCX 的 PDF 副本用 `scripts/convert_document.py`，避免维护两套不一致内容。

交付前核对联系方式、日期、职位匹配、页数与原模板样式，执行主入口的结构和逐页视觉检查。
