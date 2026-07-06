# Obsidian 知识导入中心

[English](README.md) | 中文

一套可用于生产的 OCR 转 Obsidian 导入系统：自动处理含表格、多语言与特殊字符的图片，
并把结构化的 Markdown 笔记发布到 Obsidian 知识库。

## 功能特性

- **自动文件监听**：监视 RAW 目录中的新图片
- **多引擎 OCR**：按内容类型路由到 PaddleOCR / MinerU / Mathpix
- **版面还原**：从 OCR 文本块重建标题、阅读顺序区域与表格
- **表格重建**：保留单元格颜色，输出 Markdown 表格与带 `bgcolor` 的 HTML 表格
- **分层表格增强（方案 A）**：可选的、仅供比对的重识别流程，对低置信表格区域用主机自选后端（`gridboost` / `vision` / `manual`）重新识别；默认关闭，且绝不替换主输出
- **噪音过滤**：编辑器工具栏 / PPT 页眉页脚会被移入底部审核区块并标注过滤原因，而非直接丢弃
- **LLM 后校正**：使用本地 Ollama 模型修正 OCR 错误
- **智能链接**：为已有 Obsidian 笔记自动生成 wiki 链接
- **持久化队列**：基于 SQLite 的任务队列，支持断点续跑
- **结构化日志**：JSON 日志，便于调试与监控

## 环境要求

- Windows，Python 3.11
- 固定的 OCR 依赖版本（请勿改动，否则会出现 ABI 导入错误）：

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
opencv-python==4.6.0.66
opencv-contrib-python==4.6.0.66
```

分层增强（方案 A）为可选项。`vision` 档额外需要本地 Ollama 视觉模型（例如 `ollama pull qwen2.5vl:3b`），且仅当首启探测到加速器或充足空闲内存时才会选中；在纯 CPU 主机上探测结果为 `gridboost`（纯 OpenCV 预处理），且增强默认关闭，需显式开启。

## 安装

### 1. 安装 Python 依赖

```bash
cd knowledge_import_hub
pip install -r requirements.txt
```

### 2. 安装 Ollama（用于 LLM 校正）

```powershell
# Windows
winget install Ollama.Ollama
```

```bash
# macOS
brew install ollama

# Linux
curl -fsSL https://ollama.com/install.sh | sh
```

### 3. 拉取 LLM 模型

```bash
ollama pull qwen2.5:1.5b
```

## 配置

编辑 `config.yaml`：

```yaml
vault:
  root: "D:/test-temp/ocr_output"        # Obsidian 库根目录
  raw_folder: "00-RAW"                    # 监听新图片的目录
  audit_folder: "99-Audit/OCR-Pending"   # 处理后笔记的发布目录

processing:
  max_worker_threads: 2                   # 并行处理线程数
  confidence_threshold: 0.85              # 低于该值触发 LLM 校正

ocr:
  ollama:
    endpoint: "http://localhost:11434"
    model: "qwen2.5:1.5b"
  table_structure:
    enhance_on_low_quality: false   # 方案 A 总开关（默认关闭）
    backend: ""                     # 空 = 按主机档位自动选择
    vision_model: "qwen2.5vl:3b"    # vision 档使用的模型
    vision_timeout: 180
```

## 分层表格增强（方案 A）

对于彩色 / 无边框幻灯片，当几何版面还原产出的表格置信度偏低时，可选的增强流程会只对该区域重新识别，并把结果作为**仅供人工比对**的区块附加在告警下方。它**默认关闭**、**绝不替换**主输出，因此开启后只会新增信息（对主渲染零回归）。

首次运行时，流水线会探测一次主机能力并缓存到 `host_profile.local.json`，映射到三档之一：

- `vision`：具备加速器（CUDA/MPS）或充足空闲内存，且本地有 Ollama 视觉模型；裁剪区由视觉模型（如 `qwen2.5vl:3b`）转写。
- `gridboost`：纯 CPU 但可用 PaddleOCR（常见情况）；对区域先去底色 / 二值化、再依据 OCR 词框补虚拟网格线，然后交给 PP-Structure 重识别。
- `manual`：PP-Structure 不可用或资源不足；不增强，仅输出低置信告警供人工复核。

在 `config.yaml` 中设 `ocr.table_structure.enhance_on_low_quality: true` 开启。`backend` 可强制指定档位（`vision` / `gridboost` / `manual` / `ppstructure`），留空则按缓存档位自动选择。删除 `host_profile.local.json` 后再次运行即可重新探测。

## 使用

### 启动监听

```bash
python main.py
```

会监听 RAW 目录、自动处理新图片，并把笔记发布到审核目录。

### 处理指定文件

```bash
python main.py --once "D:/test-temp/png/image1.jpg" "D:/test-temp/png/image2.jpg"
```

> 注意：处理成功时 `main.py --once` 的退出码也可能为 1，请以日志中的
> `Published note` 为准。

### 查看队列状态

```bash
python main.py --status
```

### 使用自定义配置

```bash
python main.py --config /path/to/config.yaml
```

## 测试

### 单元测试

```bash
pytest tests/ -q
```

### 快照测试（迭代结果存档与对比）

`test_snapshot.py` 会把每次测试结果存档，方便跨迭代对比，是验证版面改动的推荐方式。

```bash
# 对样本图跑 OCR，存档带时间戳的快照，并自动与上一份快照对比：
python test_snapshot.py run

# 复用缓存的 OCR 块（跳过慢速 OCR，仅做版面迭代）：
python test_snapshot.py run --use-cache

# 只跑指定图片：
python test_snapshot.py run --images "D:/test-temp/png/a.jpg" "D:/test-temp/png/b.jpg"

# 对比最近两份快照（不跑 OCR）：
python test_snapshot.py compare

# 列出已存档快照：
python test_snapshot.py list
```

每份快照保存每张图的 Markdown，以及记录指标的 `manifest.json`
（字符数、行数、表格行数、外部表格数、OCR 块数、置信度）。对比时会忽略
front matter 的 `date:` 行，并输出 未变 / 变化 / 新增 / 删除 统计，对变化文件给出 unified diff。
快照默认存放在 `D:/test-temp/ocr_output/_snapshots`（位于仓库之外）。

## 项目结构

```text
knowledge_import_hub/
- config.yaml                 # 配置
- main.py                     # 入口
- watcher.py                  # 文件系统监听
- queue_manager.py            # SQLite 任务队列
- run_test.py                 # 环境检查（真实测试见 test_snapshot.py）
- test_snapshot.py            # 快照测试 / 迭代对比
- host_profile.local.json     # 首次运行生成；缓存主机档位（已 git 忽略）
- processors/
  - base.py                   # 抽象基类
  - image_handler.py          # 流水线编排
  - preprocessor.py           # 图像增强（Unicode 路径安全读取）
  - color_extractor.py        # 表格颜色提取
  - ocr_router.py             # OCR 引擎选择（PaddleOCR + PP-Structure）
  - post_corrector.py         # LLM 校正
  - table_builder.py          # 兜底 HTML 表格构建
  - host_profiler.py            # 首启主机能力探测 + 档位缓存（方案 A）
  - table_enhancer.py           # 可插拔增强后端（gridboost/vision/manual）
  - markdown_generator.py     # 版面还原与 Markdown 组装
- publishers/
  - obsidian_publisher.py     # 笔记发布
- linkers/
  - entity_linker.py          # 链接候选生成
  - disambiguator.py          # 链接打分
- utils/
  - file_utils.py             # 文件操作
  - log_setup.py              # 日志配置
  - progress.py               # 进度跟踪
- tests/                      # 测试套件
- requirements.txt            # 依赖
- README.md                   # 英文说明
- README.zh-CN.md             # 本文件（中文）
```

## 处理流程

1. **图片检测**：监听器发现 RAW 目录中的新图片
2. **入队**：以 SHA-256 哈希将任务加入 SQLite 队列
3. **预处理**：文档检测、透视矫正、颜色提取
4. **内容分类**：表格 / 文本 / 混合
5. **OCR 处理**：路由到相应引擎
6. **后校正**：LLM 修正低置信度文本
7. **版面与表格**：重建区域与表格（保留颜色）
8. **可选增强（方案 A）**：开启时，对低置信表格区域用主机自选后端重识别，并以仅供比对的区块附加在告警下方
9. **Markdown 生成**：组装带 YAML front matter 的笔记
10. **实体链接**：生成 wiki 链接候选
11. **发布**：写入审核目录待人工复核

## 输出格式

```yaml
---
title: "提取到的标题"
date: 2026-05-11
page: 34
tags: ["ocr/pending", "ocr/table"]
status: pending
source: "[[00-RAW/original.jpg]]"
ocr_confidence: 0.87
---

# 提取到的标题

还原后的正文与 Markdown 表格……

<!-- Filtered non-content (nav bars / headers / footers) - review before archiving -->
<!-- Link Candidates (for review) -->
```

## 常见问题

### numpy / ABI 导入错误

保持 `numpy==1.26.4`。其他版本会报
`numpy.core.multiarray failed to import`。

### 中文路径图片读取失败

已修复：`preprocessor.py` 改用 `np.fromfile` + `cv2.imdecode` 读取图片，
替代在 Windows 上无法打开非 ASCII 路径的 `cv2.imread`。

### Ollama 连接失败

```bash
ollama list      # 检查是否在运行
ollama serve     # 重启
```

## 许可证

MIT License

## 贡献

1. Fork 本仓库
2. 创建特性分支
3. 运行测试：`pytest tests/ -q`
4. 提交 Pull Request
