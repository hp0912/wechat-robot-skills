# Typst 简历与独立 PDF 排版

Typst 将排版源文件编译为 PDF。Word 交付继续使用 DOCX 创建/编辑流程；已完成 DOCX 的 PDF 副本优先转换，保持内容一致。需要独立 PDF 简历或用户明确要求 Typst 时使用本流程。

## 内置简历模板

调用 `scripts/compile_typst.py`：

```text
--template resume --output '/usr/local/src/word/resume.pdf' --spec-file '/usr/local/src/word/tmp/<任务名>/resume.json'
```

```json
{
  "name": "张三",
  "contact": ["zhangsan@example.com", "北京"],
  "summary": "基于用户真实经历撰写的简介",
  "sections": [
    {"title": "工作经历", "items": ["公司、职位、时间", "有事实依据的职责与成果"]},
    {"title": "教育背景", "items": ["学校、专业、时间"]}
  ]
}
```

同样可用 `--spec` 传 JSON。数据按普通文本放入模板，不把用户内容拼成 Typst 代码。模板位于 `assets/resume.typ`；默认使用 Inter 与 Noto Sans CJK SC。

## 自定义 Typst 排版

把源文件和本次任务的图片/JSON 等素材放在 `/usr/local/src/word/tmp/<任务名>/`，使用可用的文件写入工具创建 `.typ`，再调用固定入口：

```text
--input '/usr/local/src/word/tmp/<任务名>/resume.typ' --output '/usr/local/src/word/resume.pdf'
```

脚本限定 Typst 项目根目录为源文件所在目录，素材引用必须在该目录内。优先使用内置语法及本地素材，不依赖临时下载的外部模板包。需要特定字体时先按 `references/fonts.md` 检查；字号、页边距和文字长度调整都应根据实际 PDF 页面验证。

默认超时 120 秒，可传 `--timeout`（1–900）；`--overwrite` 仅用于本次任务旧产物。脚本检查 PDF 可读且有页面，返回 `page_count`、`warnings` 和 `requires_visual_review`；字体警告不能忽略。最后通过可用的 `pdf` skill 检查文本和逐页渲染，检查全部页面后才交付。

官方说明：https://typst.app/docs/ ，字体设置：https://typst.app/docs/reference/text/text/ 。
