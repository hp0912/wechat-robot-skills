# 表单、裁剪、元数据和嵌入图片

通过 `execute_skill_script` 调用 `scripts/edit_pdf.py`。所有输出遵循 `/usr/local/src/pdf/`；不覆盖源文件。原有合并、拆分、旋转仍用 `manage_pdf.py`。

## 表单

先查看真实字段：

```text
form-info --input '/usr/local/src/pdf/tmp/task/form.pdf'
```

返回字段 id、类型、当前值、只读状态、选择项和按钮状态。默认 50 项，`--offset`/`--limit`（最高 200）支持继续。没有 AcroForm 字段的扫描表单不能直接套用字段填写；XFA 和数字签名不是本接口支持的填写类型。

填写明确要求的字段：

```text
form-fill --input '/usr/local/src/pdf/tmp/task/form.pdf' --output '/usr/local/src/pdf/filled.pdf' --data '{"name":"Alice","agree":true,"country":"CN"}'
```

较长数据用 `--data-file <JSON>` 替代 `--data`，二选一，上限 2 MiB、500 个字段。

- 文本值必须是字符串，遵守字段长度。复选框可用 JSON `true/false`，或实际状态名如 `/Yes`；不能把非空字符串一律当作选中。
- 单选按钮使用实际状态值；下拉/列表使用真实选项值，多选需原字段支持。无效值、未知字段、只读字段或签名字段明确失败。
- pypdf 更新字段值与外观，再回读校验。必须渲染核对文字位置、换行、复选框和单选按钮状态。原表单字体未包含中文等字形时，不能仅凭字段值正确就交付；需保留可显示的模板或按用户要求重建表单版式。
- 不把填写文本当作签署数字签名，也不主动提交表单。

## 裁剪

```text
crop --input '/usr/local/src/pdf/tmp/task/source.pdf' --output '/usr/local/src/pdf/cropped.pdf' --pages '1-2' --box '20,30,575,812'
```

坐标单位 pt，按未旋转 PDF 坐标系的左、下、右、上填写；框必须在页面 MediaBox 内。省略 `--pages` 处理全部页。修改 CropBox，保留页面内容。**裁剪不删除不可见内容，不是脱敏。** 敏感信息移除需专门的真正删改流程，不能用白块或裁剪冒充。

## 元数据

读取用 `inspect_pdf.py`。更新明确字段：

```text
metadata --input '/usr/local/src/pdf/tmp/task/source.pdf' --output '/usr/local/src/pdf/updated.pdf' --data '{"Title":"报告","Author":"机构"}'
```

支持 Title/Author/Subject/Keywords/Creator/Producer；保留未指定字段。该接口修改文档信息字典，现有 XMP 保留且通过 `xmp_preserved` 提示，不能声称已清理所有元数据。

## 嵌入图片

```text
extract-images --input '/usr/local/src/pdf/tmp/task/source.pdf' --pages '2-3' --output-dir '/usr/local/src/pdf/tmp/task/images'
```

一次最多 4 页、默认 20 张图片（`--max-images` 最高 50），本批总大小不超过 25 MiB。用 `remaining_pages` 和 `--start-image <next_image>` 续读。需要覆盖本任务旧输出时加 `--overwrite`。

此操作提取 PDF 中的图像对象，不代表完整页面：不包含周围文字、矢量图形，也可能是被裁剪/复用的图片。阅读扫描页和视觉复核应使用 OCR 内部渲染或 `render_pdf.py`，不能把提取出的零散图片当成原 PDF 页。
