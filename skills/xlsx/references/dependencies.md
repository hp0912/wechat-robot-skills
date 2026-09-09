# 工具链与基础镜像依赖

目标镜像由 `/Users/zuihoudeqingyu/Git/wechat/silk-base/Dockerfile` 构建，运行时使用 `/opt/venv`。新配置只有重新构建并部署镜像后生效，skill 任务中不临时 pip/apt 安装。

| 能力 | 实现 | 镜像状态 |
| --- | --- | --- |
| 文件下载、路径/JSON 校验 | Python 标准库 | 已有 |
| 工作簿读写、模板保留、公式、图表、列宽行高 | openpyxl 3.1.5、Pillow | 已有 |
| 清洗、分组、交叉汇总、描述统计、规则分类 | pandas 3.0.5、NumPy | 已有 |
| 评价与基础时序外推 | NumPy | 已有，无需新库 |
| 工作簿转换、公式计算 | LibreOffice Calc | 已有 |
| 逐页 PNG/PDF 预览 | LibreOffice + Poppler | 已有 |
| 回归、分类、聚类、Isolation Forest | scikit-learn 1.9.0 | 本次补充 |
| LP/MILP 与受控二次规划 | SciPy 1.18.1（HiGHS / SLSQP） | 本次补充 |
| 文本翻译/语义抽取 | 模型读取原文，原写入脚本回填 | 不依赖外部翻译服务 |

选择两个新增库是为了补上现有表格工具的模型训练和优化求解缺口。常规表格和图表继续使用原工具链；无需再安装 Excel COM、桌面 Excel、xlsxwriter、外部 CBC/PuLP 求解器、deep-translator、XGBoost、Prophet、Matplotlib 或浏览器展示组件来完成当前接口。

新增包固定版本并限定二进制 wheel 安装，PyPI 提供 Linux amd64/arm64 对应构建；镜像已有 libgomp1。SciPy/scikit-learn 与当前 pandas、OpenCV 要求的 NumPy 2.2.x 已做临时环境兼容性验证。构建步骤会实际运行最小线性模型与整数求解自检，缺包或二进制不兼容直接失败。没有在本机运行 Docker 构建。

参考：[SciPy 发行包](https://pypi.org/project/scipy/1.18.1/)、[scikit-learn 发行包](https://pypi.org/project/scikit-learn/1.9.0/)、[SciPy MILP](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.milp.html)、[模型预处理与数据泄漏](https://scikit-learn.org/stable/common_pitfalls.html)。

本 skill 输出静态交叉汇总表和普通图表；原生 PivotTable、切片器、任意 Python 建模、自定义神经网络或任意非线性求解不属于已提供接口，不能通过文案声称已经支持。遇到明确的额外需求时，先评估固定脚本和现有库能否扩展，再评估新增依赖。
