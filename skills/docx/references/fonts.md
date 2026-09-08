# 运行依赖与字体

基础镜像预置 Python 文档/PDF/OCR 库、LibreOffice、Pandoc、Poppler、Node docx、Typst、Fontconfig。任务中不安装软件。依赖问题用 `scripts/inspect_environment.py` 检查实际容器；Dockerfile 中的安装声明不等于某个旧运行容器已更新。

```text
--font 'Arial' --font 'Times New Roman' --font 'SimSun' --font 'FangSong' --font 'Microsoft YaHei' --font '方正小标宋简体' --font 'PingFang SC' --font 'SF Pro'
```

返回 `tools/packages/missing_tools/missing_packages` 及 `requested_fonts/missing_fonts`。字体按 `fc-list` 实际家族名匹配；`fc-match` 找到替代字体不能证明原字体存在。`ok: true` 仅表示检查执行成功，仍需阅读缺项。

## 字体选择

| 需求 | 可用字体来源 | 使用方式 |
| --- | --- | --- |
| 通用中文、简历、界面文字 | 镜像的 Noto Sans CJK SC / Noto Serif CJK SC | 默认可用，覆盖中文正文 |
| 宋、黑、楷、仿宋风格 | CTAN Fandol：FandolSong / FandolHei / FandolKai / FandolFang | 使用实际家族名；缺字可由 Noto 补充，不冒称原版宋体/仿宋 |
| Arial、Times New Roman | Debian 的 Microsoft core fonts 安装包 | 实际库存确认后用于西文；失败时明确提示或采用已有 Liberation 替代 |
| 原版宋体、黑体、仿宋、楷体 | 镜像的 SimSun / SimHei / FangSong / KaiTi | 使用实际家族名或对应中文名；仿宋和楷体不等于另行指定的 GB2312 版本 |
| 微软雅黑 | 镜像的 Microsoft YaHei / Microsoft YaHei UI | 已配置常规、粗体、细体，按模板使用对应家族 |
| 方正小标宋简体 | 镜像的 FZXiaoBiaoSong-B05S / 方正小标宋简体 | 使用完整家族名；不将简称“方正小标宋体”或 GBK 版本当作已安装的家族 |
| 苹果风格简体中文 | 镜像的 PingFang SC / 苹方-简 | 已配置六种字重；设置为东亚字体 |
| 苹果风格英文与数字 | 镜像的 SF Pro | 已配置可变字重正体和斜体；设置为西文字体 |
| Wingdings 等其他指定字体 | 额外字体文件 | 当前默认字体库不包含；可在镜像 custom-fonts 目录接入 |

用户已有模板优先保留字体。要求精确字号、行数、分页或官方字体时，生成前检查指定家族；缺少精确字体时说明实际使用的字体，不能把替代显示称为完全一致。正式公文的适用规范与字体要求按用户模板及主管机关要求核对。

## 苹方与 SF Pro 混排

用户要求苹果风格时，Word 创建说明可加入以下设置，并补全实际 `blocks`。沿用用户已有模板时保留其指定字体。

```json
{"default_font": {"name": "SF Pro", "east_asia": "PingFang SC"}}
```

单个 Run 使用 `font: "SF Pro"` 和 `east_asia_font: "PingFang SC"`。Typst 使用 `#set text(font: ("SF Pro", "PingFang SC"))`，让英文、数字优先采用 SF Pro，中文采用苹方 SC。字体存在不代表不同系统的字号、字距和渲染完全相同，仍需按文件流程检查页面。

## 来源

- Fandol 字库及 GPL 字体例外说明：https://ctan.org/pkg/fandol
- Debian Microsoft core fonts 包：https://packages.debian.org/trixie/ttf-mscorefonts-installer
- Typst 官方发行版：https://github.com/typst/typst/releases

原版中文字体的固定下载地址、版本与 SHA-256 见基础镜像 `fonts/cjk-fonts.tsv`、`fonts/apple-fonts.tsv` 和 README；以实际运行容器库存为准。
