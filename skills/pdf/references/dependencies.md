# 容器依赖与能力边界

依赖由 `silk-base/Dockerfile` 预装。机器人只调用固定脚本，不能运行安装命令；需要更新依赖时由维护者重新构建和部署基础镜像。

## 复用原 PDF 工具链

| 能力 | 镜像依赖 |
| --- | --- |
| 原生文字、表格、页面和表单 | pdfplumber 0.11.9、pypdf 6.10.0、Poppler |
| 简单文本/Markdown PDF | ReportLab 4.4.9、Pillow、已安装中文与拉丁字体 |
| PDF 页及单张图片 OCR | RapidOCR 3.9.1、ONNX Runtime 1.27.0、OpenCV 4.12.0.88、OmegaConf 2.3.1 |
| Office 导出 | LibreOffice Writer/Calc/Impress 与系统字体 |
| HTML 渲染运行时 | 已有 Node.js 与系统 Chromium，`CHROME_BIN=/usr/bin/chromium` |

OCR 使用 RapidOCR 包内的检测、方向分类与识别 ONNX 模型。缺少模型就返回错误；不回退联网下载。OmegaConf 固定兼容版本，避免旧版不能处理 RapidOCR 的路径配置。

## 本次补充

| 依赖 | 用途 | 安装位置/方式 |
| --- | --- | --- |
| poppler-data | Adobe CJK 字符映射，补齐部分中文 PDF 的渲染与提取 | Debian 软件包，显式安装并检查 GB1 映射文件 |
| playwright-core 1.63.0 | 固定浏览器渲染桥接 | 全局 Node 包，复用系统 Chromium |
| Paged.js 0.4.3 | CSS 分页、命名页、页眉页脚、目录目标页码 | 全局 Node 包，本地加载 |
| KaTeX 0.18.7 | 行内/展示公式及其字体 | 全局 Node 包，本地加载 |
| Mermaid 11.17.2 | 流程、关系等示意图 | 全局 Node 包，固定 strict 模式 |
| Tectonic 0.17.0 | 用户提供的 LaTeX 源文件编译 | amd64/arm64 官方静态发行包，逐架构 SHA-256 校验 |

不需要额外引入 pikepdf、另一套 Python 浏览器、Matplotlib 或 LaTeX 完整发行版。页面/表单/元数据/图像继续复用 pypdf；数据图用静态 SVG、图片或现有 xlsx 图表。数学公式优先 KaTeX，有 LaTeX 模板时才用 Tectonic。

`poppler-data` 提供编码映射，不能用字体包替代。没有映射时即使 PDF 带有中文字体，部分文件仍可能预览缺字；遇到 `Missing language pack` 应更新镜像，不能把有缺字的预览用于 OCR 或当作空白原文。[Debian 包说明](https://packages.debian.org/trixie/poppler-data)

## 构建与离线运行

- 浏览器只从任务目录、skill 素材和固定本地包读取资源；不使用 CDN、不自动下载 Chromium。`NODE_PATH` 保留镜像的全局包目录。
- 构建时实际运行 HTML + KaTeX + Mermaid + Paged.js 自检，不能只验证包能 import。
- `TECTONIC_CACHE_DIR=/opt/tectonic-cache` 在构建时预热 ctex/Fandol、amsmath/amssymb、booktabs/longtable、graphicx、geometry、hyperref，随后验证仅缓存编译和中文文字提取。首次构建需要网络下载 TeX 资源；任务编译始终禁用 shell escape 且只读缓存包。
- 特殊模板、额外文献工具或未预热的 TeX 包可能仍不可用。维护者把真实模板所需资源加入构建预热，再部署镜像；不能在机器人任务里解除缓存限制。
- 原有宋体、黑体、仿宋、楷体、方正小标宋、微软雅黑、苹方 SC、SF Pro、Noto 等字体继续复用。渲染后仍需检查所选字体和字形，安装完成不代表所有模板无差异。

修改 Dockerfile 后必须重新构建并部署，已运行的旧容器不会自动获得新依赖。
