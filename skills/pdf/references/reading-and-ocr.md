# 原生文本、OCR 与模型辅助复核

## 识别顺序

1. `inspect_pdf.py` 检查页数与加密状态；`extract_text.py` 分批检查原生文本。
2. 可靠原生文字直接使用。对 `text_quality.suspect_pages` 调用本地 `ocr_text.py`，普通扫描件不先做整本图片识别。
3. PDF 任务中的单张截图、扫描图片用 `ocr_image.py`。文字识别以 OCR 为主，大模型用于具体疑难内容的辅助判断。
4. OCR 返回 `needs_review`、低置信度区域，或文字与表格结构/阅读顺序冲突时，先按原图质量合理提高 DPI（最多 400）。仍有疑问或涉及手写、复杂公式、图表、印章时，用 `render_pdf.py` 只渲染相关页，通过当前环境实际可用的图像查看/识别能力辅助复核。可使用已安装的 image-recognition skill；不能虚构工具或识别结果。
5. 复核时提供页码、待判断问题与 OCR 候选，区分“确认原文”“模型推测”“无法辨认”。不要让模型补写看不见的金额、账号、日期或姓名。辅助结果不能覆盖其他页已可靠提取的文字。

`needs_ocr: false` 表示原生文字无明显问题，不代表图中的趋势、流程、版式和关系已经被理解。任务需要这些非文本信息时可以按需查看相关页，不重复 OCR 全文。

## PDF 页 OCR

通过 `execute_skill_script` 调用 `scripts/ocr_text.py`，仅传参数：

```text
--input '/usr/local/src/pdf/tmp/task/source.pdf' --pages '2,5-6'
```

- 页码从 1 开始，一次最多 4 页；默认 260 DPI，允许 150–400，单页渲染不超过 2000 万像素。
- `--timeout` 是每页渲染超时，默认 180 秒，范围 1–600。
- 默认 `--max-chars 24000`，最高 60000。
- 字符游标：`next_offset > 0` 时用 `--pages <next_page> --start-offset <next_offset>` 续读该页；`next_offset = 0` 时用 `remaining_pages` 继续。单页续读结束后仍要继续先前未处理的页面。
- 使用镜像内 RapidOCR/ONNX 模型。固定脚本明确指定本地模型路径，缺少模型立即失败，不触发下载。渲染 PNG 只在脚本内部临时使用，完成即清理。

## 单张图像 OCR

调用 `scripts/ocr_image.py`：

```text
--input '/usr/local/src/pdf/tmp/task/scan.png'
```

支持 PNG/JPEG/WebP/TIFF/BMP，最大 25 MiB、2000 万像素、单帧；自动按 EXIF 调整方向。`--max-chars` 和 `--start-offset` 用于续读可靠文本。多页 TIFF 不默默只读首帧，应先得到按页文件或 PDF。

## 结果解读

- 仅使用 `usable_for_summary: true` 的 `text` 作为正文。`empty/sparse/low_confidence` 不等于空白页面；需要核验。
- 单行置信度低于 0.60 的文字不会混入可靠正文，即使整页平均置信度很高。`review_regions` 给出 `text_candidate`、置信度和像素框；这些是**待核验候选**，不能当事实。
- `needs_review: true` 可能与 `usable_for_summary: true` 同时出现：表示该页有可用文字，也有未确认区域。`complete_ocr_coverage` 只有全部请求页处理完成且无需复核时才为真。
- `review_regions` 最多返回 40 个，每段候选最多 500 字；截断时有显式标记。需要完整复核时查看对应原图，不把列表上限误认为没有其他疑点。
- OCR 的阅读顺序按几何位置排序，多栏、跨栏标题或复杂表格不保证逻辑顺序。表格先尝试 `extract_tables.py`；扫描表格用 OCR 字框对齐并核验行列、表头、合计，不把平铺文字直接当结构化表格。
- 引用使用真实 PDF 页码；记录实际阅读/识别范围，不能只处理前几页就声称已覆盖全文。
