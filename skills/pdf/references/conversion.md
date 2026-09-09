# Office、LaTeX 与 PDF 转换

只调用固定脚本。不能直接运行 LibreOffice、Tectonic、Bash、Python 或 Node，也不在任务中安装依赖。

## Office → PDF

Office 主文档的编辑、公式计算、图表和版式由 docx/xlsx/pptx 等对应 skill 完成。已有完成的源文件可调用 `scripts/convert_to_pdf.py`：

```text
--input '/usr/local/src/pdf/tmp/task/report.docx' --output '/usr/local/src/pdf/report.pdf'
```

支持 DOCX/DOC/ODT/RTF、PPTX/PPT/ODP、XLSX/XLS/ODS，最大 25 MiB。`--timeout` 默认 180 秒，1–600；可用 `--overwrite` 覆盖本任务旧产物。每次转换使用独立 LibreOffice profile 和临时副本，不修改源文件。

转换前确认 Excel 公式已重算、打印范围正确；核对字体替换、图表、分页和页数。转换可能有版式差异，必须渲染检查。CSV 和 HTML 分别先走 xlsx 或 HTML 设计接口，不能含糊地自动推断编码和布局。

## PDF → Office

PDF 是固定版面，不能承诺用 LibreOffice 直接得到结构完整的 Word、Excel 或 PPT。先用原生提取/OCR 得到可靠文本和表格，再由对应 skill 的固定写入接口重建；明确哪些结构可编辑、哪些需要保留图片。扫描件不会因为换扩展名就变成可编辑文本。

需要高保真还原时先确认重点是视觉一致还是编辑结构；保留原 PDF 对照。不得把每页截图贴入 Word 后声称正文可编辑。

## LaTeX → PDF

用户明确提供 LaTeX 模板或源文件时调用 `scripts/compile_latex.py`：

```text
--input '/usr/local/src/pdf/tmp/task/main.tex' --output '/usr/local/src/pdf/paper.pdf'
```

输入 `.tex` 最大 2 MiB，相关图片和被引用的 `.tex` 放在本任务目录；超时默认 180 秒，范围 1–600。固定脚本内部调用 Tectonic，禁用 shell escape，只使用镜像构建时缓存的 TeX 资源。缺失包、未缓存模板、编译失败或超时都明确报错，不能安装或偷偷切换到联网编译。

基础镜像预热 ctex、常用数学、表格、图片、几何和超链接包；特殊模板或包可能还需维护者补充镜像。Tectonic 自行处理常见重跑与引用，不把同一编译无意义重复多次。

保留用户模板的语言、字体、章节与引用规范。中文模板可使用 `ctexart` 和已缓存的 Fandol 字体；新的模板需确认所用字体实际存在。若只有少量数学公式而没有 LaTeX 模板，HTML + KaTeX 即可，无需编写完整 LaTeX 文档。

返回页数、警告和视觉复核标记。检查 undefined references、缺字、Overfull 等具体问题；输出成功后仍需提取与渲染检查，不把“有 PDF 文件”作为通过标准。
