# TASK_STATUS

更新时间：2026-07-04 Asia/Shanghai

## 1. 当前状态

图片 OCR 转 Markdown 的版面还原已达到当前可用状态，重点样例验证通过。仅保留代码逻辑与规则，历史调试流水已清除。

样例输入：`D:/test-temp/png/test_20240825121121.jpg`
样例输出：`D:/test-temp/ocr_output/99-Audit/OCR-Pending/2026-07-04_test_20240825121121.md`

已满足的输出规则：

- 标题按版面特征（字号/位置）提取，不依赖固定关键词；样例标题为 `债券类产品FRTB资本计量 违约风险资本计算示例（一）`。
- 标题同时写入 front matter `title` 和正文 `# 标题`。
- 图片噪音（PDF/编辑导航栏、PPT 页眉页脚）不进正文，统一收集到底部审核区块，交人工复核后归档。
- 页号提取到 front matter `page` 字段，用于连续图片归档。
- 正文按视觉区域（纵向带 + 上下文）组织，第一段整体输出不被左右拆分。
- 区域小标题用 `> ` 高亮。
- 表格以 Markdown 表格呈现；非表格内容不表格化。
- 相邻堆叠的多个表格按垂直间隙拆分为独立表格。
- 表格列锚点去除空列；表格旁的说明段落被剥离到表格下方，不混入单元格。

## 2. 关键文件

- `processors/markdown_generator.py`：版面还原核心，唯一未提交改动文件。
- `processors/image_handler.py`：主链路。OCR 用增强图；PP-Structure 表格优先，否则 fallback `TableBuilder`。
- `processors/ocr_router.py`：PaddleOCR 2.7.3 + PP-Structure/table 接入。
- `processors/table_builder.py`：fallback HTML 表格构建。
- `processors/preprocessor.py`：预处理；透视矫正角点用 float32。

## 3. markdown_generator 处理流程

`process()` 顺序：
1. `_partition_blocks(blocks)` → 拆成核心内容块与噪音块。
2. `_extract_page_number(noise_blocks, blocks)` → 页号。
3. `_extract_title(content_blocks, source_path)` → 标题。
4. `_generate_front_matter(...)` → 含 `title`、`page`。
5. 正文 = `# 标题` + `_generate_body_text(content_blocks, title)`。
6. `_generate_tables_section(tables)` → 外部 PP-Structure/TableBuilder 表格转 Markdown。
7. `_generate_filtered_note(noise_blocks)` → 底部审核区块。
8. `_generate_link_comments(...)`。

拼装顺序：front_matter, 标题, 正文, tables, 过滤审核, 链接注释。

## 4. 关键方法与规则

`_partition_blocks`：核心/噪音分拣。噪音 = 工具栏词命中（`_noise_kind` 返回 toolbar）、纯符号、空文本、页号，或位于页面上下 7% 边距内且 `_is_margin_noise` 命中（短文本或版权/公司等页脚词）。其余为核心内容。

`_noise_kind(text)`：分类 toolbar / symbol / empty / page_number / None。工具栏词表含填充、查找、菜单、视图、演示工具、智能图形、选择等。

`_extract_page_number`：从噪音块解析 `d/d`、`第d页`、纯数字，作为 `page`。

`_extract_title`：对（已去噪的）内容行按版面打分。窗口为页面上半部（`page_top` 到 `page_top + 0.5*page_height`）。评分 = 相对中位行高的字号权重 + 靠上位置权重；接近正文字号的行降权。取最高分行。

`_generate_body_text`：`_drop_title_row` 按整行移除标题行 → `_split_into_vertical_regions` 纵向分带 → `_split_region_columns_if_needed` 仅在平衡左右列时拆列 → `_render_region`。

`_split_into_vertical_regions`：按行间大间隙（>= 2.8*中位字高，且 >=28px）纵向切带。

`_render_region`：首行若 `_is_region_heading` 则输出 `> 标题`；其余交 `_render_mixed_rows`。

`_is_region_heading`：短行（<=24 字），命中编号/冒号结尾，或行高 >= 1.35*中位字高。

`_render_mixed_rows`：仅把连续多列（>=2 块）网格行聚成表格，单列行作段落；相邻网格行若垂直间隙 > 0.7*中位字高则切成不同表格（拆分堆叠表格）。

`_looks_like_table_region`：多数行为多列且列锚点 >=2、跨列覆盖率达标才判为表格。

`_estimate_columns`：中心 x 聚成簇取质心为列锚点；丢弃仅极少数行占用的弱簇（去空列）。

`_separate_side_notes`：剥离位于网格右界外（`min_x >= grid_right + 0.3*median_width`）且宽度 >= 2.0*中位单元格宽的段落块，作为表格下方普通文字。稀疏但正常的窄列（如 JtD short）会保留。

`_render_markdown_table`：先 `_separate_side_notes`，再按列锚点对齐单元格输出 Markdown 表格，末尾附说明文字。

## 5. 环境与依赖

Python：`C:/Users/86184/AppData/Local/Programs/Python/Python311/python.exe`

```text
paddleocr==2.7.3
paddlepaddle==2.6.2
numpy==1.26.4
opencv-python==4.6.0.66
opencv-contrib-python==4.6.0.66
```

约束：numpy 必须 1.26.4，否则 `numpy.core.multiarray failed to import` / ABI 报错。

PowerShell 注意：不支持 heredoc 与 `&&`；长内联字符串易截断；控制台显示中文可能乱码（文件本身 UTF-8，用 Python 读取校验）。

## 6. 复现与验证

```powershell
python -m py_compile processors/markdown_generator.py
python -m pytest tests/ -q
python main.py --once 'D:\test-temp\png\test_20240825121121.jpg'
```

用 Python 读取输出校验（避免控制台乱码）：

```powershell
python -c "import io; print(io.open(r'D:\test-temp\ocr_output\99-Audit\OCR-Pending\2026-07-04_test_20240825121121.md',encoding='utf-8').read())"
```

调试技巧：可写临时脚本 dump OCR blocks 到 JSON（含 text/bbox），再离线仿真 `MarkdownGenerator` 调分割阈值，避免反复跑 OCR（单图约 60-100s）。用完删除临时脚本。

注意：`main.py --once` 处理成功后退出码可能为 1，但产物已正常发布，以日志 `Published note` 为准。

最近验证：`pytest` 16 passed；样例结构正确。

## 7. 已知残留（后续优化）

- 个别单元格因 OCR bbox 定位偏差仍有轻微错位/合并。
- 少数被 OCR 拆成多段的长句偶尔并入相邻区域。
- 页脚版权长句已进过滤审核区，属预期。

## 8. Git

远端：`https://github.com/popyun/knowledgeImportHub`，分支 `main`，最新已推送提交 `6eddb2a`。

当前未提交改动：`processors/markdown_generator.py`（区域/表格/噪音过滤/页号/标题的后续修复），`TASK_STATUS.md`。达标后再按需提交推送。
