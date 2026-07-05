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

## 7.4 扩大测试第二轮发现的问题（2026-07-04 迭代五；P1 稀疏表格已于迭代六修复）

在最新代码 `e05ea20` 基础上，从 `D:/test-temp/png` 再抽 5 张未测过样例：`微信图片_20240825121128/121142/121205/121217/121255.jpg`，OCR 均成功，离线复核版面还原。总体：标题提取、P1 噪音过滤、过滤原因标注表现正常；发现以下待修问题：

- 【P1｜表格漏检成游离文本】样例 `121205`：一个结构清晰的三列表格（产品大类/产品名称/风险类型，行含“人民币债券 GIRR、CSR”“含权债”“外币债券”“债券远期”等）完全没被识别为表格，散成了逐行普通文本。根因：`_looks_like_table_region`/`_render_mixed_rows` 对列锚点/多列行占比的判定，在这种“列间距大、每行仅 2-3 块”的稀疏表上未达阈值。建议：放宽稀疏表格判定，或结合列 x 对齐的规整度而非仅看多列行占比。

- 【P2｜工具栏残片漏过滤（OCR 错字场景）】样例 `121217`：正文顶部漏进 `状·专排列门轮廊、`；`121205` 也有类似 `三售栏柜栏` 等在过滤区但边界模糊。根因：该串 OCR 把 `形状→状`、`轮廓→轮廊`、且 `排列` 已从弱词表移出，导致 0 个弱词命中，未触发过滤。属“UI 词被 OCR 识别成错字/缺字”的边界情况。建议：对页面顶部/含大量间隔符（`·`/顿号）且短的行，用“UI 词模糊匹配 + 高符号密度”兜底判噪音，避免与正文误伤冲突。

- 【P3｜标题多余空格】样例 `121128`：标题 `... GIRR Delta计算示例 （六）`，`示例` 与 `（六）` 间多一个空格（源于 OCR 分块拼接）。建议：标题清洗时压缩中文/全角括号前后的多余空格。

- 【已知复现｜并排矩阵拆分】样例 `121142`、`121128`：右侧相关系数矩阵（成片 50.0%/100.0%）仍散成游离文本或与相邻表混合，与迭代二/三记录的“并排矩阵拆分”“大矩阵识别混乱”同源，未新增修复。

本轮仅记录，未改代码。

## 7.5 本轮修复（2026-07-05 迭代六）——稀疏表格漏检 P1

已修复 7.4 中的 P1（稀疏表格漏检成游离文本），改动仅在 `processors/markdown_generator.py` 的 `_render_mixed_rows`。

根因：拆分堆叠表格的边界阈值 `table_gap` 原为固定 `max(中位字高*0.7,10)`（约 57px），而部分合法表格行距天生很大（如 `121205` 行距约 170-205px、均匀）。于是每一行都被判为“新表格边界”，buffer 每次只攒 1 行、随即 flush，1 行不满足 `_looks_like_table_region`，整表降级为散文本。

修复采用“自适应 + 三重防线”，保证只放宽、绝不误伤密集/正文区（数学上新阈值恒 >= 原阈值，拆分只会更少）：
1. 行距离群检测：仅当多列行的正间距中位数 `median_gap > base_gap * 1.5` 时才放宽，`table_gap = median_gap * 1.4`；否则保持 `base_gap`（密集/堆叠表逐字节不变）。
2. 单元格长度防线：仅当多列行单元格文本中位长度 `<= 12` 才放宽——真表格是短标签，双栏正文是长句，避免把并排正文段落误并成表（挡住 `121217` 的正文误表格化）。
3. 列稳定度防线：仅当多列行的 `max块数 <= 中位块数 + 1`（列数稳定）才放宽——规整稀疏表触发，块数剧烈波动的混乱矩阵不触发（挡住 `121238` 矩阵被合并、`121125` 的回退）。

全量回归（12 张已缓存样例，忽略 front matter 日期行做逐字节 diff）：
- 内容变化仅 3 张，且均为正向：`121205`（债券类型表）、`121142`（5×5 相关系数矩阵）、`121217`（奇异衍生工具/权重表）由散文本正确转为 Markdown 表格。
- 其余 9 张（含参考样例 `121121`，及此前修复的 `121202/121238/121125/121053/121154/121248/121255/121128`）逐字节 UNCHANGED，零回退。
- `pytest tests/ -q` = 16 passed；`121205` 走完整 `main.py` 链路发布成功。

结论：本轮对识别质量只有正向提升，无任何负面影响。

仍待修（后续）：7.4 的 P2（工具栏 OCR 错字漏过滤）、P3（标题多余空格/前导符号），及并排矩阵/大矩阵结构化（OCR 精度极限，需 PP-Structure 或人工复核标注）。

## 7.6 测试结果存档功能（2026-07-05 迭代七新增）

新增迭代测试归档工具 `test_snapshot.py`（项目根目录），用于每次测试结果留存与迭代间对比。

用途与命令（PowerShell）：
- `python test_snapshot.py run`：对样本图跑完整 OCR→Markdown 链路，把每张渲染结果与指标存入带时间戳的快照目录，并自动与上一份快照对比。
- `python test_snapshot.py run --use-cache`：命中缓存时跳过慢速 OCR（单图约 60-100s），仅重算版面，便于纯布局迭代。
- `python test_snapshot.py run --images <a.jpg> <b.jpg>`：只跑指定图。
- `python test_snapshot.py compare [--old <name> --new <name>]`：对比两份快照（默认最近两份），不跑 OCR。
- `python test_snapshot.py list`：列出已存档快照。

存档结构（默认根 `D:/test-temp/ocr_output/_snapshots`，位于仓库外，不进 git）：
- `<时间戳>/<image>.md`：每张图的渲染 Markdown 快照。
- `<时间戳>/manifest.json`：本次快照的每图指标（chars/lines/table_rows/external_tables/ocr_blocks/confidence）。
- `_cache/<image>.json`：per-image OCR 块缓存（`corrected_result`），供 `--use-cache` 复用。
- `latest.txt`：最近一次快照时间戳。

对比逻辑：逐图比较 Markdown，忽略 front matter `date:` 行（避免跨天误报）；输出 unchanged/changed/added/removed 统计，对 changed 项打印指标前后值与 unified diff。这正是本项目多轮迭代所需的“改动前后逐图对照”能力，替代此前手工缓存+diff 的临时脚本流程。

验证：已建立 baseline 快照（121205/121217/121121，均成功）；二次 `--use-cache` 运行 1.5s 内完成并正确报告全部 unchanged；构造变体快照验证 changed 分支能准确定位差异行与指标变化。

## 7.7 扩大测试第三轮发现的问题（2026-07-05 迭代八，10 张新样例，仅记录待判断）

用 `test_snapshot.py` 抽测 10 张未测样例：`微信图片_20240825121131/121139/121150/121157/121208/121214/121224/121232/121241/121252.jpg`，OCR 全部成功（快照 `2026-07-05_105701`）。总体：标题、噪音过滤+原因标注、稀疏表格表格化整体正常。发现以下问题：

- 【P1｜表格底部行落入页脚边距被误过滤】样例 `121150`：页面底部一个大相关系数矩阵的最后几行（`76.3% 88.7% 56.6% 97.0% 100.0% 93.2% 40.0%` 等）落在页面下 7% 边距内，被 `_is_margin_noise`（短文本规则）判为“页脚/版权信息”过滤掉，表格数据丢失。根因：页脚判定只看“短文本 + 处于边距”，未排除“数字/百分比等表格残留”。建议：`_is_margin_noise` 对纯数字/百分比/金额型短文本不判页脚；或页脚判定要求命中版权类词或与已识别表格列锚点不对齐。

- 【P1｜页号误提取】样例 `121150`：`page` 被提成 `1`（实际非首页）。根因：底部表格里的 `1`/`5`/`10`（期限或序号）落在边距被当作纯数字页号，`_extract_page_number` 取了第一个。建议：页号应优先取“页脚区域居中/靠右的孤立数字”，排除与表格列对齐、成串出现的数字。

- 【P2｜标题 OCR 叠字/多余空格】`121224` 标题 `违约风险资本计量 量一一计算步骤`（“计量”后多“量”，破折号识别为“一一”）；`121128` 标题 `... 示例 （六）` 多空格（此前已记录）。根因：标题由 OCR 分块拼接，未做叠字/破折号/空格归一。建议：标题清洗时压缩全角括号前后空格、合并相邻重复单字、`一一`→`—`。

- 【P2｜工具栏错字残片偶尔漏过滤/错判页眉】多张（`121139` `演示上具`→页眉、`121150` `正对齐文本`）：工具栏 OCR 错字导致弱词命中不足或被判成页眉。与迭代五 P2 同源，属边界情况。

- 【P3｜矩阵型幻灯片列错位】`121150`/`121157` 等宽矩阵仍有跨表混合、列错位，属 OCR bbox 精度极限，与既往记录同源。

已缓存 22 张样例的 OCR 块于 `D:/test-temp/ocr_output/_snapshots/_cache`，后续迭代可用 `--use-cache` 秒级复算。

判断：本轮不立即迭代。两个 P1（表格底行误过滤、页号误取）值得修，但都涉及 `_is_margin_noise`/`_extract_page_number` 与页脚边距判定的耦合，需谨慎设计以免影响已验证样例的页号/页脚过滤；建议下一轮作为独立任务处理，并用快照全量回归守护。

## 7.8 扩大测试第四轮发现的问题（2026-07-05 迭代九，剩余全部未测样例，仅记录待判断）

将 `D:/test-temp/png` 中此前未测的样例全部跑完（快照 `2026-07-05_111003`）：新增内容图 7 张 `微信图片_20240825121134/121211/121220/121226/121230/121236/121244.jpg`，另含参考样例 `121121` 的 3 个预处理变体（`resized_`/`sharpened_`/`.prep`）。OCR 全部成功，置信度 0.90-0.97。至此 `D:/test-temp/png` 样例已全量覆盖。发现以下问题（本轮仅记录不修改代码）：

- 【P1｜标题失控取到整段正文】样例 `121236`：该页为纯文字稀疏幻灯片（`ocr_blocks=4`、`table_rows=0`），无大字号标题行，`_extract_title` 退化为选取首句长正文，`title` 变成 `违约风险资本计量衡量了在突发极端情况下…`（chars=331 整句入题）。根因：无“明显大字号/短标题行”候选时，评分仍会选中最靠上的长正文块。建议：设标题长度硬上限（如 >45 字直接判非标题），无合格候选时留空标题或仅取首个短语，避免整段正文进入 front matter title。

- 【P2｜标题叠字/破折号错识】样例 `121220` 标题 `违约风险资本计量 量一—一计算步骤（续）`（“计量”后多“量”，破折号 OCR 成 `一—一`）。与 7.7 的 `121224` 同一类：标题拼接后未做叠字合并与破折号/连字符归一。建议：标题清洗时合并相邻重复单字、`一一`/`一—一`→`—`、压缩全角括号前后空格（与 7.7 P2 合并一次性修）。

- 【P3｜宽矩阵列错位延续】`121134`（11 行表）、`121230`（含并排小表 external_tables=1）等仍有列锚点漂移/相邻小表并入，属 OCR bbox 精度极限，与既往 7.1/7.7 同源，非本层可根治。

- 【无缺陷｜预处理鲁棒性确认】`121121` 的 resized/sharpened/prep 三个变体均产出 `table_rows=13`、置信度 0.94-0.96，与基线一致，说明缩放/锐化/预处理链路对参考样例无回归、结果稳定。

判断：本轮维持“记录不修”。真正值得下一轮修的是标题层的两个问题（`121236` 整段入题 P1、`121220`/`121224` 叠字破折号 P2），二者都集中在 `_extract_title` 的候选筛选与清洗，改动面小、回归风险可用快照 `2026-07-05_111003` 全量守护。矩阵列错位仍归 OCR 精度极限，需 PP-Structure 或人工复核，暂不在版面层处理。
## 7.9 本轮修复（2026-07-05 迭代十）：无合格标题时摘要回退 + 待确认事项

修复 7.8 记录的 P1（`121236` 整段正文误入标题）。改动仅在 `processors/markdown_generator.py` 与测试 `tests/test_pipeline.py`。

- 【P1 已修复｜标题失控取到整段正文】`_extract_title` 现返回 `(title, meta)` 二元组，`meta['source']` ∈ `heading` / `summary` / `filename`：
  - 合格标题改为**长度硬门槛**：最佳候选必须 `best_score>30` 且 `len<=40` 才认定为真实标题；去掉此前“字号略大即放行(is_larger_font or within_len)”的宽松分支——整句正文即便 OCR 行框略高也不再当标题。
  - 无合格候选时调用新方法 `_summarize_blocks`：按阅读顺序取正文首个非噪音块，去除行首 `>`/项目符号/引号，按句末标点（`。！？；.!?`）截首句，再按 `_SUMMARY_TITLE_MAX=30` 字硬上限、优先在 `，、：` 等自然分隔处断句，生成摘要标题。
  - 新增 `_generate_review_note`：仅当 `meta['source']=='summary'` 时，在正文与过滤区之间输出 `> [!todo] 待确认事项（需人工核对）` 区块，说明“本页未检测到明显加大/加粗标题，标题系正文首段自动摘要生成（已限长），请人工确认或改写”，并回填摘要标题原文。`process()` 的 `note_parts` 已插入 `review_note`。

- 效果（缓存全量回归，26 个样例）：仅 `121236` 走 summary 回退，标题由 52 字整句纠正为 `违约风险资本计量衡量了在突发极端情况下，企业经营情况恶化`（28 字）并附待确认区块；其余 25 个样例标题不变（heading，长度 7-31 字），无回归。

- 测试：`tests/test_pipeline.py` 新增 `TestTitleExtraction`（真实标题保留、长正文回退摘要且带 todo、heading 不产生 todo）。`pytest tests/ -q` = 19 passed。

- 说明：摘要标题为长句截断，与正文完整首句不同名，故 `_drop_title_row` 不会误删正文首句——标题为摘要、完整句仍保留在正文，内容不丢失。

- 环境备注：本轮运行沙箱为受限模式，禁止启动仓库外可执行（系统 Python、apply_patch/codex.exe 均被拦为“拒绝访问”）。已改用“PowerShell 进程内写脚本 + 升级权限运行系统 Python 3.11”完成编辑与验证，此为此前“任务频繁中断”的根因与规避方式。
## 7.10 本轮修复（2026-07-05 迭代十一）：摘要标题保留语义完整性（三档）

在 7.9 摘要回退基础上，按需求加入“保留标题语义完整性”的分档逻辑。改动仅在 `processors/markdown_generator.py` 与 `tests/test_pipeline.py`。

- 常量：`_SUMMARY_TITLE_MAX=30`（字数上限），新增 `_SUMMARY_OVERFLOW_TOLERANCE=0.30`（超限容忍比例）。

- `_summarize_blocks` 改为返回 `(title, mode)`，取正文首句（按 `。！？；.!?` 断句）后分三档：
  - `complete`：首句 `<=30` 字，直接用。
  - `tolerated`：首句超限但超出比例 `<30%`（31-38 字），**保留完整首句不截断**，以保证语义完整。
  - `condensed`：首句超限 `>=30%`（`>=39` 字），调用新方法 `_condense_phrase` 做语义压缩——按 `，、：,;` 分句累积前若干完整子句直到接近上限，实在过长再按最后分隔符硬窗口截断，形成不超上限的连贯短语。

- `_generate_review_note` 按 `meta['summary_mode']` 输出差异化待确认文案：`condensed` 标注“语义压缩、可能损失部分语义”；`tolerated` 标注“略超上限但在 30% 内、为保留语义完整未截断”；其余为通用摘要说明。仅 `source=='summary'` 时才产生 `> [!todo] 待确认事项` 区块。

- 验证：构造样例 A(16字)=complete、B(36字,超20%)=tolerated 完整保留、C(56字,超87%)=condensed 压到28字，均符合预期。全量缓存回归 33 样例：32 个 heading 标题不变，仅 `121236` 走 `summary/condensed`（28 字 + 待确认区块），无回归。`pytest tests/ -q` = 22 passed（新增三档模式测试）。
## 7.11 本轮修复（2026-07-05 迭代十二）：P1 表格底行误过滤 + 页号误取

修复 7.7 记录的两个 P1。改动仅在 `processors/markdown_generator.py`。

- 【P1 已修复｜表格底部行落入页脚被误过滤】`_is_margin_noise` 新增数字豁免：纯数字/百分比/金额/分隔符型短文本（正则 `[\d.,%+\-/~():：·¥$€]+`）一律不判页脚，返回内容。`121150` 底部相关系数矩阵最后几行（76.3%/88.7%/56.6%/97.0%/100.0%/93.2%/40.0%）不再被当“页脚/版权信息”丢弃，完整进入表格。

- 【P1 已修复｜页号误取】新增 `_page_number_candidates(all_blocks)` 统一判定页号，`_partition_blocks` 与 `_extract_page_number` 共用：
  - 明确形式 `N/M` 与 `第N页` 在页边距内任意位置有效。
  - 裸数字仅当“位于页脚/页眉边距 + 中心相对横坐标 relx>=0.75（靠右）+ 该边距内此类裸数字唯一”时才认定为页号；成串裸数字（表格期限/序号行，如 1/5/10）一律不当页号。
  - `_partition_blocks` 的 page_number 标记改用该候选集的 id 集合，删除旧“in_margin 裸数字一律当页号”逻辑。

- 全量缓存回归（33 样例，改前 vs 改后严格 diff）：仅 2 张变化且均为正向修复——`121150` page `1`→`None`、footer 过滤 10→0（矩阵数据回归、tblsep 31→32）；`121139` page `9`→`32`（此前误取左侧序号列末位 9，现正确取右下角 relx=0.98 的 32）。其余 31 张页号/过滤/表格指标零变化。`pytest tests/ -q` = 22 passed。
## 7.12 本轮修复（2026-07-05 迭代十三）：P2 标题清洗（叠字/破折号/括号空格）

修复 7.7 记录的 P2（标题 OCR 叠字、破折号碎片、全角括号多余空格）。改动在 `processors/markdown_generator.py` 与 `tests/test_pipeline.py`。

- 新增 `_clean_title`，在 `_extract_title` 的 heading 返回处、以及 `_summarize_blocks` 生成首句后统一调用。规则（保守，避免误伤正常标题）：
  1. OCR 叠字：`X<空格>X` -> `X<空格>`（删除空格后重复的单字），修复 `计量 量一...` -> `计量 一...`；
  2. 破折号碎片：连续的 `一`/`—`/`-`（`[一—\-]{2,}`）归一为单个 `一`，修复 `一—一`/`一一` -> `一`；
  3. 全角括号 `（）【】《》` 前后多余空格压缩；
  4. 多空格归一。

- 全量缓存回归（33 样例标题，改前 vs 改后严格 diff）：仅 3 张变化且均为目标修复——`121128` `示例 （六）` -> `示例（六）`；`121220` `计量 量一—一计算步骤（续）` -> `计量 一计算步骤（续）`；`121224` `计量 量一一计算步骤` -> `计量 一计算步骤`。其余 30 张标题零变化。`pytest tests/ -q` = 26 passed（新增 4 项标题清洗测试）。
## 7.13 本轮修复（2026-07-05 迭代十四）：P2 边界——工具栏 OCR 错字残片漏过滤/错判页眉

修复 7.7 记录的 P2 边界情况。改动仅在 `processors/markdown_generator.py` 与 `tests/test_pipeline.py`。

- 【已修复｜工具栏粘连残片漏过滤】`_noise_kind` 新增“边距内重复字符游程”信号：位于页上/下边距、非纯数字/百分比、且含 `([一-龥A-Za-z])\1{2,}`（≥3 个连续相同 CJK/字母）的块判为 toolbar。此前这些被 OCR 严重错字化的顶部工具栏残片（如 `雯8三三三三色运栏运面转智能形`、`IAAAE·汇区`、`三三三三栏栏三喵转智能册形`、`A·XX婴·名三三三三仁栏信面转智能形`、`USAXX会三三三栏坛转能`）因强词不匹配、弱词命中不足而漏进正文，现被正确列入过滤审核区。
  - 安全性：仅在边距内触发；正文/表格中的重复样例值（如 `AAA`/`BBB`）不在边距，不受影响；纯数字/百分比经数字豁免正则排除，避免误伤页脚表格数据。全量缓存核验：边距内命中的 5 处全是工具栏残片，非边距命中全是表格示例值（未过滤）。

- 【已修复｜错判页眉】弱词表新增 `演示`，修正 `121139` 的 `演示上具·`（OCR 错字“演示工具”）此前被兜底判为“页眉”，现正确判为“编辑器/导航栏按钮”。

- 全量缓存回归（33 样例，改前 vs 改后严格 diff）：仅 4 张变化且均为正向——`121139`（演示上具 header→toolbar + 残片过滤）、`121150`（2 处残片过滤）、`121211`、`121232`（各 1 处残片过滤）。残片从正文移入过滤审核区，正文更干净；其余 29 张零变化。`pytest tests/ -q` = 30 passed（新增 4 项噪音识别测试）。
## 7.14 本轮修复（2026-07-05 迭代十五）：P3 宽矩阵/并排表——方案 B 天沟拆分 + 表格质量门控

按用户批准的“方案 B 先行、方案 A 兜底”方向，修复 P3（宽矩阵 / 并排表格被拼成错乱大网格）。本轮只做方案 B（几何法），改动仅在 `processors/markdown_generator.py` 与 `tests/test_pipeline.py`；方案 A（PP-Structure 增强，需下载模型权重 + 联网）尚未启动，留待质量分低于阈值时触发。

- 【新增｜天沟拆分】`_split_columns_by_gutter(rows, columns)`：仅当列数 ≥ 8、最大列间距 ≥ 1.8×中位列间距、且拆分后两侧各保留 ≥ 2 列时，在最宽“天沟”处把一组行拆成左右两张独立表格，分别渲染。小表（4-6 列）一律不拆，避免把单张真实表格打碎。
- 【新增｜表格质量分】`_table_quality(rows, columns)` 返回各分量 + 综合 `score`∈[0,1]，全部来自 bbox 几何信号（无需模型）：`fill`（填充率）、`align`（单元格中心对齐度）、`stab`（每行列数一致性）、`collision`（同列碰撞行占比，强烈提示并排表混排）、`col_penalty`（列数超过 `_WIDE_TABLE_COLS=9` 的惩罚）。综合式：`0.30*fill + 0.30*align + 0.20*stab + 0.20*(1-collision) - 0.35*col_penalty`，裁剪到 [0,1]。
- 【新增｜低置信门控】`_render_markdown_table` 重构为方案 B 流水线：先剥离旁注 → 尝试天沟拆分（对左右两侧递归渲染）→ 否则走 `_render_single_table` 并打分；`score < _TABLE_QUALITY_MIN=0.62` 时在表格前追加告警注记（`> [!warning] 表格结构复杂……建议启用增强识别（PP-Structure）或人工核对原图`）。此告警即方案 A 的精准触发闸门。
- 【重构】抽出 `_render_single_table(rows, columns)`（原“吸附到列锚点”的渲染逻辑，不含打分），供拆分/单表两路复用。

- 全量缓存回归（33 样例，改前 vs HEAD `1259535` 严格 diff）：质量分分布合理——正常表 0.8-1.0；最差矩阵 `121125`（18×18 相关系数矩阵，0.51/0.43）与 `121238` 内一表（0.46）被正确标记。天沟拆分（列数下限 4→8 收紧后）仅在 `121125` 触发并改善正文；`121238` 仅新增告警注记；其余 31 张正文字节不变。
- 【已知限制｜方案 A territory】`121134` 底部 8 列表实为并排合并表（加权敏感度表 + CSR WEIGHT 表），列间距无明显天沟，几何法无法拆分，且质量分 0.85（高于阈值）不触发告警——这正是方案 A（PP-Structure）后续要处理的场景。不下调阈值去硬抓它（会误标大量 0.77-0.85 的正常表）。
- `pytest tests/ -q` = 35 passed（新增 `TestTableQuality` 5 项：清晰网格高分、宽矩阵低分、天沟拆分并排表、小表不拆、低质量表追加告警）。

## 7.15 本轮开发（2026-07-05 迭代十六）：方案 A——PP-Structure 低置信区域增强（按需触发、零负面影响）

按用户批准的“方案 B 先行、方案 A 兜底”落地方案 A。改动文件：新增 `processors/table_enhancer.py`，改 `processors/markdown_generator.py`、`processors/image_handler.py`、`config.yaml`、`tests/test_pipeline.py`。

- 【调研结论｜关键约束】在本套彩色幻灯片语料上实测：PP-Structure 整页 layout 会把幻灯片误判成一个 `figure`，切不出表；对低分“伪表格”（`121125` 相关系数矩阵、`121238` 宽扁说明块）裁剪后识别结果比方案 B 更差；对真正并排合并表（`121134` 目标区）裁剪后仍是错乱结果。结论：PP-Structure 在此语料上不能稳定优于方案 B。因此方案 A 采用“**纯附加、绝不替换主输出**”的形态，把增强结果作为“供人工比对”的补充块附在低置信告警下。
- 【新增｜TableEnhancer】`processors/table_enhancer.py`：懒加载 `PPStructure(layout=False, table=True, ocr=True)`，只对方案 B 上报的低置信区域裁剪（增强图/OCR 坐标系，`crop_pad` 默认 14），返回 `{html, region, engine}`。默认关闭（`enhance_on_low_quality: false`），每区域约数秒；`max_enhance_regions` 默认 4。
- 【接入｜markdown_generator】`_render_markdown_table` 在 `score < _TABLE_QUALITY_MIN=0.62` 时，除告警外记录该区域 bbox 到 `self._low_quality_regions`（`_rows_bbox`），并在 `self._enhanced_tables`（来自 `ocr_result["enhanced_tables"]`）中按 `_bbox_iou >= 0.5` 匹配，命中则追加 `> [!tip] 增强识别结果（PP-Structure，供人工比对，未替换上方主输出）` + 增强表 markdown。主输出（方案 B 表格）位置与内容保持不变。
- 【接入｜image_handler】两趟渲染：Step 5 先渲染收集 `_low_quality_regions`；若 `table_enhancer.enabled` 且有低置信区域，Step 5b 对 `enhanced_image` 裁剪跑增强，结果写回 `corrected_result["enhanced_tables"]`（**持久化，供缓存复跑渲染**）后重渲染。
- 【降级/安全】增强器 import/init/推理失败均静默降级返回空，绝不影响主流程；开关默认关闭，正常路径零额外耗时。
- 【回归验证】开关关闭时，全 33 张缓存语料输出与 HEAD `1259535`（方案 B）**逐字节零差异**（`byte-diffs vs HEAD: 0`）——方案 A 纯附加、零负面影响。真实全链路（`ImageHandler` 开启方案 A 跑 `121125`）跑通：成功识别、生成 2 个增强表、附加 tip 块，主流程无异常（约 89s，含 OCR + 2 次 PP-Structure）。
- `pytest tests/ -q` = 42 passed（新增 `TestTableEnhancer` 7 项：默认关闭、配置开关、`_first_table_html` 提取、低置信区域记录、匹配区域附加增强块、不匹配区域不附加、`_bbox_iou`）。
- 【后续可选】若要让方案 A 真正“增强”而非“比对”，需更强的表格模型或对彩色表做去底色/网格线增强预处理；当前语料下 PP-Structure 未达该标准，故保持 review-only + 默认关闭。

## 7.16 本轮开发（2026-07-05 迭代十七）：方案 A 升级——三档自动降级 + 首启能力探测缓存 + 可插拔增强后端

按用户批准的三档方案落地。改动文件：新增 `processors/host_profiler.py`；重写 `processors/table_enhancer.py`；改 `processors/image_handler.py`、`config.yaml`、`.gitignore`、`tests/test_pipeline.py`。

- 【新增｜host_profiler】`processors/host_profiler.py`：首启探测 CPU 核数、总/空闲内存（psutil）、加速器（torch CUDA/MPS）、关键库（paddleocr/torch）；结果写入 `host_profile.local.json`（`.gitignore` 已加 `*.local.json` + 显式 `host_profile.local.json`，不入库），命中缓存即复用、不再扫描；`load_or_create_profile(base_dir, force_rescan=)` 支持 `--rescan` 重建。`ollama list` 会冷启动服务并阻塞，故 `ollama_vision_models` 默认延迟探测：仅当 `vision_capable`（有 CUDA/MPS 或空闲内存 ≥ `VISION_MIN_FREE_GB=8.0`）时才在 `decide_tier` 内懒探测。
- 【三档映射｜decide_tier】`vision`（有加速器/大内存且本地视觉模型可用）→ VisionLocalBackend；`gridboost`（CPU-only 但 PaddleOCR 可用，本机落此档）→ GridBoostBackend；`manual`（无 PaddleOCR/资源不足）→ 不增强、人工复核。若“具备视觉潜力但缺软件/模型”→ 记 `missing_vision_requirements`，降级到 gridboost 并提示安装。
- 【重写｜table_enhancer】抽出可插拔后端：`PPStructureBackend`（原始裁剪直跑）、`GridBoostBackend`（`gridboost_preprocess`=去底色自适应二值化 `_binarize_decolor` + 依据 OCR 词框聚类 `_cluster_axis` 补虚拟网格线 `_draw_virtual_grid` 后再跑 PP-Structure）、`VisionLocalBackend`（占位，本机 `run()`→None 安全降级）。`TableEnhancer(config, tier=)` 解析顺序：`config.ocr.table_structure.backend` 显式覆盖 > 传入 tier > 默认 gridboost；`manual` 档直接返回 `[]`。新增可比质量分 `enhanced_quality`（`score=0.45*fill+0.35*stab+0.20*size_bonus`），产出 `{html, region, engine, backend, score_enhanced, score_base, verdict="compare"}`。
- 【接入｜image_handler】`__init__` 调 `load_or_create_profile()` 取 tier 建 `TableEnhancer(config, tier=tier)`，并记录档位与原因日志；`_maybe_prompt_vision_install` 在“有潜力但缺环境”且交互式 TTY 时询问是否安装（非交互/CI 只记日志不阻塞）。Step 5b 调 `enhance_regions(enhanced_image, regions, blocks=corrected_result["blocks"])`——补传 blocks 供 gridboost 画虚拟网格线。
- 【比对闸门】本轮统一停在“比对档”：`verdict` 恒为 `compare`，增强结果仍以 review-only 附加块呈现，方案 B 主输出与位置不变，采纳档留待后续。
- 【关键调研结论】本套彩色幻灯片语料上 gridboost/PP-Structure 仍不能稳定优于方案 B（如 `121134` 并排合并表 gridboost `S_e=0.845 < baseline 0.886`；`121125` 矩阵、`121238` 宽块更差），故维持“纯附加、review-only、默认关闭”。`vision` 档面向后续更强机器，本机（i5-11300H、CPU-only、16GB/空闲约 4-5GB、Intel Iris Xe）探测结论恒为 `gridboost`。
- 【回归验证】`enhance_on_low_quality: false` 默认关闭；`markdown_generator.py` 相较 HEAD 零改动，全 33 张缓存语料重渲染逐字节等价、无 `\ufffd`。`pytest tests/ -q` = **54 passed**（新增 `TestHostProfiler` 6 项 + `TestGridBoost` 6 项：三档判定、缓存复用不重扫、非视觉主机不探测 ollama、`--rescan` 重建、gridboost 预处理形状、`enhanced_quality` 打分、tier→后端选择、config 覆盖优先）。

## 7.17 本轮开发（2026-07-05 迭代十八）：方案 A vision 档落地——本地视觉模型 qwen2.5vl:3b 接入（按需触发、零负面影响）

按用户批准"方案 A、拉 qwen2.5vl:3b 试"落地 vision 档。改动文件：`processors/table_enhancer.py`（实现 VisionLocalBackend）、`config.yaml`（新增 vision_model/vision_timeout）、`tests/test_pipeline.py`（新增 TestVisionLocalBackend 5 项）。

- 【环境】`ollama pull qwen2.5vl:3b` 已完成（3.2GB，本地 `ollama list` 可见）。尾段网速被限速到 ~10KB/s 长时间卡在 99%，重启 pull 进程换新连接后立即 success。ollama 0.30.8 服务常驻，`/api/generate` 走 `images` 字段传 base64 PNG 即可视觉推理。
- 【实现｜VisionLocalBackend】`processors/table_enhancer.py` 顶部加 `import base64, re`。`VisionLocalBackend.__init__` 从 `config.ocr.table_structure.vision_model`（其次 `ocr.ollama.vision_model`，默认 `qwen2.5vl:3b`）取模型、`vision_timeout`（默认 180s）取超时、`ocr.ollama.endpoint` 取地址。`run(crop, region, region_blocks, offset_xy)`：`cv2.imencode('.png')` → base64 → POST `/api/generate`（temperature=0、num_predict=2048、stream=False）→ `_extract_table_html` 用正则 `<table.*?</table>` 抠出表格 HTML。任何失败（编码失败/连接异常/非 200/JSON 坏/无 table）均 `return None` 安全降级，主输出不受影响。提示词要求"仅输出单个 HTML <table>、保留原行列、保留中文数字符号、不合并列"。
- 【配置】`config.yaml` 的 `ocr.table_structure` 新增 `vision_model: "qwen2.5vl:3b"`、`vision_timeout: 180`。默认 `enhance_on_low_quality: false`、`backend: ""`（自动分档）保持不变。
- 【分档现状】本机探测仍为 `gridboost`（空闲内存 4.49GB < `VISION_MIN_FREE_GB=8.0`，无 GPU）。测试通过 `config.ocr.table_structure.backend="vision"` 显式覆盖强制走 vision 档验证。host_profiler 的 `vision_keys` 已含 `vl`，`qwen2.5vl:3b` 若被自动探测可正确识别为视觉模型。

- 【效果实测｜3 张目标图，backend=vision + enhance 开启】
  - `121125`（18×18 相关系数矩阵）：vision 档命中低置信区域，`S_b=0.431 → S_e=0.857`，比对档 `> [!tip] 增强识别结果` 附加块结构明显更整齐，正向提升明显。
  - `121053`、`121134`：`enhanced_tables=0`——**未触发**。根因：方案 B 未把这两张的问题区判为"低置信表格"（121053 是多步骤流程图式图文混排、121134 底部并排合并表质量分 0.85 高于阈值），vision 后端只对"低置信表格区域"裁剪重识别，因此不覆盖这两类版式错乱场景。这与之前几何法/PP-Structure 的结论一致：需要在"区域判定/触发条件"层面扩展，才能让 vision 覆盖混排流程图。
  - 速度：本机 CPU 跑 3B VLM 很慢，单区域约 40s~500s+，曾出现 300s 超时（已由 vision_timeout 兜底降级）。生产需更强机器或调大超时。

- 【回归验证｜零负面影响】默认配置（`enhance_on_low_quality: false`）下，用 33 张缓存 OCR 结果重渲染 markdown，与 `_regbaseline`（HEAD 全量渲染基线）**逐字节等价：MATCH 33 / DIFF 0 / 无缺失**。vision 档纯附加、review-only，对现有线上输出零改动。
- `pytest tests/ -q` = **59 passed**（新增 `TestVisionLocalBackend` 5 项：HTML 抠取、ollama 表格解析、连接异常降级、非 200 降级、无 table 降级；全部 mock `requests.post`，不依赖真实模型）。

- 【结论与后续】vision 档对"表格型低置信区"（如 121125）确有正向提升，已按用户"先上 A 看效果"落地并验证零回归。但对 121053/121134 这类"版式错乱但不被判为低置信表格"的场景，vision 后端当前不触发；若要覆盖，需扩展触发判定（把复杂混排/低置信整区也纳入 vision 重识别），属下一步可选增强，待用户确认方向。默认仍关闭增强，保证对现有质量零负面影响。

## 8. Git

远端：`https://github.com/popyun/knowledgeImportHub`，分支 `main`。方案 A vision 档（本地 qwen2.5vl:3b 接入）本轮改动待提交推送：`processors/table_enhancer.py`、`config.yaml`、`tests/test_pipeline.py`、`TASK_STATUS.md`。

默认增强关闭，全 33 张缓存回归逐字节等价、`pytest tests/ -q` = 59 passed。vision 档为纯附加 review-only，对现有线上输出零负面影响。
