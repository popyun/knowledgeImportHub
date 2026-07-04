# TASK_STATUS

更新时间：2026-07-04 Asia/Shanghai（迭代二）

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

## 7.1 本轮修复（2026-07-04 迭代二）

- 修复非 ASCII 路径读图失败：`processors/preprocessor.py` 新增 `_imread_unicode`，用 `np.fromfile` + `cv2.imdecode` 读取，替换 `cv2.imread`。此前中文文件名（如 `微信图片_*.jpg`）会 `Failed to read image`，现已可正常处理并发布。
- 修复标题串行拼接错误：`_extract_title` 评分改进——字号比值上限 3.0，避免单个超高块（多行合并单元格）霸榜；对块数 >=4 的宽表头行按 `25*(len-3)` 降权、文本长度 >45 按 `1.5/字` 降权；有效标题长度窗口收紧为 6-40 字。样例 `微信图片_20240825121238` 标题由整行表头串（“…隐含波动以发行人信…”）纠正为 `敏感度资本计量 一计算步骤（续）`。
- 抽样测试：`D:/test-temp/png` 取 `微信图片_20240825121053/121154/121238.jpg` 三张，经完整链路发布到 `D:/test-temp/ocr_output/99-Audit/OCR-Pending/2026-07-04_微信图片_*.md`；参考样例 `test_20240825121121` 无回归；`pytest tests/ -q` = 16 passed。
- 残留：多风险因子矩阵型幻灯片（大跨度网格）因 OCR bbox 精度，左右并排的独立小表仍会并入同一区域，列错位偏多；属后续区域拆分优化项，未在本轮修改分割阈值以避免参考样例回归。

## 7.2 扩大测试发现的问题（2026-07-04 迭代三，P1 已修复，其余待修）

抽样：`D:/test-temp/png` 新增 3 张 `微信图片_20240825121125/121202/121248.jpg`，OCR 均成功，离线复核版面还原。发现以下待修问题，本轮仅记录不修改代码：

- 【P1｜正文被误过滤】样例 `121202`：多段正文（如“相关性风险：…”“交易账户中需要计提Vega…”）被丢进底部过滤审核区块。根因：`_noise_kind` 工具栏词表含“工具/视图/格式/选择”等高频单字，命中所有含这些字的正常长句（例如“金融工具”命中“工具”），且 `kind=="toolbar"` 不看位置直接判噪音。建议：工具栏词命中仅在“位于页面上/下边距 + 短文本（如 <=12 字）”时才判为 toolbar；或改成更精确的整词/组合匹配，避免长句误杀。

- 【P1｜正文与表格重复输出】样例 `121248`：三栏并排文字（Delta/Vega/Curvature 风险资本说明）在正文里输出一次后，`## Tables` 区又把整块相同内容当作一个表格再输出一次，内容完全重复。根因：外部 `table_builder` 把三栏说明文字判成一个表，正文与 `_generate_tables_section` 两处都渲染同一批 block。建议：正文已渲染的 block 不再进外部表格；或对纯文字型“伪表格”不启用 table 输出。

- 【P2｜大矩阵识别混乱】样例 `121125`：18×18 相关性系数矩阵，数字粘连、列严重错位、表格结构基本失真。属 OCR bbox 精度极限的加剧场景，与迭代二记录的“并排矩阵拆分”同源。建议：此类超大数值网格优先走 PP-Structure/table 结构化，或在报告中标注“矩阵识别置信度低，需人工核对”。

- 【P3｜标题前导符号】样例 `121125` 标题为 `债券类产品FRTB资本计量 -CSRDelta计算示例（四）`，多了一个前导 `-`。建议：标题清洗时去除行首孤立的连字符/符号。

- 【P3｜过滤区混入正文重复句】样例 `121248` 过滤区块里除页脚版权外，还混进了正文里已出现的句子（如“在计量前，会先识别…”）。与上面的正文误过滤/表格重复同源，修复前两项后应一并缓解。

## 7.3 本轮修复（2026-07-04 迭代四）

已修复 7.2 中的两个 P1，并新增过滤原因标注。改动仅在 `processors/markdown_generator.py`。

- 【P1a 已修复｜正文被误过滤】`_noise_kind` 词表拆分为强词/弱词两类：
  - 强词（`智能图形/另存为/幻灯片/放映/批注/缩放`）任意位置命中即判 toolbar。
  - 弱词（`工具/视图/格式/选择/文本框/形状/轮廓/对齐/旋转/艺术字/绘图/演示工具` 等高频且歧义词）：单个弱词仅在“文本 <=8 字 且 位于页面上下边距”才判 toolbar；**当同一段命中 >=2 个不同弱词时，无论长短/位置一律判 toolbar**（方案1，用于捕获 OCR 粘连的工具栏残片，如 `文本框形状多排列口轮廊替换`）。
  - 已移出弱词表的易组词项：`排列/组合/字体/段落/样式`，避免“排列组合”等正常词误伤。
  - 效果：`121202` 六段正文全部回归正文；参考样例 `121121` 顶部乱码串重新被正确过滤；`金融工具` 等含单弱词长句不再误删。

- 【P1b 已修复｜正文与表格重复输出】`process()` 在生成 tables 前调用新方法 `_drop_body_duplicate_tables`：外部表格若其单元格文本 >=60% 已出现在正文 block 中，则丢弃该表，避免 `## Tables` 与正文重复。辅助方法 `_table_cell_texts` 从 `cells` 或 HTML `<td>` 抽取压缩文本。效果：`121248` 的 `## Tables` 重复块消失；`121121` 无外部表格不受影响。

- 【新增｜过滤原因标注】`_partition_blocks` 给每个噪音块打 `_filter_reason`（toolbar/symbol/empty/page_number/header/footer），`_generate_filtered_note` 按 `_FILTER_REASON_LABELS` 输出 `> - [原因] 文本`。页号判定优先于页脚，`20/34` 等正确标为 `[页号]`。原文本保留，`_extract_page_number` 不受影响。

- 【P3 第 5 条 已随之缓解】`121248` 过滤区不再混入正文重复句。

验证：`pytest tests/ -q` = 16 passed；参考样例 `121121` 无回归（标题/页号 34/全部正文表格保留）；`121202`、`121248` 离线复核通过。

仍待修：7.2 的 P2（大矩阵识别混乱）、P3（标题前导符号 `-`），以及迭代二记录的并排矩阵表格拆分。

## 8. Git

远端：`https://github.com/popyun/knowledgeImportHub`，分支 `main`，最新已推送提交 `0201c92`（Filter image noise, extract page number, split stacked tables and side notes）。

工作区干净，本地 `main` 与 `origin/main` 一致，无未提交改动。上述区域/表格/噪音过滤/页号/标题的全部修复均已并入 `0201c92`。
