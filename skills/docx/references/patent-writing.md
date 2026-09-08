# 专利撰写、审查、答复与布局

## 模式

| 用户意图 | 处理重点 | 指南 |
| --- | --- | --- |
| 根据技术交底撰写 | 发明点 → 权利要求 → 说明书 → 摘要与附图 → 自检 | `references/patent/drafting-workflow.md` |
| 审查现有申请 | 检查支持关系、充分公开、术语及引用关系，按严重程度列出问题 | `references/patent/review-checklist.md` |
| 答复审查意见 OA | 对应每项理由和对比文件，区分争辩与修改，避免超出原始公开 | `references/patent/oa-response.md` |
| 布局/规避/FTO | 保护目标、技术拆解、竞争专利、法律状态与覆盖范围 | `references/patent/patent-strategy.md` |

先利用已有资料确定法域、申请阶段和用户期望的产物。没有完成检索、法律状态核验或对比分析时，明确尚未完成的部分，不能把写作框架当成确定的授权或不侵权结论。用户的专业程度按对话判断，不预设其职业。

以权利要求为主线；说明书支持各项技术特征；全文术语一致。从属权利要求明确引用关系，说明书按技术领域、背景技术、发明内容、附图说明、具体实施方式组织。摘要长度按申请法域核对，中文初稿通常控制在 300 字以内。

## 文件流程

Word 先读取完整结构，其他资料用 `scripts/extract_source.py`。有原稿时使用 `scripts/edit_document.py` 回填；用户需要修订对照时添加 `--track-changes --author <作者>`，只支持文本替换。复杂结构改动可另给对照说明或批注，不伪造修订状态。

无模板时调用 `scripts/create_document.py --preset patent`，保留 v3 的专利内容结构，通过统一创建和校验流程生成：

```text
--preset patent --output '/usr/local/src/word/patent.docx' --spec-file '/usr/local/src/word/tmp/<任务名>/patent.json'
```

```json
{
  "properties": {"title": "专利申请初稿"},
  "claims": [
    {"number": 1, "text": "一种由技术交底支持的方法……", "dependent": false},
    {"number": 2, "text": "根据权利要求1所述的方法……", "dependent": true}
  ],
  "specification": {
    "field": "技术领域内容",
    "background": ["背景技术内容"],
    "summary": ["技术方案与效果"],
    "drawings": ["图1的说明；实际附图需另行提供或生成"],
    "detailed": ["具体实施方式"]
  },
  "abstract": "摘要内容"
}
```

可只提供非空 claims、specification、abstract 中的一部分。权利要求编号必须从 1 连续；预设使用真实编号列表、标题样式、A4 和 2.5 cm 页边距。权利要求书、说明书、摘要分别分节，设置独立页眉并从第 1 页编号。预设使用镜像现有的 Noto/Liberation 字体，属于通用初稿排版；有明确字体或官方模板要求时按操作指南使用通用 JSON/原模板，并检查字体，不能声称预设自动满足所有官方格式。

预设中的 drawings 只生成附图说明，不生成技术附图。核对权利要求与说明书覆盖、附图实际交付、来源及用户要求后，再执行主入口校验和渲染。
