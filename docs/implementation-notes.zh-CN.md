<p align="center">
  <a href="../README.md"><img alt="返回首页" src="https://img.shields.io/badge/%E8%BF%94%E5%9B%9E%E9%A6%96%E9%A1%B5-README-2b6cb0"></a>
  <img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D-blue">
  <a href="implementation-notes.md"><img alt="English" src="https://img.shields.io/badge/English-Read-2f855a"></a>
</p>

# 实现说明

## Source 边界

Scriptorium 现在把输入视为 document source，而不是默认等同于 PDF。`render_source()` 会分发到 PDF renderer 或 image renderer：

- 图片 source（`PNG`、`JPEG`、`TIFF`、`WebP`、`BMP`）会被渲染成一页 `RenderedDocument`，并标记 `source_type = "image"`。
- PDF source 继续走原生提取路径：PyMuPDF 文本/图像/drawing、可选 image-only OCR fallback，以及可选结构 JSON 融合。
- 图片坐标用 `--image-dpi` 把源像素映射到 PDF point 坐标。原始像素成为页面 visual layer，OCR/结构 JSON 负责贡献可编辑文本锚点和 reading-stream 证据。
- `DocumentIR.source` 是 source-neutral 主字段。`source_path` 为旧调用方保留镜像；旧的 `source_pdf` 字段仅作为 PDF source、既有 JSON、报告和 XML 的兼容别名。image-source IR 不再自动填充 `source_pdf`，除非旧 payload 显式提供。
- 原生 PDF 提取会显式拒绝图片 source。图片语义层应来自 OCR JSON 或 Paddle/PP-Structure/Docling/ROOR 风格结构 JSON，而不是从伪 PDF wrapper 里猜。

示例：

```bash
scriptorium convert page.png \
  --input-kind image \
  --image-dpi 96 \
  --structure-json page.structure.json \
  --out-dir outputs/page-image
```

## OCR 后端边界

核心 pipeline 只消费归一化 JSON，并把它转换为 `DocumentIR`。这是刻意设计的边界：

- PaddleOCR-VL 1.6 的官方示例使用 `from paddleocr import PaddleOCRVL`、创建 `PaddleOCRVL(pipeline_version="v1.6")`、运行 `pipeline.predict(...)`，再用 `save_to_json(...)` 保存结果。
- PP-StructureV3 文档将其定位为文档解析结构化 JSON/Markdown 输出，坐标和版面结构比纯 VLM 结果更细。
- 因此 Scriptorium 把 Paddle、Docling、PP-Structure 输出当作 OCR/结构适配器输入；渲染、几何、IR、HTML 导出、编辑、翻译和质量比较不依赖具体模型运行时。

当前状态：

- `--ocr-json` 是稳定测试入口，适合转换质量工作。
- `PaddleOcrAdapter` 隔离在 `scriptorium.ocr`，并且延迟导入 `paddleocr`。
- `--structure-json` 是真实模型输出的轻量桥接入口，支持 PaddleOCR-VL / PP-StructureV3 风格 JSON、DoclingDocument JSON，以及 `document` / `ro_linkings` 这类关系式结构 payload。
- 对图片 source，如果没有单独提供 `--ocr-json`，`--structure-json` 也可以先生成初始文本锚点。常见 `parsing_res_list` / `block_bbox` / `block_content`、PP 的 `overall_ocr_res` 等 OCR 字典，以及 ROOR 风格 `document` segment 的 `box` / `text`，都会被归一成 `native-ocr` 文本节点，再由结构 evidence 反向融合标签、顺序和置信度。适配器会递归常见 `res`、`result`、`data`、`pages`、`page_results`、`raw_results`、`results` 包装，并保留 page index fallback。
- `DocumentIR.metadata.semantic_layer` 会记录当前语义层驱动。图片 case 会报告 `structure-json`、`ocr-json`、`ocr-fallback` 或 `visual-only`；原生 PDF case 报告 `native-pdf`，结构 JSON 默认作为增强证据。
- 原生 PDF 提取提供 `image-only` OCR fallback：当页面没有原生文字且图像覆盖面积很高时，生成透明的 `native-ocr` 可编辑锚点，同时保留原始图像元素。
- `structure_evidence.py` 能解析嵌套 `res`、`raw_results`、`pages`、`parsing_res_list`、`document`、`layout_det_res.boxes` 形状，也能解析 Docling `body.children`、`furniture.children`、`prov` bbox/page 证据和上下坐标原点差异，并把 ROOR 风格 `ro_linkings` 当作 successor edges。真实 PP-StructureV3 JSON 已在论文和门户截图上走通该桥接；PaddleOCR-VL、Docling、模型直接提供的 relation/stream JSON 仍是独立验证轨道。

## 标注层

Structured HTML 不依赖手写样式把 demo 调漂亮；转换结果必须把识别证据显式标进 IR 和 DOM。

提取阶段写入原始证据：

- 原生 PDF 文本行：字体、字号、颜色、weight、bbox、source metadata 和可编辑文本。
- 原生 PDF span：记录在 `element.metadata.text_runs`，包含 run text、bbox、font、weight、style、color、script 和 source coordinates。
- 原生图像块：写成局部 `image` 元素，带 `source_crop`、bbox、尺寸和 `native-image` source。
- 原生 drawing：写成 shape 元素；简单线段保存 `line_points_pdf`，支持的复杂 path 保存 `svg_path_pdf`。
- 密集矢量区域：可触发局部 raster fallback，生成带 bbox/source metadata 的 `native-raster-region` 图像元素。
- OCR fallback：保留 bbox、类型、置信度、text runs、style hints、`native-ocr` source、OCR language 和 OCR DPI。

`annotate_document()` 负责给元素打上可解释标记：

- `role`: `heading`、`paragraph`、`table-cell-text`、`figure-shape`、`separator-shape` 等。
- `source_kind`: `native-pdf`、`native-drawing`、`json-fallback` 等。
- `style_id`: 稳定样式桶，记录在 `DocumentIR.metadata.styles`。
- `layout_group_id` 和 `layout_group_kind`: 例如 `table-001`、`figure-001`、`separator-001`。
- `semantic_order`、`visual_order`、`column_index`、`column_count`、`column_span`、`flow_segment_index`。
- `reading_order_strategy`、`reading_order_region_path`、`reading_order_scope`、`reading_order_artifact_type`、`reading_order_sidebar_type`。
- `reading_order_stream_id`、`reading_order_stream_type`、`reading_order_stream_index`，用于标记 page-local reading streams，例如 `body-main`、`body-segment-002`、`footnote`、`sidebar-right`、`page-artifact-header`、`caption-figure-*`、`table-island-001`、`grid-island-001`。
- 正文 segment stream 只在存在 full-width flow break 或多个 recursive XY-Cut 区域等结构断点证据时启用；第一条连续正文链仍保留 `body-main`。
- `reading_order_caption_target_id`、`reading_order_caption_target_kind`、`reading_order_caption_target_position`、`reading_order_caption_target_confidence`，以及 target bbox/source metadata，用于记录 figure/table caption 与附近对象的关系。
- `reading_order_confidence`、`reading_order_evidence`、`reading_order_evidence_summary`，用于解释阅读顺序依据。
- `editable`、`edit_target`、`bbox_pdf`、`bbox_px`。
- Paddle/PP-Structure/Docling 的外部结构标签会映射到 `formula`、`running-header`、`footer`、`caption`、`table-cell-text` 等角色。

HTML exporter 会把这些标记落成 DOM 属性，例如：

```html
<div
  data-scriptorium-role="table-cell-text"
  data-scriptorium-source="native-pdf"
  data-scriptorium-style-id="style-004"
  data-scriptorium-layout-group="table-001"
  data-scriptorium-layout-kind="table"
  data-scriptorium-semantic-order="12"
  data-scriptorium-reading-order-strategy="recursive-xy-cut-v1"
  data-scriptorium-reading-order-stream-id="body-main"
  data-scriptorium-reading-order-stream-type="body"
  data-scriptorium-reading-order-stream-index="12"
  data-scriptorium-reading-order-confidence="0.83"
  data-scriptorium-reading-order-evidence="recursive-xy-cut,horizontal-whitespace-cut"
  data-scriptorium-edit-target="edited_text"
  data-scriptorium-translation-target="translated_text"
  data-scriptorium-translation-stream-id="body-main"
  data-scriptorium-translation-stream-type="body"
  data-bbox-pdf="76.99,212.49,117.83,224.22"
  contenteditable="true"
>
  PDF text
</div>
```

`structured` 模式不会放整页背景图。输出由可编辑文本节点、结构 shape 节点、原生 image 节点和局部 raster fallback 节点组成，每个节点都能追溯到 IR 中的识别证据。

翻译工具应使用 `data-scriptorium-translation-stream-id` / `data-scriptorium-translation-stream-type` 按正文、边栏、脚注、表格岛和卡片网格岛分批处理，再把替换文本写入 `translated_text`。这样复杂页面可以先通过 HTML 保留坐标和结构，再走同一套渲染/打印路径回 PDF。

对于 image-only 页面，原生图像仍然是可见层；`native-ocr` 节点默认透明，只在 hover/focus 时可见，避免重复显示文本，同时保留编辑锚点。

## 原生视觉保真层

复杂科学论文和网页 PDF 的视觉误差通常不来自阅读顺序，而来自字体度量、嵌入图像、矢量绘制、透明度和浏览器重绘差异。当前原生路径覆盖这些能力：

- `native-image`: PyMuPDF `get_text("dict")` 图像块会保存成本地图像资产，并作为定位 image 元素导出。
- `native-ocr`: 无原生文本且图像覆盖率达到阈值时，PyMuPDF/Tesseract 生成透明可编辑锚点。
- 字体族归一化会把 `NimbusRomNo9L`、`CMR`、`CMMI`、`CMSY`、`SFTT`、`LiberationSans` 等 PDF 字体映射到更接近的浏览器字体。
- `font_profile` 支持 `browser-default` 稳定基线和 `local-urw` 本地字体实验。
- `--font-profile auto`、`--font-size-scale auto`、`--text-fit auto` 会在 benchmark 中运行候选并选择视觉相似度最高的路径。
- `text_fit = svg` 使用 PDF run bbox、baseline origin、SVG `textLength` / `lengthAdjust="spacingAndGlyphs"` 拟合行宽，同时保留透明编辑代理。
- `fidelity` HTML 模式用 SVG 或 raster 页面背景保留源视觉，同时叠加透明可编辑坐标节点。打印时未编辑节点隐藏；编辑或翻译节点作为局部白底 replacement overlay 打印。
- `fidelity` 的 edited/translated replacement 使用 `fidelity-replacement-fit-v1`：导出器会扩展局部白底 mask，记录 `data-scriptorium-replacement-mask-padding`，用 CSS padding 把替换文本对齐回原 bbox，对长文本写入 `data-scriptorium-replacement-fit-scale`，并在仍然溢出或与相邻元素重叠时写入 `data-scriptorium-replacement-overflow` / `data-scriptorium-replacement-conflict` 和冲突元素 id。
- `--translation-stress pseudo-expand` 会在 benchmark 内写入确定性伪译文到 `translated_text`，用于压测翻译回渲染的 mask、fit-scale、overflow 和邻近冲突；它不是翻译质量评测，也不需要外部翻译服务。
- `--html-mode auto --fidelity-background auto` 会比较 structured redraw、SVG fidelity 和 raster fidelity，并保留更高分候选。
- 打印后的 PDF page box 会归一到源 PDF 尺寸，避免 Chromium A4 1px 量化误差污染视觉指标。
- 当源页数已知时，HTML 打印导出还会删除浏览器追加的尾部空白伪页；删除条件限定为超出源页数、无文字/图片/批注，且只包含空白或白色 drawing。这样可以避免翻译压力测试被 Chromium 分页伪影主导，同时保留真正有内容的翻译溢出页。
- 某些本地 Chromium 环境会在本地图片资源尚未就绪时，让 Playwright 返回一个表面成功但视觉全空的 PDF。`print_html_to_pdf()` 现在会识别全空 Playwright 输出并改走 Chromium CLI；CLI 在打印前推进 3 秒 virtual time，使冷缓存下的本地资源也能稳定完成渲染。
- 简单 drawing 输出为 SVG line/path；密集矢量图可以局部 raster fallback，但仍然保留 bbox/source metadata。

这是一个明确的保真度和可编辑性权衡：普通文本、表格、分隔线、简单 drawing 和支持的 SVG path 尽量保持结构化；无法可靠结构化的复杂图形先以局部 raster 节点保真。

## 外部结构证据融合

PaddleOCR-VL、PP-StructureV3 和 Docling 应该作为可选证据提供者，而不是替换原生 PDF 提取。数字 PDF 的 native extraction 通常更适合保留字体、样式和 bbox；模型输出更适合补充 OCR、layout label、table/formula/chart region 和阅读顺序预测。

`src/scriptorium/structure_evidence.py` 当前提供：

- `normalize_structure_evidence(payload, document)`: 接受 Paddle 风格 JSON，包括带 `block_bbox`、`block_label`、`block_content`、`block_order` 的 `parsing_res_list`。
- 显式 block order 仍是最强的 block 顺序证据。`parsing_res_list`、`blocks` 或 `elements` 没有 `block_order` 时，只有正文流、表格和明确 card/grid 标签才可把列表位置记录为较弱的 `implicit-list` 顺序；嵌套的 `children`、`sub_blocks`、`sub_regions`、`items`、`cells` 等子列表会按 depth-first 顺序遍历。图片、图表、页眉页脚、脚注和边栏只保留 region/role 证据，不能因序列化位置把图内说明或页边元素拖入正文流。纯 `layout_det_res.boxes` 检测框不会获得隐式顺序。
- PP-StructureV3 的 `table_res_list` cell 会从 `cell_box_list` 或 `table_ocr_pred.rec_boxes` / `rec_polys` 加 `rec_texts` / `rec_scores` 归一化。能匹配到父 table block 时，cell 会继承父 block 顺序，并用 row-major `external_structure_order_subindex` 表示局部单元格顺序；没有父 block 时，只作为较弱的 `implicit-table-cell` 顺序证据。对图片 source，同一个 payload 也可以先生成 `native-ocr` table-cell anchors，再反向融合结构证据。
- PP-StructureV3 的通用 OCR 结果也会直接解析：`overall_ocr_res` 和 `text_paragraphs_ocr_res` 可以从 `rec_boxes` / `rec_polys` 加 `rec_texts` / `rec_scores` 生成文本锚点，`formula_res_list` 可以从 `rec_formula` 生成 `formula` 锚点，`seal_res_list` 可以生成 seal-text 锚点。这些结果默认只作为无序区域证据，除非同时存在更强的 block order、关系边或 reading stream。
- 同一文本且 bbox 高度重合的 OCR 近重复项，会在生成 IR 或结构区域前去重。`formula`、`seal`、`table_cell` 等更具体 label 优先于普通文本；bbox 等价时，`text_paragraphs_ocr_res` 优先于更泛的 `overall_ocr_res`。
- 接受 DoclingDocument JSON，遍历 `body.children`，解析 `texts`、`tables`、`pictures`、`key_value_items`、`groups` 引用，并把 `prov` 转成页面局部结构区域。Docling `furniture.children` 也会作为非正文结构证据解析，用于 page header/footer 等 artifact 的角色和 stream 标注，但不生成正文 block-order 证据。
- Docling body-tree 生成 relation 和 stream 时，只接受同一 container 内连续、同页的文本 sibling。group、table、picture、未解析 ref、跨页，以及 root body 的几何断点都会结束局部 run。这样保留有价值的局部 successor 证据，而不会把 Docling 序列化的 body 顺序当作整页 permutation。
- 当通用的 root-body Docling run 遇到更强的 native table、grid、caption、sidebar、footnote 或 page-artifact stream 时，还会再次切断。受保护的 island 保留 native stream；实际应用的段使用独立 `native-segment-*` provenance，跳过的边界也会写入 review 记录，避免门户卡片 grid 被悄悄折叠进正文翻译流。
- Docling table 的 `data.table_cells` / `grid` 条目如果带 bbox 和 text，会提升为更具体的 `table_cell` 区域。它们在需要时继承父 table 的 page provenance，保留行、列、span、header 元数据，并根据 row-major 单元格坐标写入 `external_structure_order_subindex`，让只有一个父 block order 的表格岛也能在局部按行列重排。
- 支持 PDF-point bbox、pixel bbox、top-left 和 bottom-left 坐标原点。
- `page_results[*].data`、`res`、`result`、`raw_results`、`pages` 等嵌套模型包装会为 region、relation 和 stream 证据继承最近的显式父级页码。原始 PP-StructureV3 `save_to_json` 若把 `page_index` 留空，Scriptorium 还会识别 `input_path` 中的渲染页名，例如 `page_0005.png` 会恢复为源页索引 4。这样长文档抽样页会继续按源页码对齐，不会误退回包装列表位置，也不需要手工写 JSON wrapper。
- `apply_structure_evidence(document, payload)`: 通过 bbox coverage 和文本相似度把模型区域对齐到原生元素。
- 当父区域和更具体的子区域同样覆盖某个文本时，会优先选择面积更小的子区域。这样嵌套 card/product/tile 结构可以驱动局部 reading stream，而不会被父级 grid bbox 吞掉。
- 匹配元素会获得 `structure_evidence`、`external_structure_label`、`external_structure_order`、`external_structure_order_source` 和可选的 `external_structure_order_subindex` 元数据。PP-Structure 和 Docling 表格单元格还会暴露 `external_structure_table_cell_*` 元数据，并映射到一致的 `table-cell-text` role / `table-island` stream。
- 结构 JSON 也可以通过 page-level 或 stream-level 的 `successor_edges`、`successor_relations`、`ro_linkings`、`reading_order_edges`、`reading_order_relations`、`reading_order_linkings`、`precedence_edges`、`order_edges`、`relations`、`reading_streams`、`streams` 提供关系证据。端点可以引用已匹配结构节点的 id/ref、`document`、`elements`、`blocks`、`parsing_res_list`、`layout_det_res.boxes` 的 0-based 下标、`formula_region_id`、`seal_region_id`、`table_region_id`、`layout_region_id` 等模型 region id、OCR anchor 原始 id/ref，也可以直接用文本。如果端点 id 或下标不能通过已匹配的 region/node key 解析，Scriptorium 会回退到同页结构列表文本 alias，因此只有文本列表、没有 bbox 的关系 payload 也能在源文本已存在时驱动顺序。解析成功的边会写到源元素的 `external_structure_successor_ids` 和 `external_structure_precedence_target_ids`；通过 alias 解析的 edge record 还会暴露 `source_alias` / `target_alias` 和 `resolved_via_alias`。
- 一个 relation endpoint 或 stream member 现在可以指向覆盖多条 native/OCR 行的已匹配结构 block。只有所有候选都共享同一个已匹配结构区域 signature 时，Scriptorium 才会展开该 block：先保留原有局部行顺序并补入内部 successor edge，再把外部关系连接到两个 block 边界。重复的可见文本仍保持歧义，不会被扩展；非精确的单字符文本也不会再模糊匹配普通正文。
- `reading_streams` / `streams` 不再只作为关系边来源，也会写入 stream 元数据。Stream 成员可以来自 text sequence、member list、受支持的结构列表下标，也可以来自 stream-local 的 `ro_linkings`、`reading_order_linkings` 等关系别名。成员解析使用和关系端点相同的文本 alias 回退。解析成功的成员会获得 `external_structure_stream_*` 以及 `reading_order_stream_id`、`reading_order_stream_type`、`reading_order_stream_index`，因此 OCR/image 页面即使结构 JSON 没有 region bbox，也能暴露面向翻译的正文、边栏、表格、卡片/网格等局部流。成员级诊断会保留 `external_structure_stream_member_ref`、`external_structure_stream_resolved_via_alias`，以及使用 fallback 时的 alias 文本。
- IR 会写出 `relation_resolution_by_page` 和 `stream_resolution_by_page`，包含已解析 element id、group/alias 解析方式、重叠端点、未解析 ref 与重复 stream member。汇总的 `structure_evidence_*` 指标会输出 group relation edge、补入的 group-internal edge、未解析 relation endpoint/edge，以及已解析/未解析的 group stream ref，供 benchmark 定位真实 sidecar 问题。
- 只有关系边、没有显式 `reading_streams` 的 sidecar 现在也会自动派生翻译局部流。页面提供 `successor_edges`、`ro_linkings` 或等价 reading-order relations 时，Scriptorium 会从安全的 degree-constrained successor chains 生成 `external-relation-*` streams。显式外部 stream 优先：已有 `external_structure_stream_id` 的元素不会被 relation-derived stream 覆盖。
- 当解析后的 successor/precedence 边能形成安全的无环 path-cover 顺序时，可用 `external-structure-relation-fusion-v1` 重排文本阅读顺序。这样 image source 和复杂页面的语义层可以由 OCR/结构 JSON 的局部关系主导，而不是依赖一个容易歧义的全局 block permutation。
- 如果没有可用关系顺序，但页面至少匹配两个外部 block-order tier，Scriptorium 会把它们当作 partial order，而不是整页 permutation：只在相邻显式 tier 间加入 precedence 约束，以 native reading order 作为稳定拓扑排序的 tie-breaker，未匹配元素保持在本地 native 位置。通用模型 `text` order 不会压平更具体的 native table、grid、caption、artifact、footnote 或 sidebar 流；模型自身给出同样具体的 table/grid label 时仍可参与。一个真实 PP block 覆盖多条 native 行时，这些行仍保留局部顺序。发生重排时使用 `reading_order_strategy = external-structure-partial-order-fusion-v2`，并向 `reading_order_evidence` 追加 `external-structure-partial-order`。
- Benchmark 会输出 `structure_evidence_order_source_counts`，用于区分显式模型顺序、Docling body-tree 顺序、允许的隐式列表顺序以及无序的视觉/furniture 区域。发生重排的元素会在模型置信度高于 native heuristic 时保留该置信度到 `reading_order_confidence`。
- 外部 label 还会进入 reading-order scope 和 stream metadata：header/footer/page-number 会成为 page artifact，footnote/sidebar 会成为局部 secondary stream，caption 会成为 caption stream，table 会成为 table-island stream，明确的 card/grid/product/tile 类区域会成为 `grid-island` 翻译/编辑流。普通 `list` 只作为列表角色证据，不自动提升为 grid stream，避免新闻/排行列表误标为卡片网格。

A/B 路径示例：

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark-structure-ab input.pdf --structure-json paddle.json --out-dir outputs/structure-ab
scriptorium benchmark page.png --input-kind image --structure-json page.structure.json --out-dir outputs/page-image
```

`benchmark-structure-ab` 会同时写出 `native-only/benchmark_report.json`、`native-plus-structure/benchmark_report.json`、`structure_ab_report.json` 和 `structure_ab_summary.csv`。A/B 报告会比较 visual similarity、reading-order risk、`grid_island_element_count`、结构区域/匹配/重排数、block-group relation 展开与未解析 relation/stream ref 数、page/stream `needs-structure-evidence` 推荐数、review 推荐数、successor-disagreement 数，以及有 sidecar 时的 semantic successor、semantic relation/stream/assignment missing-label delta 和 semantic stream-assignment id/type accuracy 指标。

真实 PP-StructureV3 CPU 运行现已覆盖 Attention 第 1 页、Transformer-XL 第 1-3 页、JD 第 1 页、PUMA 的图文混排第 5 页，以及启用表格识别的比亚迪财务报告第 136 页。两篇有标注论文在融合后仍保持 `1.0` pair 和 successor accuracy；Transformer-XL 第 1-3 页的 stream `needs-structure-evidence` 减少 1，consensus successor disagreement 减少 26。比亚迪表格运行把 10 个单元格映射到一个 row-major `table-island`，可以正确归因 replacement conflict，但不声称总 conflict 已降低。JD 的候选分歧也降低，但模型输出仍没有 relation/stream 边，因此单靠 block order 不能解决所有翻译局部 stream 歧义。当前 CPU 环境可通过 `requirements-ocr.txt` 安装；PaddlePaddle 3.3 下，需要在导入 Paddle 前设置 `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0`、`FLAGS_enable_pir_api=0`，并以 `enable_mkldnn=False` 创建 PP-StructureV3，绕开当前 PIR/oneDNN 兼容问题。这些是版本相关设置，不属于 Scriptorium 核心依赖。

这个设计和阅读顺序研究方向保持一致：LayoutReader / ReadingBank 把 reading order 当作文档理解的一等任务；ROOR 把 reading order 建模为 layout element 之间的关系；新的 graph/path-cover 工作也把复杂页面视为多条 successor chain，而不是一个脆弱的视觉扫描序列。Scriptorium 不把模型运行时绑进核心路径，但接收同形态证据：局部 successor edges、precedence edges 和 stream memberships。

### 可审查的阅读顺序 Sidecar

每个 benchmark case 现在都会写入 `reading-order.sidecar.proposal.json`。它是 `ScriptoriumReadingOrderSidecar` 提案，不是自动接受的顺序修复：`reading_streams` 保留正文、表格、网格、边栏、caption 和 artifact 的局部 membership；只有高置信局部关系进入 `successor_edges`；较弱的局部关系保留在 `review_successor_edges`；所有跨 stream handoff 都作为不可执行的 `review_transitions` 记录。

```bash
scriptorium propose-reading-sidecar \
  outputs/sample/document.ir.json \
  --sidecar outputs/sample/reading-order.sidecar.proposal.json
```

`sidecar_status: "proposal"` 会被 `apply_structure_evidence()` 有意忽略，并记录 `proposal-skipped` revision。只有人工审查或后续 relation model 显式改为 `accepted` 后，严格的局部边才会影响 IR。Sidecar 的 `document` 节点也能让 image/OCR anchor seeding 可复现，但它们被标为 reference，在结构融合时不会覆盖更强的 region/table metadata。

若存在 semantic ground-truth sidecar，benchmark 会写入 `semantic/reading_order_sidecar_proposal_quality_report.json`，并分别报告 strict edge 与 review edge 的 precision/coverage。`reading_order_proposal_semantic_reviewable_successor_coverage` 表示 strict 加 review 边的合并覆盖，因此证据阈值把正确边移入 review 时，不会被误判为语义回退。

对于带有 `match_mode: "ordered-subsequence"` 的页面，报告还会区分 direct edge 和相邻标注锚点之间的 graph path。`strict_anchor_path_coverage` 只沿可执行局部边；`local_reviewable_anchor_path_coverage` 额外允许 review-only 局部边；`reviewable_anchor_path_coverage` 还允许 review-only 的跨 stream transition。若路径穿过另一个已标注锚点就会被拒绝，因此不会把乱序锚点误判为正确。这只是评测视图：review transition 在单独 accepted 前仍不可执行。没有标注的页面只保留原始 stream/edge/transition 计数；这些是 triage 信号，不是正确率声明。

低 `reading_order_confidence` 不再把整个局部流的边一律降级。`reading_order_sidecar.py` 只有在 review edge 位于同一个 provisional stream 且同时通过三项独立检查时才提升为 strict：互为最近的前向几何邻居、全页 relation graph 实际选中且 score `>= 0.86`、visual-YX、box-flow、relation-graph 三个局部 candidate 的直接 successor 一致。边会记录全部三项 evidence。Relation graph API 会单独暴露实际选中的 path-cover edge，而不是把序列化 candidate order 的 handoff 误当作几何关系；跨 stream transition 被有意排除在提升路径之外。

图片 source 使用同一个 benchmark 命令：

```bash
scriptorium benchmark page.png \
  --input-kind image \
  --image-dpi 96 \
  --structure-json page.structure.json \
  --html-mode structured \
  --out-dir outputs/page-image-benchmark
```

视觉比较会按 `image_dpi` 渲染导出的 PDF，让源图片 visual layer 和打印输出在相同像素尺寸下比较。结构 JSON 可以先生成 `native-ocr` 初始锚点层，再由 structure evidence 反向融合标签、顺序和置信度。

报告会记录 `source`、兼容列 `source_pdf`、`source_type_counts`、`input_kind`、`image_dpi`、`semantic_layer_driver`、`semantic_layer_payload_kind`、`semantic_layer_structure_role`、`structure_evidence_relation_reordered_page_count`、`structure_evidence_order_reordered_page_count`、`structure_evidence_stream_count`、`structure_evidence_resolved_stream_member_count`、`structure_evidence_resolved_stream_alias_member_count`、`structure_evidence_relation_stream_count`、`structure_evidence_resolved_relation_stream_member_count` 和 `structure_evidence_resolved_relation_alias_edge_count`，用于区分 PDF case 与 image case、复现图片像素到 PDF point 的坐标映射，并确认语义层来自 native PDF、结构 JSON、OCR JSON、OCR fallback 还是仅有 visual layer，以及结构重排是由关系边还是 block order 驱动、结构流是否真正解析到元素。`*_alias_*` 计数表示实际通过无 bbox 文本列表 alias fallback 解析成功的关系边或 stream 成员。

长文档可以用 `--page-ranges` 按源页码抽样，例如 `--page-ranges 1-12,136-160,220`。页码是 1-based，且不能和 `--max-pages` 同时使用。渲染后的 `DocumentIR.pages[*].page_index` 仍保留原始源页索引，所以 semantic sidecar 和 Paddle/PP-Structure/Docling 结构 JSON 可以继续按源页码对齐，而不是按抽样后的列表位置误匹配。报告会记录 `page_ranges` 和 `sampled_page_numbers`，便于复现实验。

## 阅读顺序层

PDF 文本只是绘制证据，不保证天然语义顺序。Scriptorium 保持视觉 element id 稳定，然后写入语义顺序元数据：

- `visual_order`: bbox top-left 视觉顺序。
- `semantic_order`: XML/DOM/export 消费的阅读顺序。
- `recursive-xy-cut-v1`: 递归 whitespace cut，用于保留 section heading 和独立栏区域。
- `column-flow-v1`: 检测常见二栏/三栏文本流，按栏再按纵向排序。
- `spatial-graph-v1`: 通过水平重叠和 center proximity 串联弱 irregular column，只在更强 table/repeated-anchor 路径拒绝后启用。
- `box-flow-v1`: 受控 pdfminer-style column-biased fallback，只在候选分歧、列平衡、垂直重叠和列间距都成立时接管弱段落。
- `successor-consensus-arbitration-v1`: 保守 runtime 仲裁路径，用于 sparse weak-column 页面；要求 box-flow 和 relation-graph 高度一致、visual-yx 在相邻后继边上失败，并且共识顺序存在明确 column handoff。
- `infer_relation_graph_order()`: 几何 successor graph 候选，只做 benchmark 诊断，不默认替换当前顺序。
- `structure_relation`: 仅用于 benchmark 的语义候选。它组合 `reading_order_scope`、页眉页脚 artifact、脚注、边栏、caption-target proximity 和正文 relation-graph 排序，形成结构感知候选顺序；在 sidecar 或外部模型证据证明安全前，不会替换 runtime selected order。
- `infer_successor_consensus_order()`: 汇总 visual-yx、box-flow、relation-graph、structure-relation 和 external-structure 的相邻后继边，生成 acyclic path-cover 候选。
- `table-row-major-v1`: 表格主导页面显式保持行优先。
- `mixed-table-column-flow-v1`: 混合表格/正文页面中，局部表格岛 row-major，周围正文继续按多栏流排序。
- `mixed-grid-column-flow-v1`: 门户/电商/卡片式页面中的非表格局部网格路径。它检测相邻紧凑行里的重复 x slot，把区域标成 `root/grid-island-###`、`grid-island-row-major`、`local-structure-grid`，并暴露为 `grid-island` reading stream，供翻译和编辑按卡片网格独立处理。
- 页眉页脚、脚注、边栏/旁注会标记为 `page-artifact`、`footnote`、`sidebar`，保持可编辑但不干扰主体叙事流。
- caption-flow 识别以 `Figure`、`Fig.`、`Table`、`Algorithm` 开头的浅 caption 证据，并区分栏内 caption 和跨 gutter caption。
- `reading_order_caption_target_*` 是局部 figure/table caption 关联证据。annotation 层会把符合类型的 caption 与附近 native image、local raster region 或推断出的 figure/table layout region 关联，要求 bbox proximity 和水平对齐；接近整页的截图/扫描背景会被排除，避免 image-only OCR 页面把整页背景误当成 figure target。

Caption target matching 当前只添加证据，不直接改变 semantic order。caption 节点会获得 `caption-target-proximity`、`<kind>-target` 和位置证据，并通过 HTML `data-scriptorium-caption-target-*` 属性导出；后续可以把它作为 relation graph / successor consensus 仲裁的一项独立结构证据。

非表格 `grid-island` 与表格检测分开：它要求至少两行重复的紧凑卡片 slot，并拒绝宽正文列 slot。复杂门户页的像素相似度仍主要依赖 fidelity 背景层；这条路径解决的是“翻译/编辑时哪些文本属于同一个局部卡片流”的结构问题。

Benchmark 会输出 pairwise order accuracy、successor-edge accuracy、sequence similarity、候选顺序分歧、selected-edge support、edge coverage、conflicted-edge ratio、page-level recommendation 和 reading-order risk。后续优化不应对单个 PDF 调阈值，而应通过 semantic sidecar、外部结构证据和候选仲裁证明泛化能力提升。

此外，新增加了流内候选诊断：`reading_order_candidate_stream_diagnostics` 会按 `reading_order_stream_id` 记录局部文档流内 selected 与候选共识的分歧与推荐，`reading_order_candidate_stream_count` 给出当次 case 的流内候选数量，`reading_order_candidate_stream_recommendation_counts` 则用于 case/summary/CSV 的流级 triage 统计，避免将边栏/脚注与正文流混到同一页级分数。Benchmark 也会记录 `grid_island_element_count`，用于观察复杂卡片/门户布局是否被识别为可翻译局部流。

翻译回渲染路径还会输出 fidelity replacement 风险指标：`fidelity_replacement_element_count`、`fidelity_replacement_overflow_count`、`fidelity_replacement_conflict_count`、`fidelity_replacement_conflict_target_count`、`fidelity_replacement_same_stream_conflict_target_count`、`fidelity_replacement_cross_stream_conflict_target_count`、`fidelity_replacement_min_fit_scale`、`fidelity_replacement_mean_fit_scale` 和 `fidelity_replacement_policy_counts`。源文档无 replacement 时这些字段为 0/`null`；翻译写入 `translated_text` 后，它们用于衡量像素相似度之外的遮罩、压缩和邻近冲突风险。

冲突目标还会按流属性归因：`fidelity_replacement_conflict_target_stream_type_counts`、`fidelity_replacement_conflict_target_stream_id_counts`、`fidelity_replacement_conflict_stream_type_pair_counts` 和 `fidelity_replacement_conflict_stream_id_pair_counts`。同流 target 冲突通常说明局部文本 fitting 空间不足；跨流 target 冲突通常指向语义流边界错误或 mask 扩张过大。

同一套风险也会按局部 reading stream 聚合：`fidelity_replacement_stream_diagnostics`、`fidelity_replacement_stream_type_counts`、`fidelity_replacement_stream_type_overflow_counts`、`fidelity_replacement_stream_type_conflict_counts`、`fidelity_replacement_stream_id_counts`、`fidelity_replacement_stream_id_overflow_counts` 和 `fidelity_replacement_stream_id_conflict_counts`。这些字段用于判断翻译冲突集中在正文、卡片网格、边栏、脚注、表格岛还是页边 artifact；每个 stream diagnostic 也包含 same-stream/cross-stream target counts 和 stream pair-count maps，方便做局部 triage。

对应的输入压力统计包括 `translation_stress`、`translation_stress_element_count`、`translation_stress_source_char_count`、`translation_stress_translated_char_count` 和 `translation_stress_char_expansion_ratio`。`pseudo-expand` 会故意拉长源文本，让 JD/PUMA/门户页这类复杂布局能在没有真实翻译服务的情况下先暴露 replacement 风险。

最新 JD/PUMA/web-HN 三样本翻译压力 rerun 覆盖 15 页，没有页数或尺寸 mismatch；平均视觉相似度为 `0.81899535`，但 567 个 replacement 中仍有 565 个报告邻近冲突，所以后续优化重点仍是 mask、fitting、冲突消解和结构流分批翻译，而不是只追背景层像素分。

Semantic sidecar 除了 `text_sequence`，现在还支持关系式标签：

```json
{
  "version": 3,
  "pages": [
    {
      "page_index": 0,
      "match_mode": "ordered-subsequence",
      "successor_edges": [["Article title", "First body line"]],
      "precedence_edges": [
        {"source": "Sidebar heading", "target": "Sidebar detail"},
        {"from": "Figure 1.", "to": "The caption continues."}
      ]
    }
  ]
}
```

同一个评测器也接受 ROOR/结构 JSON 风格 payload。Sidecar 可以在 page 级提供带文本、可选 id 的 `document`、`elements`、`blocks`、`parsing_res_list` 或 `layout_det_res.boxes`，再用 `ro_linkings`、`reading_order_edges`、`reading_order_relations` 或 `reading_order_linkings` 引用这些标签。没有显式 id 的有序结构列表也可以用 0-based 列表下标引用，这兼容 `[[0, 2], [2, 1]]` 这类常见关系模型输出。它既可以包在标准 `pages` 下，也可以直接复用模型根级的 `page_results`、`raw_results`、`results`、`res`、`result`、`data` 包装：

```json
{
  "version": 3,
  "pages": [
    {
      "page_index": 0,
      "document": [
        {"id": 0, "box": [0, 0, 10, 10], "text": "A"},
        {"id": 1, "box": [20, 0, 30, 10], "text": "C"},
        {"id": 2, "box": [0, 20, 10, 30], "text": "B"},
        {"id": 3, "box": [20, 20, 30, 30], "text": "D"}
      ],
      "ro_linkings": [[0, 2], [2, 1], [1, 3]]
    }
  ]
}
```

`successor_edges` 和 ROOR 风格 linkings 会评估 labelled 节点的相邻后继关系，`precedence_edges` 只要求 source 在 target 之前。通用 `relations` 列表也可以使用，但每个 item 必须显式声明 successor 或 precedence 的 type/kind。关系端点可以是文本、列表下标、数组，也可以是使用 `source` / `target`、`from` / `to`、`head` / `tail`、`source_id` / `target_id` 等别名的字典；id 和受支持的列表下标会先通过 page label map 解析成文本再评分。Page label map 也能识别 PP/结构 payload 里的 `formula_region_id`、`seal_region_id`、`table_region_id`、`layout_region_id` 等模型 region id，因此 sidecar 可以复用 runtime 结构融合使用的同一组 id。Semantic page payload 还会在同页内读取 `res`、`result`、`data`、`page_results`、`raw_results`、`results` 包装后再评分 relation 和 stream labels。只有关系标签、没有 `text_sequence` 的页面会默认按 `ordered-subsequence` 处理，不因为未标注正文而扣 sequence 分。报告会输出 `semantic_relation_successor_accuracy`、`semantic_relation_precedence_accuracy`、relation missing text counts、每个候选的 relation-edge 指标，以及 `semantic_candidate_relation_successor_delta`；当 sequence 分数持平但 relation edge 变好时，候选仲裁也可以给出 `consider-<candidate>`。

Stream sidecar 使用同一套形状。`text_sequence`、`sequence` 或 `texts` 会被视为有序序列，并生成 stream-local successor/precedence 检查。`members`、`elements`、`items`、`children` 只用于声明 stream label 和 missing/coverage 诊断，不会单独暗示顺序；stream-local 的 `ro_linkings`、`reading_order_*` 或 typed `relations` 才提供显式顺序约束。

Stream sidecar 现在还会单独评估 IR 的流归属质量。`semantic_stream_successor_accuracy` / `semantic_stream_precedence_accuracy` 只看局部顺序约束；`semantic_stream_assignment_id_accuracy`、`semantic_stream_assignment_type_accuracy` 以及对应的 label/found/missing/correct count 字段，会把 sidecar 的 stream membership 和当前元素的 `reading_order_stream_id`、归一化后的 `reading_order_stream_type` 对比。这样 benchmark 可以判断 OCR/结构 JSON 是否真的把 image/source 文本分进了正确的正文、边栏、表格、caption、脚注或卡片网格翻译流，而不是只得到一个看似可用的全局顺序。候选顺序不会参与 assignment 评分，因为 stream membership/type 是语义层抽取结果，不是候选排序列表本身。

对应的 triage 字段还包括 `semantic_stream_assignment_id_mismatch_count`、`semantic_stream_assignment_type_mismatch_count` 和 `semantic_stream_assignment_type_confusion_counts`。Confusion key 使用 `expected=>actual`，例如 `grid-island=>body`，用于快速判断复杂页面错在卡片网格、边栏、表格岛、caption、脚注还是页边 artifact 的流类型归属。

在 `benchmark-structure-ab` 中，`semantic_relation_missing_text_delta`、`semantic_stream_missing_text_delta` 和 `semantic_stream_assignment_missing_delta` 表示 native-plus-structure 减去 native-only 的 missing label 数；负向 delta 表示结构 JSON 让 sidecar label 能在提取文本或 stream metadata 中被解析到。`semantic_stream_assignment_id_accuracy_delta` 和 `semantic_stream_assignment_type_accuracy_delta` 表示 native-plus-structure 减去 native-only 的流归属准确率。正向 delta 表示结构 JSON 改善了局部翻译流 membership，即使视觉相似度主要仍由背景层决定。

Semantic sidecar 现在也会给 `structure_relation` 候选打分，并与 visual-yx、box-flow、relation-graph、successor-consensus、external-structure 一起输出候选指标。这样可以观察 page-scope 和 caption-target 结构是否改善 local successor edge，而不把单个无标签样本直接升级成 runtime 规则。

## 研究参考

- PyMuPDF 文档说明 PDF 文本不一定是自然阅读顺序，并提供排序辅助: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- W3C PDF3/PDF14/PDF4 将复杂布局、页眉页脚、footnote、side-bar 视为需要显式阅读顺序修复的 PDF 场景。
- OCR-D PAGE reading-order 指南把 print space 外的 marginalia 排在 primary text/footnote 之后。
- pdfminer.six `LAParams.boxes_flow` 是 horizontal-vs-vertical box ordering 的经典启发式。
- LayoutReader / ReadingBank 把阅读顺序作为文档理解的一等任务。
- Docling rule-based reading order 使用 above/below adjacency 和 horizontal overlap 几何。
- Relation-based reading-order 和 graph/path-cover 方法提示应关注局部 successor edge，而不只看全局 y/x 排序。
