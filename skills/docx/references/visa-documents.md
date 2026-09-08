# 签证材料与官方模板填写

适用于签证表格、申请说明信（cover letter）、行程单、邀请函、在职证明及其他官方 Word 模板。表格字段和支持材料以目的地、签证类别、申请地点和当前官方清单为准；cover letter 不自动解释为求职信。

## 流程

1. 收集已提供的模板及个人资料。Word 用 `scripts/inspect_document.py`；PDF/TXT/MD/HTML 用 `scripts/extract_source.py`。图片可按需放入临时 Word 后用 `scripts/ocr_document.py` 识别；姓名、护照号、日期等关键字段必须与清晰原件或用户确认核对，低置信度 OCR 不作为事实。
2. 识别模板字段，区分已填、待填、不适用项；字段与日期格式参考 `references/visa/field-mappings.md`，具体规则以当前表格要求为准。
3. 只询问缺失且必要的信息；可从出入境日期计算天数，但要区分入境停留天数与住宿晚数。
4. 用 `scripts/edit_document.py` 精确回填。重复下划线或复选框必须结合字段上下文定位；不能全文替换全部占位符。固定接口不能唯一定位时按 `references/word-operations.md` 的 XML 流程最小修改，保留格式与关系。
5. 无模板时参考 `references/visa/common-visa-docs.md` 的结构，再用 `scripts/create_document.py` 创建；不编写临时 Node/Python 生成脚本。
6. 核对姓名、证件号、日期、行程、金额、雇主信息在所有文件中的一致性，再执行主入口校验和逐页渲染。

姓名、地址、语言、日期顺序、货币、电话国家代码、护照有效期均按对应官方要求；不把“至少六个月”视作所有国家与签证类别的固定规则。没有真实预订或任职依据时保留待填项，不能虚构预订确认、雇主签名或公章。用户资料只用于所请求的文件。
