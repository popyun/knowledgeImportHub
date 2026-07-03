# TASK_STATUS

更新时间：2026-07-03 Asia/Shanghai

## 1. 当前状态

图片 OCR 转 Markdown 的版面还原已达到当前可用状态。重点样例验证通过：

- 标题正确：`债券类产品FRTB资本计量 违约风险资本计算示例（一）`，不再依赖固定关键词。
- 正文首段整体成段，不再被左右拆开，也不再被误判为区域小标题。
- 区域小标题（如 `> 债券 示例`）用 `>` 高亮。
- 全文不再统一插入表格；仅连续网格行渲染为 Markdown 表格。
- 页面内左右两个表格区域分别成表。

仅代码逻辑保留，历史调试流水已清除。

## 2. 关键文件与职责

- `processors/markdown_generator.py`：版面还原核心，本轮主要改动文件。
- `processors/image_handler.py`：图片处理主链路，OCR 用增强图，PP-Structure 表格优先，否则 fallback `TableBuilder`。
- `processors/ocr_router.py`：PaddleOCR 2.7.3 + PP-Structure/table 接入。
- `processors/table_builder.py`：fallback HTML 表格构建。
- `processors/preprocessor.py`：预处理，透视矫正角点用 float32。

## 3. markdown_generator 逻辑约定

标题提取 `_extract_title`：按版面特征打分（bbox 高度、靠上位置、行宽、长度），取内容区最高分行，无候选回退文件名。

正文生成 `_generate_body_text(blocks, title)`：
1. 过滤工具栏噪声 `_is_toolbar_noise`。
2. `_drop_title_row` 按整行文本移除标题行。
3. `_split_into_vertical_regions` 先按行间大间隙纵向分带。
4. `_split_region_columns_if_needed` 仅当出现平衡左右列时拆分（`_is_balanced_column_split`）。
5. `_render_region` 渲染区域：首行若为短标题则输出 `> 标题`，其余交给 `_render_mixed_rows`。

`_render_mixed_rows`：只把连续的多列网格行聚成表格，单列行仍作段落。
表格判定 `_looks_like_table_region`：基于列锚点 `_estimate_columns` 和跨列覆盖率，多数行需覆盖 >=2 列且占比 >=0.6。
表格渲染 `_render_markdown_table`：按列锚点对齐单元格，输出标准 Markdown 表格。
外部表格 `_generate_tables_section`：PP-Structure/TableBuilder 的 HTML 经 `_html_table_to_markdown` 转 Markdown。

## 4. 环境与依赖

Python：`C:/Users/86184/AppData/Local/Programs/Python/Python311/python.exe`

可用 OCR 依赖组合：

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
opencv-python==4.6.0.66
opencv-contrib-python==4.6.0.66
```

约束：numpy 必须 1.26.4，否则触发 `numpy.core.multiarray failed to import` / ABI 报错。

PowerShell 注意：不支持 heredoc 与 `&&`；长内联字符串易截断；控制台显示中文可能乱码（文件本身为 UTF-8，用 Python 读取校验）。

## 5. 复现与验证命令

```powershell
python -m py_compile processors/markdown_generator.py
python -m pytest tests/ -q
python main.py --once 'D:\test-temp\png\test_20240825121121.jpg'
```

样例输出：

```text
D:/test-temp/ocr_output/99-Audit/OCR-Pending/2026-07-03_test_20240825121121.md
```

用 Python 读取输出校验（避免控制台乱码）：

```powershell
python -c "import io; print(io.open(r'D:\test-temp\ocr_output\99-Audit\OCR-Pending\2026-07-03_test_20240825121121.md',encoding='utf-8').read())"
```

注意：`main.py --once` 处理成功后进程退出码可能为 1，但产物已正常发布，以日志 `Published note` 为准。

最近验证：`pytest` 16 passed；样例 Markdown 结构正确。

## 6. 已知残留问题（后续优化）

- 个别表格单元格因 OCR bbox 定位偏差出现错列/空列，属识别精度问题。
- 部分被 OCR 拆成多段的长句，偶尔会并入相邻表格；可按行宽/文字密度进一步区分段落与表格行。
- 页脚版权长句仍作为正文输出，可扩展 `_is_toolbar_noise` 页脚规则。

## 7. Git

远端：`https://github.com/popyun/knowledgeImportHub`，分支 `main`。

当前未提交改动：`processors/markdown_generator.py`、`TASK_STATUS.md`。达标后再按需提交推送。
