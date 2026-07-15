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

- `--ocr-json` 是稳定的 OCR/layout-anchor 输入。`benchmark` 和 `benchmark-structure-ab` 也接受该参数；A/B 两侧使用同一份 anchors，只有 native-plus-structure 分支融合 `--structure-json`，避免把“换了一组文本节点”误计成结构模型提升。
- `PaddleOcrAdapter` 和 `PpStructureAdapter` 都隔离在 `scriptorium.ocr`，并且延迟导入 `paddleocr`。`scriptorium run-paddleocr-vl` 与 `scriptorium run-pp-structure` 都会先渲染 source 页、穿过 Paddle result wrapper 保留源页索引，再写出可重放 JSON。PP-Structure runner 默认只跑 layout；`--table-recognition`、`--formula-recognition`、`--region-detection` 可按需启用更重的证据模块。
- `run-pp-structure` 默认启用 CPU compatibility mode：在导入 PP-StructureV3 前设置 Paddle 3.3 的 PIR/oneDNN 保护，并传入 `enable_mkldnn=False`。GPU 部署在验证本地 Paddle 栈后可显式使用 `--no-cpu-compatibility-mode`。
- `--structure-json` 是真实模型输出的轻量桥接入口，支持 PaddleOCR-VL / PP-StructureV3 风格 JSON、DoclingDocument JSON，以及 `document` / `ro_linkings` 这类关系式结构 payload。
- 对图片 source，如果没有单独提供 `--ocr-json`，`--structure-json` 也可以先生成初始文本锚点。常见 `parsing_res_list` / `block_bbox` / `block_content`、PP 的 `overall_ocr_res` 等 OCR 字典，以及 ROOR 风格 `document` segment 的 `box` / `text`，都会被归一成 `native-ocr` 文本节点，再由结构 evidence 反向融合标签、顺序和置信度。适配器会递归常见 `res`、`result`、`data`、`pages`、`page_results`、`raw_results`、`results` 包装，并保留 page index fallback。
- `DocumentIR.metadata.semantic_layer` 会记录当前语义层驱动。图片 case 会报告 `structure-json`、`ocr-json`、`ocr-fallback` 或 `visual-only`；原生 PDF case 报告 `native-pdf`。独立 OCR anchors 已经拥有文本/bbox 时，只有 region/role/order 的结构 payload 记录为 `augmenting-evidence`；显式 relation/stream 或实际重排仍会提升为 `semantic-driver`。
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
  data-scriptorium-structure-stream-id="external-block-body-001-003"
  data-scriptorium-structure-stream-type="body"
  data-scriptorium-structure-stream-index="2"
  data-scriptorium-structure-stream-primary="false"
  data-scriptorium-structure-stream-kind="derived-block"
  data-bbox-pdf="76.99,212.49,117.83,224.22"
  contenteditable="true"
>
  PDF text
</div>
```

`structured` 模式不会放整页背景图。输出由可编辑文本节点、结构 shape 节点、原生 image 节点和局部 raster fallback 节点组成，每个节点都能追溯到 IR 中的识别证据。

翻译工具应先使用 `data-scriptorium-translation-stream-id` / `data-scriptorium-translation-stream-type` 保持正文栏、边栏、脚注、表格岛和卡片网格岛的主顺序，再按需使用 `data-scriptorium-structure-stream-*` 把同一主流内的模型 paragraph/block 分组送入翻译与 fitting。`structure-stream-primary="false"` 表示它是从属分组，不能替代主 reading stream。替换文本仍写入 `translated_text`，并走同一套渲染/打印路径回 PDF。

对于 image-only 页面，原生图像仍然是可见层；`native-ocr` 节点默认透明，只在 hover/focus 时可见，避免重复显示文本，同时保留编辑锚点。

### 浏览器编辑补丁

导出的 HTML 会包含一个轻量浏览器桥 `window.ScriptoriumEdits`。编辑可编辑节点后，它会从透明 fidelity 锚点升级为可见的局部 replacement，将改动保存在当前浏览器会话中，并通过 `collect()` 或 `download()` 生成可移植 JSON 补丁。补丁格式为 `scriptorium-html-edits/v1`，记录 document id、element id、目标字段（`edited_text` 或 `translated_text`）、替换文本和导出时的原文。

下载补丁后，先写回原始 IR，再重新导出或打印：

```bash
scriptorium apply-html-edits outputs/document.ir.json document.scriptorium-edits.json
scriptorium export-html outputs/document.ir.json --out-dir outputs/html --display-mode fidelity
scriptorium print-pdf outputs/html/index.html --pdf outputs/edited.pdf
```

导入器默认拒绝不同 document id、未知 element id 或原文已经变化的补丁，避免旧的浏览器补丁悄悄写入错误锚点。`--allow-document-mismatch` 和 `--allow-source-mismatch` 只用于已经人工审查过的迁移。

## 原生视觉保真层

复杂科学论文和网页 PDF 的视觉误差通常不来自阅读顺序，而来自字体度量、嵌入图像、矢量绘制、透明度和浏览器重绘差异。当前原生路径覆盖这些能力：

- `native-image`: PyMuPDF `get_text("dict")` 图像块会保存成本地图像资产，并作为定位 image 元素导出。
- `native-ocr`: 无原生文本且图像覆盖率达到阈值时，PyMuPDF/Tesseract 生成透明可编辑锚点。
- 字体族归一化会把 `NimbusRomNo9L`、`CMR`、`CMMI`、`CMSY`、`SFTT`、`LiberationSans` 等 PDF 字体映射到更接近的浏览器字体。
- `font_profile` 支持 `browser-default` 稳定基线和 `local-urw` 本地字体实验。
- `--font-profile auto`、`--font-size-scale auto`、`--text-fit auto` 会在 benchmark 中运行候选并选择视觉相似度最高的路径。
- `text_fit = svg` 使用 PDF run bbox、baseline origin、SVG `textLength` / `lengthAdjust="spacingAndGlyphs"` 拟合行宽，同时保留透明编辑代理。
- `fidelity` HTML 模式用 SVG 或 raster 页面背景保留源视觉，同时叠加透明可编辑坐标节点。打印时未编辑节点隐藏；编辑或翻译节点作为局部白底 replacement overlay 打印。
- `fidelity` 的 edited/translated replacement 使用 `fidelity-replacement-fit-v3-browser`：导出器仍先计算保守的静态 scale 和局部 mask padding，再逐方向在相邻可见元素处收缩，同时忽略包裹整页的背景 container。字体 ready 后，`window.ScriptoriumFitting` 会实测 Chromium 的真实排版，在 `0.62` 到 `1` 间二分搜索 scale，并且只在能明显提高可用 scale 时把行高压到受限的 `1.0`。静态预测写到 `data-scriptorium-replacement-estimated-overflow`；实测结果写到 `data-scriptorium-replacement-rendered-overflow`、`data-scriptorium-replacement-rendered-fit-scale`、`data-scriptorium-replacement-rendered-line-height`、`data-scriptorium-replacement-rendered-fit-policy`；fitting 后的 `data-scriptorium-replacement-overflow` 反映真实渲染结果。实际 padding 和约束继续通过 `data-scriptorium-replacement-mask-padding`、`data-scriptorium-replacement-padding-constrained`、`data-scriptorium-replacement-padding-constraint-ids`、`data-scriptorium-replacement-padding-constraints` 暴露。
- 普通深色源文本仍默认使用白色 mask。亮色源文本如果落在采样到的深色 raster 边缘，会改用边缘采样的深色 RGB mask，避免白色 replacement 被白色遮罩盖住。fidelity element 的几何来自 source render pixel，而浏览器打印几何使用 96-DPI CSS pixel；打印专用 `--print-*` 变量会在 Chromium 生成 PDF 前按 `96 / render_dpi` 换算 mask bbox、padding 和字号。因此 144 DPI 等 render 输出不会再把 overlay 放大或偏移。打印切换到 print media 后、生成 PDF 前也会执行同一轮浏览器 fitting。
- `--translation-stress pseudo-expand` 会在 benchmark 内写入确定性伪译文到 `translated_text`，用于压测翻译回渲染的 mask、fit-scale、overflow 和邻近冲突；它不是翻译质量评测，也不需要外部翻译服务。
- `--html-mode auto --fidelity-background auto` 会比较 structured redraw、SVG fidelity 和 raster fidelity，并保留更高分候选。
- 打印后的 PDF page box 会归一到源 PDF 尺寸，避免 Chromium A4 1px 量化误差污染视觉指标。
- 当源页数已知时，HTML 打印导出还会删除浏览器追加的尾部空白伪页；删除条件限定为超出源页数、无文字/图片/批注，且只包含空白或白色 drawing。这样可以避免翻译压力测试被 Chromium 分页伪影主导，同时保留真正有内容的翻译溢出页。
- 某些本地 Chromium 环境会在本地图片资源尚未就绪时，让 Playwright 返回一个表面成功但视觉全空的 PDF。`print_html_to_pdf()` 现在会识别全空 Playwright 输出并改走 Chromium CLI；CLI 在打印前推进 3 秒 virtual time，使冷缓存下的本地资源也能稳定完成渲染。
- 简单 drawing 输出为 SVG line/path；密集矢量图可以局部 raster fallback，但仍然保留 bbox/source metadata。

这是一个明确的保真度和可编辑性权衡：普通文本、表格、分隔线、简单 drawing 和支持的 SVG path 尽量保持结构化；无法可靠结构化的复杂图形先以局部 raster 节点保真。

## 外部结构证据融合

PaddleOCR-VL、PP-StructureV3 和 Docling 应该作为可选证据提供者，而不是替换原生 PDF 提取。数字 PDF 的 native extraction 通常更适合保留字体、样式和 bbox；模型输出更适合补充 OCR、layout label、table/formula/chart region 和阅读顺序预测。

Paddle 官方的 `aside_text` 布局标签会和 `sidebar_text` 一样归一化为 sidebar 翻译流。这样页面边侧的版本信息、边注等次要内容仍保留可见和可编辑锚点，但不会混入正文流。

`src/scriptorium/structure_evidence.py` 当前提供：

- `normalize_structure_evidence(payload, document)`: 接受 Paddle 风格 JSON，包括带 `block_bbox`、`block_label`、`block_content`、`block_order` 的 `parsing_res_list`。
- 显式 block order 仍是最强的 block 顺序证据。`parsing_res_list`、`blocks` 或 `elements` 没有 `block_order` 时，只有正文流、表格和明确 card/grid 标签才可把列表位置记录为较弱的 `implicit-list` 顺序；嵌套的 `children`、`sub_blocks`、`sub_regions`、`items`、`cells` 等子列表会按 depth-first 顺序遍历。图片、图表、页眉页脚、脚注和边栏只保留 region/role 证据，不能因序列化位置把图内说明或页边元素拖入正文流。纯 `layout_det_res.boxes` 检测框不会获得隐式顺序。
- PP-StructureV3 的 `table_res_list` cell 会从 `cell_box_list` 或 `table_ocr_pred.rec_boxes` / `rec_polys` 加 `rec_texts` / `rec_scores` 归一化。能匹配到父 table block 时，cell 会继承父 block 顺序，并用 row-major `external_structure_order_subindex` 表示局部单元格顺序；没有父 block 时，只作为较弱的 `implicit-table-cell` 顺序证据。对图片 source，同一个 payload 也可以先生成 `native-ocr` table-cell anchors，再反向融合结构证据。
- PP-StructureV3 的通用 OCR 结果也会直接解析：`overall_ocr_res` 和 `text_paragraphs_ocr_res` 可以从 `rec_boxes` / `rec_polys` 加 `rec_texts` / `rec_scores` 生成文本锚点，`formula_res_list` 可以从 `rec_formula` 生成 `formula` 锚点，`seal_res_list` 可以生成 seal-text 锚点。这些结果默认只作为无序区域证据，除非同时存在更强的 block order、关系边或 reading stream。
- 同一文本且 bbox 高度重合的 OCR 近重复项，会在生成 IR 或结构区域前去重。`formula`、`seal`、`table_cell` 等更具体 label 优先于普通文本；bbox 等价时，`text_paragraphs_ocr_res` 优先于更泛的 `overall_ocr_res`。
- 接受 DoclingDocument JSON，遍历 `body.children`，解析 `texts`、`tables`、`pictures`、`key_value_items`、`groups` 引用，并把 `prov` 转成页面局部结构区域。Docling `furniture.children` 也会作为非正文结构证据解析，用于 page header/footer 等 artifact 的角色和 stream 标注，但不生成正文 block-order 证据。
- Docling body-tree 生成 relation 和 stream 时，只接受同一 container 内连续、同页的文本 sibling。group、table、picture、未解析 ref、跨页，以及 root body 的几何断点都会结束局部 run。这样保留有价值的局部 successor 证据，而不会把 Docling 序列化的 body 顺序当作整页 permutation。
- 当通用的 root-body Docling run 遇到更强的 native table、grid、caption、sidebar、footnote 或 page-artifact stream 时，还会再次切断。受保护的 island 保留 native stream；实际应用的段使用独立 `native-segment-*` provenance，跳过的边界也会写入 review 记录，避免门户卡片 grid 被悄悄折叠进正文翻译流。
- 当 root-body Docling run 落到明确的 native column 时，membership 仍会写为 `external_structure_stream_*`，但 `external_structure_stream_primary = false`。对应 relation record 会写入 `secondary_native_column_flow = true`：边继续作为 sidecar 诊断证据保留，但不能重排全局 path cover，也不会再次被包装成 generic external block stream。这样 native 多栏翻译流保持主导，同时模型证据仍可审查；嵌套 Docling group 仍可执行。
- Docling table 的 `data.table_cells` / `grid` 条目如果带 bbox 和 text，会提升为更具体的 `table_cell` 区域。它们在需要时继承父 table 的 page provenance，保留行、列、span、header 元数据，并根据 row-major 单元格坐标写入 `external_structure_order_subindex`，让只有一个父 block order 的表格岛也能在局部按行列重排。
- 支持 PDF-point bbox、pixel bbox、top-left 和 bottom-left 坐标原点。
- `page_results[*].data`、`res`、`result`、`raw_results`、`pages` 等嵌套模型包装会为 region、relation 和 stream 证据继承最近的显式父级页码。原始 PP-StructureV3 `save_to_json` 若把 `page_index` 留空，Scriptorium 还会识别 `input_path` 中的渲染页名，例如 `page_0005.png` 会恢复为源页索引 4。这样长文档抽样页会继续按源页码对齐，不会误退回包装列表位置，也不需要手工写 JSON wrapper。
- `apply_structure_evidence(document, payload)`: 通过 bbox coverage 和文本相似度把模型区域对齐到原生元素。
- 像素坐标的结构 JSON 会先通过保存的模型输入画布归一化，再和当前渲染页比较。特别是 PaddleOCR-VL result 层的 `width` / `height`（包括嵌套 `res` wrapper 继承的尺寸）会把模型像素映射到 page point 和当前渲染像素，因此同一份 raw JSON 在不同 `--dpi` 下重放不会把区域匹配错位。非精确匹配时，少于两个字母数字字符的片段不能靠 substring 匹配正文；完全相等的单字符标题或单元格仍然有效。
- 当父区域和更具体的子区域同样覆盖某个文本时，会优先选择面积更小的子区域。这样嵌套 card/product/tile 结构可以驱动局部 reading stream，而不会被父级 grid bbox 吞掉。
- PP-Structure 同一页常同时包含精确但无序的 OCR 行，以及带显式 order 的较大 `parsing_res_list` parent。若二者来自同一 provider、归一化 label 相同、bbox/text 能匹配，且只存在一个显式 parent order，精确行会保留为根 `structure_evidence`，parent 则写入 `ordered_companion`。该 order 标记为 `external_structure_order_review_only`：可以生成带 parent provenance 的 block transition，但不会进入 runtime partial order、全页 external-order candidate 或 derived block stream。不同 parent order 发生冲突时拒绝继承。
- Provider 可以在 root、page 或 block/edge 级把 `order_policy`、`relation_policy`、`semantic_policy` 声明为 `review-only`。Review-only region 可以匹配元素并用于 provenance/proposal 评分，但不能分配 runtime role、主/从 stream、semantic-layer ownership 或可执行顺序。Review-only relation 会保留在 `external_structure_relation_edges` 与 sidecar 诊断中，但不会进入 runtime path cover 或 relation 派生流。Benchmark 用 `structure_evidence_review_region_count` 和 `structure_evidence_review_relation_edge_count` 单独暴露这些证据，不与可执行 evidence 混计。
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
- 显式 ordered 的正文/段落 block 可以成为 `external-block-body-*` 从属翻译分组：至少两个匹配文本元素的 coverage 不低于 `0.5`，它们必须共享同一个已选 native flow segment 和列，且尚未被显式/relation stream 占用。分组写入 `external_structure_stream_*`，并标记 `external_structure_stream_primary = false`；主 `reading_order_stream_*` 保持不变，HTML 同时输出 `data-scriptorium-structure-stream-*`。这样 block boundary 可辅助分批翻译/fitting，却不会把稳定多栏主流切碎。诊断级 generic block，以及 table/grid/caption/artifact/footnote/sidebar 成员都会被排除。`derived_block_stream_count`、`derived_block_stream_member_count` 和 `derived_block_streams_by_page` 会记录这项保守派生。

A/B 路径示例：

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark-structure-ab input.pdf --structure-json paddle.json --out-dir outputs/structure-ab
scriptorium benchmark-structure-ab page.png \
  --input-kind image \
  --ocr-json page.ocr.json \
  --structure-json page.structure.json \
  --out-dir outputs/page-image-ab
```

`benchmark-structure-ab` 会同时写出 `native-only/benchmark_report.json`、`native-plus-structure/benchmark_report.json`、`structure_ab_report.json` 和 `structure_ab_summary.csv`。Case/CSV 还会保存两侧 OCR JSON 与 structure JSON provenance。A/B 报告会比较 visual similarity、reading-order risk、`grid_island_element_count`、结构区域/匹配/重排数、block-group relation 展开与未解析 relation/stream ref 数、page/stream `needs-structure-evidence` 推荐数、review 推荐数、successor-disagreement 数，以及有 sidecar 时的 semantic successor、semantic relation/stream/assignment missing-label delta 和 semantic stream-assignment id/type accuracy 指标。

真实 PP-StructureV3 CPU 运行现已覆盖 Attention 第 1 页、Transformer-XL 第 1-3 页、JD 第 1 页、PUMA 的图文混排第 5 页，以及启用表格识别的比亚迪财务报告第 136 页。PaddleOCR-VL 1.6 对 PUMA 第 5 页的真实重放还在 96 与 144 DPI 同时验证了模型画布映射：同一份 raw JSON 在两个 DPI 下都匹配 24 个元素、没有 selected reorder、没有 candidate-disagreement delta；其中 4 条保守 block 派生流覆盖 17 条 native 正文行，且不改变这些结果。两篇有标注论文在融合后仍保持 `1.0` pair 和 successor accuracy；Transformer 第 1 页重放派生出 6 条同列 block 流、覆盖 85 个成员，successor accuracy 仍为 `1.0`。JD 没有派生 block 流，正确保留其 35 个 native grid-island 成员。这些是局部翻译边界，不是语义顺序正确率声明：PUMA 和 JD 仍缺少人工 relation sidecar，PUMA 的伪翻译 conflict 总量也还没有下降。Transformer-XL 第 1-3 页的 stream `needs-structure-evidence` 减少 1，consensus successor disagreement 减少 26。比亚迪表格运行把 10 个单元格映射到一个 row-major `table-island`，可以正确归因 replacement conflict，但不声称总 conflict 已降低。对于图片和扫描 PDF，模型证据可以成为主文本源；对于数字 PDF，它应优先补 role/order/table/formula 证据，同时保留 native text/style。当前 CPU 环境可通过 `requirements-ocr.txt` 安装；PaddlePaddle 3.3 下，需要在导入 Paddle 前设置 `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0`、`FLAGS_enable_pir_api=0`，并以 `enable_mkldnn=False` 创建 PP-StructureV3，绕开当前 PIR/oneDNN 兼容问题。这些是版本相关设置，不属于 Scriptorium 核心依赖。

OpenDataLoader PDF 2.4.7 是面向数字 PDF 的 Apache-2.0、确定性 CPU/Java
provider。使用 Java 11+ 安装 `requirements-opendataloader.txt`，再运行
`scriptorium run-opendataloader`。Adapter 请求 XY-Cut 顺序，同时保存 raw 与
normalized replay JSON，把 PDF bottom-left bbox 转成 Scriptorium 的 top-left PDF
坐标，并生成稳定的 `opendataloader-p####-b####` id。无效 block 会断开 relation
chain，不会把前后节点错误相连。Provider 文档中合法但不在抽样 `DocumentIR`
中的页面会跳过并记录诊断；超过 provider 声明总页数的 page number 会被拒绝。

结构融合会自动识别 raw OpenDataLoader JSON。它的 block label、order 和相邻
successor edge 全部带 semantic/order/relation `review-only` policy，因此可以独立
评分或与另一 provider 求交，但不能改变 role、stream、semantic ownership 或
runtime order。该 provider 仍是 PDF-only；image source 继续由 OCR/layout JSON
主导语义路径。

Surya FastLayout 是独立的可选 provider，因为它会固定一组模型/图像依赖，而且权重许可比 Apache-2.0 代码许可多出额外条款。应在专用环境安装 `requirements-surya.txt`，审阅 modified AI Pubs OpenRAIL-M 权重/输出许可后再传入 `--accept-model-license`：

```bash
scriptorium run-surya-layout input.pdf \
  --page-ranges 1-3 \
  --device cpu \
  --accept-model-license \
  --output outputs/input.surya-layout.json
```

`SuryaLayoutAdapter` 会要求 FastLayout detector 返回 encoder feature，并直接调用 learned order head。若 head/feature map 缺失、安装版本无法暴露模型容量、检测框超过模型声明容量（本次权重为 128），或 position 是小数、无效值、重复/缺失值而不能组成完整 permutation，它会 fail closed。这样不会把 Surya 的 raster-order fallback 误记为 learned evidence。每个输出 block 都带 `order_policy: review-only` 与 `semantic_policy: review-only`，successor 都带 `relation_policy: review-only`；模型 label 不能静默修改 role/stream，模型顺序和关系也都不能重排 IR。

这个设计和阅读顺序研究方向保持一致：LayoutReader / ReadingBank 把 reading order 当作文档理解的一等任务；ROOR 把 reading order 建模为 layout element 之间的关系；新的 graph/path-cover 工作也把复杂页面视为多条 successor chain，而不是一个脆弱的视觉扫描序列。Scriptorium 不把模型运行时绑进核心路径，但接收同形态证据：局部 successor edges、precedence edges 和 stream memberships。

### 可审查的阅读顺序 Sidecar

每个 benchmark case 现在都会写入 `reading-order.sidecar.proposal.json`。它是 `ScriptoriumReadingOrderSidecar` 提案，不是自动接受的顺序修复：`reading_streams` 保留正文、表格、网格、边栏、caption 和 artifact 的局部 membership；只有高置信局部关系进入 `successor_edges`；较弱的局部关系保留在 `review_successor_edges`；所有跨 stream handoff 都作为不可执行的 `review_transitions` 记录。

Sidecar schema `1.1` 在不改变上述契约的前提下增加显式模型 block transition 提案。只有两个 structure region 的数值 `block_order` 唯一且刚好相差 1、order 明确来自 explicit 字段、所有匹配成员的 region coverage 至少为 `0.5`、并且两端都是 primary text block 时才会生成关系。页眉页脚 artifact、边栏、脚注、caption、table/grid island、同 order 歧义、implicit list order 和缺失的数值 tier 都是硬断点。关系连接前一 block 的最后一个 selected native member 与后一 block 的第一个 member，保留 provider/order/label/bbox/member provenance，并始终标记 `review_required`。因此在独立 relation source 被接受前，`strict_block_transition_count` 应持续为 0。

```bash
scriptorium propose-reading-sidecar \
  outputs/sample/document.ir.json \
  --sidecar outputs/sample/reading-order.sidecar.proposal.json
```

独立 provider 可以在同一组稳定 document node 上求交：

```bash
scriptorium consensus-reading-sidecars \
  outputs/native/reading-order.sidecar.proposal.json \
  outputs/pp/reading-order.sidecar.proposal.json \
  outputs/surya/reading-order.sidecar.proposal.json \
  --min-providers 2 \
  --output outputs/reading-order.consensus.proposal.json
```

`build_provider_consensus_sidecar()` 只接受尚未 accepted 的 proposal、唯一且非空的 provider 名、完全一致的 page 集合，以及 id/text/PDF bbox 都一致的 stable-element fingerprint。它按 source/target ID 求交显式 block-order transition，记录 provider provenance 与最低 confidence，并删除任何 selected-order 标记。输出仍是 `sidecar_status: proposal`、`policy: review-only`、`runtime_reorder: false`；consensus 只降低 review 噪声，不是 acceptance 机制。

`sidecar_status: "proposal"` 会被 `apply_structure_evidence()` 有意忽略，并记录 `proposal-skipped` revision。只有人工审查或后续 relation model 显式改为 `accepted` 后，严格的局部边才会影响 IR。Sidecar 的 `document` 节点也能让 image/OCR anchor seeding 可复现，但它们被标为 reference，在结构融合时不会覆盖更强的 region/table metadata。

若存在 semantic ground-truth sidecar，benchmark 会写入 `semantic/reading_order_sidecar_proposal_quality_report.json`，并分别报告 strict edge 与 review edge 的 precision/coverage。`reading_order_proposal_semantic_reviewable_successor_coverage` 表示 strict 加 review 边的合并覆盖，因此证据阈值把正确边移入 review 时，不会被误判为语义回退。

显式 block transition 另外报告 strict/review 的 candidate、labelled、correct、precision 和 coverage 字段。这样模型 block-order 边不会被混进通用跨 stream transition，同时普通 benchmark 和 native-only / native-plus-structure A/B 都能持续检查 `strict = 0` 的安全约束。

对于带有 `match_mode: "ordered-subsequence"` 的页面，报告还会区分 direct edge 和相邻标注锚点之间的 graph path。`strict_anchor_path_coverage` 只沿可执行局部边；`local_reviewable_anchor_path_coverage` 额外允许 review-only 局部边；`reviewable_anchor_path_coverage` 还允许 review-only 的跨 stream transition；`review_block_transition_anchor_path_coverage` 单独表示显式 block-order 提案新增的 anchor path。若路径穿过另一个已标注锚点就会被拒绝，因此不会把乱序锚点误判为正确。这只是评测视图：review transition 在单独 accepted 前仍不可执行。没有标注的页面只保留原始 stream/edge/transition 计数；这些是 triage 信号，不是正确率声明。

`local_structure_successor_*`、`local_table_successor_*` 和 `local_grid_successor_*` 只统计带原生 `table-local-order` 或 `grid-local-order` 标记、且不是 review 的 strict proposal edge。仅有 table/grid stream type 并不足够：没有该原生标记的 external model membership 会被排除。这样原生几何诊断和外部结构证据保持分离，provider 新建的 table/grid stream 也不会虚增 local-edge precision。

低 `reading_order_confidence` 不再把整个局部流的边一律降级。`reading_order_sidecar.py` 只有在 review edge 位于同一个 provisional stream 且同时通过三项独立检查时才提升为 strict：互为最近的前向几何邻居、全页 relation graph 实际选中且 score `>= 0.86`、visual-YX、box-flow、relation-graph 三个局部 candidate 的直接 successor 一致。边会记录全部三项 evidence。Relation graph API 会单独暴露实际选中的 path-cover edge，而不是把序列化 candidate order 的 handoff 误当作几何关系；现在还会保留每条已选边在选择时的 source/target 替代边、margin 和 max-regret。出现 score tie 的边绝不会自动提升，review edge 会带上 `relation_graph` payload，交给结构模型或人工复核。跨 stream transition 被有意排除在提升路径之外。

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
- `infer_relation_graph_selected_edge_diagnostics()`: 暴露实际选中的局部 relation edge，以及其选择时的 source/target alternative score、margin、max-regret、选择步骤和 exact-tie 标记。它用于复核和模型融合，不代表序列化 candidate order 已被判定正确。
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

Benchmark 会输出 pairwise order accuracy、successor-edge accuracy、sequence similarity、候选顺序分歧、selected-edge support、edge coverage、conflicted-edge ratio、page-level recommendation 和 reading-order risk。relation-graph 还会输出 `reading_order_relation_graph_path_cover_edge_count`、`reading_order_relation_graph_tied_edge_count` / `reading_order_relation_graph_tied_edge_ratio`、`reading_order_relation_graph_margined_edge_count` 和 `reading_order_relation_graph_mean_minimum_margin`：它们分别表示实际选中的局部边、存在完全同分可行替代边的数量/比例，以及有替代项时较弱一侧的平均选择 margin。这些是歧义 triage，不是正确率或校准概率。后续优化不应对单个 PDF 调阈值，而应通过 semantic sidecar、外部结构证据和候选仲裁证明泛化能力提升。

最新 relation-graph 选择歧义验证：

| 样本 | 输出目录 | 视觉相似度 | Path-cover 边 | 完全同分 | 平均最小 Margin | 结论 |
|---|---|---:|---:|---:|---:|---|
| Transformer-XL 前 3 页 | `outputs/external/transformer-xl-relation-ambiguity-v1` | 0.98160664 | 288 | 3 (1.041667%) | 0.00123018 | 有标注论文的视觉和语义结果保持稳定；同分边只作为 review 证据。 |
| PUMA 年报前 12 页 | `outputs/external/puma-2024-annual-report-relation-ambiguity-v1` | 0.9795117 | 329 | 0 | 0.03710031 | 主瓶颈是弱几何/模型结构证据，不是完全同分。 |
| JD 首页截图 PDF | `outputs/external/jd-home-relation-ambiguity-v1` | 0.99576887 | 93 | 2 (2.150538%) | 0.03896739 | 密集 OCR/卡片布局仍需要显式 stream 或 successor relation。 |
| 比亚迪年报 p. 136 | `outputs/external/byd-2024-annual-report-relation-ambiguity-v1` | 1.0 | 30 | 0 | 0.09570952 | 没有完全同分，但表格/stream 证据仍是翻译局部处理所必需的。 |

这些字段不会改变 runtime selected order。它们把纯几何图无法偏好的位置暴露出来，阻止同分边自动 strict promotion，并为 Paddle/Docling 或未来 semantic scorer 提供明确的局部补证目标。任何未来自动使用 margin 的阈值都必须在独立的 relation-style sidecar 上验证，不能从这些 benchmark 标签反推调参。

此外，新增加了流内候选诊断：`reading_order_candidate_stream_diagnostics` 会按 `reading_order_stream_id` 记录局部文档流内 selected 与候选共识的分歧与推荐，`reading_order_candidate_stream_count` 给出当次 case 的流内候选数量，`reading_order_candidate_stream_recommendation_counts` 则用于 case/summary/CSV 的流级 triage 统计，避免将边栏/脚注与正文流混到同一页级分数。Benchmark 也会记录 `grid_island_element_count`，用于观察复杂卡片/门户布局是否被识别为可翻译局部流。

原生 `table-island` / `grid-island` 的严格边不会被伪装成另一张全页候选票。sidecar 只有在边带有 `table-local-order` 或 `grid-local-order` 时，才把它写入 `local_structure_*` 诊断：局部 stream 数、潜在/严格 successor edge 数、严格覆盖率、selected/reference 覆盖率，以及没有被通用 consensus 保留的严格边。它既不制造跨 stream handoff，也不改变 page-level consensus；只有一个局部岛完整被严格边覆盖时，流级建议才会变成 `keep-selected-local-structure`。正文或区域交接仍不确定的页面会继续保持 `needs-structure-evidence`。`benchmark-structure-ab` 也会输出相同计数及 delta，因此“通用分歧变少、但 stream triage 变差”的结构模型结果不会被误认为无条件语义提升。

`protected_successor_consensus` 是下一层、但仍只用于诊断的 relation candidate。它会在通用加权 path cover 之前安装有效的原生 table/grid strict edge，而不是给它们伪造 vote。字段会把 protected edge 和 unresolved constraint 分开，并分别记录 unknown endpoint、self-loop、入/出度冲突和 cycle。`local_structure_constrained_consensus_disagreement_*` 只计算约束序列化后仍缺失的严格 island edge。该候选不会改变 `infer_semantic_reading_order()` 或 runtime arbitration，并且被排除在自动 semantic-candidate 建议之外。没有适用 strict native edge 的有标注 case，其聚合 semantic 分数会是 `null`，而不是误导性的满分。

翻译回渲染路径还会输出 fidelity replacement 风险指标：`fidelity_replacement_element_count`、`fidelity_replacement_estimated_overflow_count`、`fidelity_replacement_overflow_count`、`fidelity_replacement_layout_measurement_available`、`fidelity_replacement_layout_measured_count`、`fidelity_replacement_browser_fit_count`、`fidelity_replacement_line_height_compacted_count`、`fidelity_replacement_sampled_background_mask_count`、`fidelity_replacement_conflict_count`、`fidelity_replacement_conflict_target_count`、`fidelity_replacement_same_stream_conflict_target_count`、`fidelity_replacement_cross_stream_conflict_target_count`、`fidelity_replacement_padding_constrained_count`、`fidelity_replacement_padding_constraint_side_count`、实际/静态的 `fidelity_replacement_min_fit_scale` / `fidelity_replacement_mean_fit_scale`，以及 `fidelity_replacement_policy_counts`。其中 `estimated_overflow` 是保留的静态 predictor，而 `overflow` 在布局 measurement 可用时是 Chromium 实测 clipping，二者不能混用。每个 fidelity case 会写出 `quality/fidelity_replacement_layout_report.json`，保留 DOM 尺寸和裁切证据；stream diagnostic 也会记录 estimate、测量、browser fitting 和行高压缩。

冲突目标还会按流属性归因：`fidelity_replacement_conflict_target_stream_type_counts`、`fidelity_replacement_conflict_target_stream_id_counts`、`fidelity_replacement_conflict_stream_type_pair_counts` 和 `fidelity_replacement_conflict_stream_id_pair_counts`。同流 target 冲突通常说明局部文本 fitting 空间不足；跨流 target 冲突通常指向语义流边界错误或 mask 扩张过大。

同一套风险也会按局部 reading stream 聚合：`fidelity_replacement_stream_diagnostics`、`fidelity_replacement_stream_type_counts`、`fidelity_replacement_stream_type_overflow_counts`、`fidelity_replacement_stream_type_conflict_counts`、`fidelity_replacement_stream_id_counts`、`fidelity_replacement_stream_id_overflow_counts` 和 `fidelity_replacement_stream_id_conflict_counts`。这些字段用于判断翻译冲突集中在正文、卡片网格、边栏、脚注、表格岛还是页边 artifact；每个 stream diagnostic 也包含 same-stream/cross-stream target counts 和 stream pair-count maps，方便做局部 triage。

对应的输入压力统计包括 `translation_stress`、`translation_stress_element_count`、`translation_stress_source_char_count`、`translation_stress_translated_char_count` 和 `translation_stress_char_expansion_ratio`。`pseudo-expand` 会故意拉长源文本，让 JD/PUMA/门户页这类复杂布局能在没有真实翻译服务的情况下先暴露 replacement 风险。

v3 JD/PUMA/web-HN 三样本 rerun 覆盖相同 15 页，没有页数或尺寸 mismatch；平均视觉相似度为 `0.92760169`（v2 padding-only 为 `0.81937118`），max / mean / p95 diff 为 `0.10089579` / `0.04620805` / `0.09823334`。567 个 replacement 中，静态 estimate 为 326 个 overflow，Chromium 实测 clipping 为 81 个；它们是不同指标，不能表述成直接的 `326 -> 81` 降幅。全部 567 个 replacement 都经过浏览器 fitting，81 个使用行高压缩，101 个使用深色背景采样 mask。JD 仍占 79 个真实 clipping，下一步应做通用的局部 flow reflow，而不是继续缩小 mask。

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

同一份 A/B 现在还包含 `reading_order_local_structure_stream_delta`、`reading_order_local_structure_successor_edge_delta`、`reading_order_local_structure_consensus_disagreement_edge_delta` 和 `stream_keep_selected_local_structure_delta`。它们只衡量原生几何已确认 table/grid 岛内的严格边，不能替代有人类标注的 semantic successor accuracy。

Semantic sidecar 现在也会给诊断专用的 `protected_successor_consensus` 候选打分，并与 visual-yx、box-flow、relation-graph、structure-relation、successor-consensus、external-structure 一起输出候选指标。这样可以观察关系保护候选是否改善 local successor edge，而不把单个无标签样本直接升级成 runtime 规则。

完整 ROOR validation 说明它必须继续只是诊断项。使用官方 text/layout anchor、并隔离 `ro_linkings` 后，49 页全部使用稳定 element ID 解析 endpoint，未解析 identifier 为 0，因此即使 segment text 重复也保留 2,612 条官方 relation。strict native local edge 在直接可标注 endpoint 上得到 `316/617`（`0.51215559`）；protected candidate 在其适用 relation 范围内为 `0.41918103`，没有超过 selected native order（`0.48774885`）。因此 constraint preservation 只证明 serializer 行为，不证明 relation correctness。runtime hard constraint 必须来自显式 external successor/stream evidence、独立验证过的 relation predictor，或 accepted review。泄漏边界和完整结果见[外部基准](external-benchmarks.zh-CN.md#roor-关系基准-v1)。

## 研究参考

- PyMuPDF 文档说明 PDF 文本不一定是自然阅读顺序，并提供排序辅助: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- W3C PDF3/PDF14/PDF4 将复杂布局、页眉页脚、footnote、side-bar 视为需要显式阅读顺序修复的 PDF 场景。
- OCR-D PAGE reading-order 指南把 print space 外的 marginalia 排在 primary text/footnote 之后。
- pdfminer.six `LAParams.boxes_flow` 是 horizontal-vs-vertical box ordering 的经典启发式。
- LayoutReader / ReadingBank 把阅读顺序作为文档理解的一等任务。
- Docling rule-based reading order 使用 above/below adjacency 和 horizontal overlap 几何。
- Relation-based reading-order 和 graph/path-cover 方法提示应关注局部 successor edge，而不只看全局 y/x 排序。
- 2026 的复杂布局阅读顺序研究进一步以 max-regret path cover 处理局部候选边，并说明应把同分/低 margin 边交给额外语义或结构证据，而非把几何 tie-break 当作事实: https://arxiv.org/html/2607.01018

## Docling 审查型 Provider

`scriptorium run-docling` 接受 PDF 和图片 source，同时写出 Docling 原始 JSON
与标准化 structure payload。可选环境固定为 `docling==2.111.0`、
`docling-core==2.86.0` 和 `docling-ibm-models==3.13.3`。Docling 代码及 IBM
models 使用 MIT 许可，Heron checkpoint 使用 Apache-2.0。Heron 是学习式
RT-DETR 布局检测器，但 Docling 后续 reading-order predictor 是规则式实现。

标准化 payload 将语义、顺序和关系声明为 `review-only`，禁用 provider streams，
将 external candidate 与 successor consensus 隔离，并设置
`runtime_reorder: false`。已有的可信 OCR evidence 会被保留，review-only identifier
也不能改变语义标注分母。A/B 应使用标准化 `--output`，而不是 `--raw-output`。

`candidate_consensus_policy: isolated` 是 provider-neutral 的 structure contract，
不是 Docling 特例。通用 page/block/relation JSON 会把隔离标记传播到每个匹配元素和
已解析 relation endpoint。external candidate 仍可由 sidecar 独立评分，但 successor
consensus 与 page/stream recommendation 只使用和控制分支相同的 native candidates。

## 可训练 Relation Ranker

可选的 `requirements-relation-ranker.txt` 路径使用归一化 source/target 几何、重叠、
方向、尺寸和少量文本形状特征训练 `HistGradientBoostingClassifier`。训练器只读取官方
ROOR `data.train.txt`，通过 UID SHA-256 划出内部 calibration subset，并在该 subset
上选择使 top-successor relation F1 最大的阈值。训练过程不会打开 validation 文件或
benchmark sidecar。

`train-relation-ranker` 写出本地 joblib 模型及相邻 manifest，记录 feature schema、
train-index digest、fit/calibration 数量、数据许可、阈值、校准指标、sklearn 版本和模型
SHA-256。`run-relation-ranker` 加载前校验 digest，拒绝任何已含 successor/
`ro_linkings` 答案的输入，并输出带置信度、review-only、
`candidate_consensus_policy: isolated`、`runtime_reorder: false` 的边。Joblib 加载时
可能执行代码，因此只信任本机生成的 bundle。

ROOR relation 不一定是一条单路径，部分 source node 有多个合法 immediate successor。
因此 v2 bundle 会在 fit partition 上训练第二个二分类 branch gate，输入 source 几何、
前两个 pair score 与 margin，以及选定的 pair 几何特征。独立 calibration sweep 决定
是否输出 rank-2 edge。推理最多输出两个 successor，并分别记录 `confidence` 和
`branch_confidence`；不会把 pair scorer 变成不受约束的全局多边阈值。

`run-relation-ranker` 也接受多页 `DocumentIR`。每个含文本的页面都会投影为归一化
PDF-space segment，使用同一个模型评分，再输出通用
`pages/elements/successor_edges` structure JSON。因此 native PDF 文本、图片 OCR
anchor、年报和门户截图共享一条推理路径，而不是只支持 ROOR runtime。

模型 bundle 保存仅由 fit partition 计算的逐特征 1%/99% envelope。每个输出页面会
报告 mean pair confidence、edge-level envelope outlier ratio 和 feature-value outlier
ratio。这些是域漂移诊断，不是正确率估计；模型在域外页面也可能保持很高 confidence。
它们用于未来 runtime rejection，并帮助优先补独立标注。

`requirements-semantic-order.txt` 新增隔离的语义研究路径。当前 preset 固定
Apache-2.0 的 Google BERT-Tiny checkpoint 与 revision，使用预训练 NSP head 计算
`log p(IsNext)`，并把分数写入内容寻址的 SQLite cache。模型 identity、revision、许可、
截断方式和评分公式都属于 feature contract；本地 snapshot 只改变获取方式，不能改变模型
身份。语义 bundle 在推理时必须提供同一个 scorer；v2 geometry bundle 会拒绝额外特征，
不会静默接受错误的 feature shape。

第一轮直接特征实验明确不晋升。在与 v2 相同的 ROOR train 122/27 fit/calibration 文档、
138,513 个样本和随机种子下，把 Tiny NSP 作为第 26 个特征后，top-edge F1 从
`0.65488640` 降至 `0.64327062`，branch F1 从 `0.66737288` 降至 `0.65594855`。
该可选路径用于让语义 A/B 可复现，不代表新的默认能力；输出仍为 review-only，runtime
reorder 继续关闭。

v4 语义路径改用二阶段 contract。保持不变的 v2 pair estimator 先为每个 source 选择最多
5 个 target，只有这些 candidate 才计算 NSP。31 维 reranker 组合 base score/rank/margin、
NSP 原始/相对分数和原有 25 个 pair feature；阈值来自 fit 文档上的 5 个 document-hash
OOF fold，不读取 27 文档 calibration partition。最终 fit candidate recall 为
`0.94110838`；ROOR 只需缓存 35,751 个 unique pair，而 direct 路径需要 402,395 个。
冻结后的 calibration top/branch F1 为 `0.67855816/0.69343066`。

```bash
pip install -r requirements-semantic-order.txt
scriptorium train-relation-ranker path/to/ROOR-Datasets/data \
  --semantic-scorer bert-tiny-uncased-nsp \
  --semantic-fusion top-k-rerank \
  --semantic-top-k 5 \
  --semantic-cache outputs/cache/semantic-successor.sqlite3 \
  -o outputs/models/semantic-relation-ranker.joblib
```

Comp-HRDoc relation evaluator 也改为严格两阶段：先完成所有 mode prediction，再解析或打开
任何 semantic sidecar。报告明确写入 `labels_opened_after_all_predictions: true`；benchmark
CLI 运行可选语义 ranker 时，必须提供同一个固定 scorer/cache。

`benchmark-relation-rankers-roor` 对已获取的 ROOR corpus 使用相同的两阶段 contract，
分别比较 top、branch 与 degree-one path-cover edge。在官方 validation 全部 49 页上，v4
把 branch F1 从 `0.69167292` 提高到 `0.73061145`，path-cover F1 从 `0.68729852`
提高到 `0.71334792`。

Hierarchy benchmark 可通过 `--relation-model` 和相同 semantic scorer 参数接入 v4。
Native geometry 继续主导 membership、within-region stream 和已有 region transition；semantic
edge 不能填补空 region slot，只有在它与恰好一条 boundary-aligned native region edge 冲突、
置信度至少高 `0.10` 且替换不产生环时，才允许一换一。这样 transition 总量和局部指标都不变。
64 页 development corpus 发生 4 次替换，calibration line/region F1 达到
`0.93209877/0.90690691`。独立的官方 test 32 页窗口发生 2 次替换，line/region F1
提高到 `0.94255569/0.91990847`，membership 与 within-region F1 不变。

## Comp-HRDoc Relation Benchmark

`fetch-comphrdoc` 固定 MIT Comp-HRDoc 仓库 revision，并通过 SHA-256 校验
129,857,097-byte Git LFS annotation object。它只读取 unified test annotation
member，下载指定 arXiv source 文档，并把每页直接渲染到官方标注尺寸；不会重新分发
HRDoc image asset。

每个文本 block 会展开成 textline node；图形标注会保留为带稳定伪文本的
figure/table node。block 内连续 textline 局部连接；`reading_order_label = 1`
把当前 block tail 连接到下一个官方 reading-order block，`0` 结束该局部链。
floating label `2` 不并入 body flow：figure 在 caption 前，table caption 在 table 前，
与官方 evaluator 一致。answer-free structure 只含带类型的 layout/bbox anchor；稳定 id 与
`ro_linkings` 写入相邻 semantic sidecar，因此模型推理看不到答案。样本按固定
document/page prefix 选择，manifest 记录仓库 revision、archive/PDF hash、URL 和
relation 数。这是 oracle-layout order benchmark，不是 OCR detection 评分。

关系推理时，显式 figure/table role 可以产生经局部几何约束的 caption edge。
这些 edge 仍为 review-only，并标记 `relation_origin = structure-role-geometry`；
它会替换同一 source 的 learned outgoing edge，而不是制造歧义分支。稳定的
layout `block_id` 用于将 caption 多行分组：figure 指向首行，caption 尾行在 table
之前。answer-free payload 不会暴露 Comp-HRDoc 官方 `reading_order_id` 或 relation label。

Floating oracle 只在隔离的 semantic sidecar 内按官方 relation id 分组。它不依赖
caption 在图形 annotation 之前还是之后；官方 test 数据中两种顺序都存在。

`fetch-comphrdoc-relations` 生成 annotation-only 的跨文档 floating prefix。typed layout
anchor 与 semantic 答案分目录保存；manifest 明确记录 label 只用于选页，
不会进入推理输入。它不下载或重新分发 source image。主 body successor 会跳过
floating group，与官方 evaluator 分离 body/floating chain 的契约一致。
`benchmark-comphrdoc-relations` 只加载一次模型，对同一批页面分别禁用和启用
structure-role fusion。

`train-floating-ranker` 只读取固定 Comp-HRDoc 官方 train member。页面按 document-id
hash 拆分，同一论文的页不会跨越 fit/calibration。每个图形 block 与官方 caption
组成正例，同页附近文本 block 作为 hard negative。27 个浅层特征包含图形类型、
归一化 pair 几何、overlap、相对方向、block 行数、文本长度和 caption prefix。
模型是 balanced histogram gradient booster，阈值只由 train 内 calibration partition 选择。
每个阈值候选都会使用与推理一致的 cardinality-first 最大权重全局一对一 assignment。
Edge margin 取两类竞争差值中的较小者：同一 graphical source 的其他 caption，以及同一
caption 的其他 graphical source。这样不会把失去全局 assignment 的 source-local top score
误当作高 margin。推理只输出 isolated review-only edge；含答案输入会被拒绝，runtime
reorder 仍关闭。缺少 assignment policy 字段的旧 manifest 继续使用原始 greedy
source-best-margin decoder，保证历史模型可复现。

Manifest 记录 archive/member hash、split policy、4,102 个 fit 页、1,073 个 calibration 页、
49,763 个样本、5,638 个正例、`assignment_policy = global-cardinality-weight-v1` 和
`selection_margin_policy = min-row-column-score-gap-v1`。阈值 `0.36` 在 1,446 条
calibration label 上得到 1,373/1,489 correct/predicted，precision/recall/F1 为
`0.92209537/0.94951591/0.93560477`，高于 greedy decoder 的 calibration F1
`0.91295681`。两次训练产生字节完全相同的 model；manifest 的指标与 policy 相同，
只分别记录各自输出文件名。

Reliability 与 F1 operating point 分开校准。Review gate 要求 calibration 上至少 25 条
预测且 precision `>= 0.95`；confidence `>= 0.85`、assignment margin `>= 0.72`
得到 995/1,047 correct，precision `0.95033429`、recall `0.68810512`。严格的 `0.97`
目标现在可用：confidence `>= 0.05`、margin `>= 0.84` 得到 871/897 correct，
precision `0.97101449`、recall `0.60235131`。模型还保存仅由 fit 数据计算的 1%/99%
feature envelope。每条 edge 都会报告 outlier count/ratio、reliability tier 和 strict-gate
status。Clean calibration 可用不等于 runtime 可用；噪声与真实 provider 仍是独立 promotion gate。

Relation corpus 现在会把 body 和 floating candidate 输入共享的 degree-one acyclic
selector。Candidate edge 按 confidence 排序；只有由 train calibration 得到的 high-precision、
zero-OOD floating 子集会作为 protected 诊断证据先插入。Selector 会报告
selected/protected edge、outgoing/incoming conflict、self-loop 和 cycle rejection。
它是基于稳定 hashable id 的通用 path-cover primitive，但 corpus 中仍只用于 benchmark。

Corpus scorer 支持确定性 `clean`、`mild` 和 `stress` source perturbation。Mild 使用
0.5% 页面相对 bbox jitter、10% 文本 block fragmentation、3% 图形类型 dropout、1% 元素
dropout 和 5% caption prefix corruption；Stress 分别使用 1.5%、25%、10%、3% 和 15%。
选择由 profile、page uid、element/block id 和 action 的 SHA-256 决定，同一 corpus 每次
获得相同噪声。报告会把保留元素、可解析 label 与 relation accuracy 分开。
这些是受控 sensitivity test，不是真实 OCR error distribution。

`benchmark-provider-anchors` 会把 Docling document、PaddleOCR-VL 1.6 `raw_results`、
ROOR-style page 和通用 `pages/elements` provider 归一到同一 top-left anchor 契约。
Text/caption 匹配允许多条 oracle line 进入同一 provider paragraph；figure/table 改为
全局一对一。无额外依赖的 Hungarian solver 先最大化达到门槛的配对数量，再最大化
总几何得分，并用 dummy column 保留 unmatched anchor。同一 primitive 也替换
structure-role relation fusion 中依赖输入顺序的 caption 抢占逻辑。同一 paragraph 内的
oracle line 仍按几何顺序排列，不使用 JSON list position，避免答案顺序泄漏。

报告包含总体与分类 anchor recall、provider match rate、serialized relation edge、显式
figure/table-caption edge 和可选 trained floating edge。`graphical_relation_audit` 保留官方
raw score，同时用 answer-free 局部几何建议检查 graphical label，报告 exact agreement、
conflict、未解析 label 和 provider 对该诊断建议的一致性。该建议明确不是 ground truth，
不会改写 label 或 `DocumentIR`。Suite 命令会按渲染 Comp-HRDoc manifest 聚合相同计数。

Provider 报告现在包含不读取 relation 答案的 `provider_degradation`。归一化层会把
provider `group_id` 与单个 anchor id 分开保存，因此精确文本行可以重组回同一语义
block，真实段落 provider 也可以保持单一 anchor。只有诊断几何对应会忽略 type；runtime
匹配和 relation 评分仍保持原 kind 约束。诊断会分开八类 LED 风格错误：anchor 级的
missing/hallucination，grouped-unit 级的 size/split/merge/overlap/duplicate，以及
几何匹配 anchor 的 type confusion。Split/merge multiplicity、分 kind 未匹配计数、页归一化
center/edge 误差、IoU/coverage/area ratio、NFC 字符相似度、bag-of-token P/R/F1 和
caption prefix 保留率都可独立审查。

两个文档特有 guard 避免误导性计数。Provider overlap 只记录相对 oracle 新增的重叠，
high-IoU 重复框只留在 duplicate 类。如果 provider text/caption anchor 至少 90% 被 oracle
figure/table 包含，且面积不超过父区域 25%，它会记为 `nested_graphical_content`；整图大小的
figure-to-text 类型丢失仍是 misclassification。这样可以区分有用的图表/图示 OCR 和真正的页面幻检。

每份真实报告还会与同一 oracle 的确定性 clean/mild/stress replay 比较。距离是 12 个归一化
诊断率的无权 RMSE，只有描述意义：它不经拟合、不读取 relation label，也不能晋升 edge
或改变 `runtime_reorder`。Suite 命令会先 micro-aggregate 原始计数和几何/文本 record，再重算
signature。该分解延续 split/merge 导向的文档结构评测传统，也遵循 LED/COTe 对“单纯
IoU 或 mAP 会隐藏不同结构错误”的结论：
https://www.haralick.org/journals/Liang_2001_Computer-Vision-and-Image-Understanding.pdf、
https://arxiv.org/abs/2603.17265 和 https://arxiv.org/abs/2603.12718。

固定 250 页 graphical test corpus 上，全局 structure-role assignment 将 graphical
correct/predicted 先从 `295/342` 提高到 `301/346`。随后独立执行 train-only locality
calibration：取消强制水平 overlap，同时把最大水平中心距离从 `0.50` 收紧到 `0.35`
页宽；纵向 gap 保持 `0.12` 页高。4,102 个 fit 页上，correct/predicted 为
`5284/5658 -> 5291/5665`，没有页面回退；1,073 个独立 calibration 页上为
`1348/1473 -> 1349/1474`，同样没有回退。

两项参数冻结后，未改动的 250 页 test 得到 graphical `306/350/347`，
precision/recall/F1 为 `0.87428571/0.88184438/0.87804878`。整体 structure-role F1
为 `0.85803621`，诊断 joint path-cover F1 为 `0.88575528`。相对仅全局 assignment，
3 个变化页全部改善；相对原始 greedy baseline，graphical correct 净增 11，prediction
增加 8。真实 Docling/Paddle anchor recall 与 raw relation 指标保持不变。

Learned floating decoder 也改为全局 assignment。Train-only operating point 冻结后，
raw graphical test F1 从 `0.91322902` 提高到 `0.91761364`（`323/357/347`），joint
path-cover F1 从 `0.88774602` 提高到 `0.88839440`（`8983/9758/10465`）。唯一变化的
raw 页是 `1507.01067_7`，从 `2/3` 提高到 `4/4`。Clean strict subset 为 `196/201`
（precision `0.97512438`），要求 zero feature outlier 后为 `169/173`（precision
`0.97687861`）。五条 strict raw 错误全部触及独立局部几何审计与官方 label 冲突的
graphical 对象；报告只记录这一事实，不改写 corpus label。

Noise-aware selective calibration 现作为第二层、合取式 abstention。官方 train text block
保留精确 text-line polygon，再复用 corpus benchmark 的确定性 clean/mild/stress 扰动。
四个 document-hash fold 分别训练临时 pair estimator，并且只评分 held-out fit 文档，共生成
15,413 条 correctness record，不使用 pair estimator 的 in-sample prediction。最终 forecaster
是标准化 L2 logistic regression，只使用 12 个 domain-general 特征：pair score、source/target
竞争 score 与 margin、排除当前 edge 后的全局 assignment cardinality/score gap、feature OOD
ratio，以及页面/assignment 规模。它明确排除 raw coordinate、caption text feature、profile
identity 和答案 relation。早期 selector-only 非线性原型在噪声下泛化较差，因此没有保留。

Forecaster 不能绕过原 gate。Noise-aware review 要同时通过 base `confidence >= 0.85`、
`margin >= 0.72` 和 correctness score `>= 0.29`；noise-aware strict 要同时通过 base
`confidence >= 0.05`、`margin >= 0.84` 和 score `>= 0.44`。阈值在 train-derived
calibration view 上最大化 minimum-profile risk coverage，并要求每个 profile 达到 precision
下限。Strict calibration precision/recall 在 clean 为 `0.97272727/0.59197787`、mild 为
`0.97522816/0.51728907`、stress 为 `0.97164461/0.35546335`。四折计数、feature name、
全部 profile 指标与 gate provenance 都写入 manifest。

固定且未改动的 250 页 test 上，noise-aware strict 将 clean 从 `196/201` 收紧到
`192/195`（precision `0.98461538`），mild 从 `169/175` 收紧到 `163/167`
（`0.97604790`），stress 从 `115/123` 收紧到 `109/116`（`0.93965517`）。Clean/mild
错误全部触及已审计 conflict graphical；stress 仍有 6 条错误不在 conflict set。Noise-aware
review 在 clean/mild 保留 235/198 条正确边并各去掉 1 条错误，在 stress 保留全部 133 条
正确边并去掉 2 条错误。其 protected path cover 保持 clean/stress F1，并将 mild F1 从
`0.85764341` 提高到 `0.85784363`。Stress precision 缺口仍然真实，因此 provider 输出仍为
review-only，绝不把 order 写回 `DocumentIR`，`runtime_reorder` 保持 false。下一步需要
真实 provider-noise label 或更强的 domain-shift 模型，而不是继续调整 synthetic/test threshold。

Assignment-confidence 研究支持继续加入 score-gap 与 assignment-stability 特征：
https://doi.org/10.1016/j.patrec.2015.07.010

Calibrated structured prediction 支持用 margin/pseudo-margin 训练独立 correctness forecaster：
https://proceedings.neurips.cc/paper/2015/file/52d2752b150f9c35ccb6869cbf074e48-Paper.pdf

Noise-aware selective calibration 支持这层 train-only rejection，但不能据此声称 test
具有 distribution-free 鲁棒性：https://arxiv.org/abs/2208.12084

Sparse graph segmentation 将文本行与区域建模为双向几何关系，再做 cluster-and-sort；
这是 train-only floating-pair gate 的候选架构：https://arxiv.org/abs/2305.02577

亚像素正 bbox 的 crop 现在使用 floor/ceil 边界，不再把两侧 round 到同一坐标。
这会保留至少一个像素，避免 image-source benchmark 因 `cannot write empty image` 中止。

## Train-Only Provider 校准与快速 Paddle Layout

`fetch_comphrdoc_provider_calibration_corpus()` 使用固定版本的 Comp-HRDoc train
annotation archive 和原始 arXiv PDF 重建小规模真实图像 provider 语料。它只用 annotation
geometry/category 将页面分为 `multicolumn` 或 `graphical-multicolumn`，样本选择不能读取
relation label。文档 id 先按 SHA-256 分区，再选择页面；当前固定拆分包含 3 个 fit 文档和
1 个 calibration 文档。Manifest 记录 annotation revision/hash、source PDF URL/hash、
partition、layout stratum、选择字段和 source license policy。

生成目录明确隔离数据边界：

- `images/` 是 provider 唯一输入。
- `structure/` 是已移除 relation 的 answer-free anchor，只用于 provider/oracle 匹配。
- `semantic/` 保存评测 relation，只由评分器读取。
- `sources/` 只是本地重建缓存；Scriptorium 不重新分发 arXiv PDF。

`benchmark_provider_anchor_suite()` 现在为每个 case 保留 `sample_id`、`partition` 和
`layout_stratum`，并输出每个 partition 的 micro summary 与缺失 provider case。Nested
graphical 诊断使用 oracle group id，不再假定 line id 同时也是 grouped block id；因此
Docling 图内 OCR 的 block/line namespace 重叠不会在 aggregate overlap 分析中触发冲突。

`PaddleLayoutAdapter` 直接以 `PP-DocLayoutV3` 运行 Paddle `LayoutDetection`，跳过 OCR
和 VLM 识别。每个有效 provider box 会归一化为稳定的页内 id、bbox、label、confidence、
raw index 和可选数值 order；连续的数值 order box 会生成序列化 successor edge。Payload
明确声明：

```json
{
  "capabilities": {
    "layout": true,
    "reading_order": true,
    "text_recognition": false
  },
  "semantic_policy": "review-only",
  "order_policy": "review-only",
  "relation_policy": "review-only",
  "runtime_reorder": false
}
```

`run-paddle-layout` 同时接受 PDF 和一等 image source，保留抽样后的原始 source page index，
并记录模型参数、包版本、输入尺寸与输入 SHA-256。使用本地模型目录也不会改变输出契约。
Degradation 评分会消费 capability 声明：layout-only 输出的文本 record 仍可审查，但 text
fidelity 标为不适用，character、token、caption-loss 不进入 profile distance；其余 9 个
layout 诊断仍正常评分。

PaddleOCR-VL 与 PP-Structure payload 现在也带同类可复现 provenance：序列化 pipeline、
adapter 与 prediction option，记录已安装的 `paddleocr`、`paddlex`、`paddlepaddle` 版本和
每个输入 hash，并对凭据特征 key 脱敏。PaddleOCR-VL 本地运行默认 synchronous，同时显式
暴露 `--queued` 与 `--max-new-tokens`，不再隐藏队列调度和生成长度条件。

对于已经渲染且方向正常的 PDF 页，PP-Structure 默认关闭整页方向、文档去畸变和文本行
方向模型，并继续把 table/formula/region 阶段设为可选。旋转、弯曲、拍照、密集表格或公式
页面可通过 CLI 只重新启用所需阶段。CPU compatibility 环境和参数继续写入 provenance。

在答案隔离的固定 8 页语料上，PP-DocLayoutV3 relation F1 在 fit 为 `0.89882353`、
calibration 为 `0.87248322`、overall 为 `0.89198606`；Docling 在同页分别为
`0.88119954`、`0.84415584`、`0.87148936`。PaddleOCR-VL 与轻量 PP-Structure 只运行了
每个 partition 的一个 graphical 页，overall F1 分别为 `0.89510490` 和 `0.88732394`，
这两个两页结果不是完整语料对比。所有 provider 继续保持 review-only。

该架构与 RT-DocLayout/PP-DocLayoutV3 的证据一致：不运行完整 recognition 也能获得有用的
layout 与 reading-order prediction（https://arxiv.org/html/2606.23344）。
https://arxiv.org/html/2607.01018 的 CLM+NSP path-cover 方法也支持“局部 successor 评分后做
max-regret path cover”。Scriptorium 已实现 degree-one、acyclic、max-regret path-cover
selection；在 provider 文本更干净且有更大 held-out calibration set 证明收益之前，暂不接入
昂贵的 semantic next-sentence scorer。当前 8 个 train-only 页不足以支持 runtime 晋升。

`run_paddle_layout_corpus()` 只加载一次 `PP-DocLayoutV3` predictor，再按 manifest 连续运行，
不为每页重复支付模型启动成本。每个输出保存 corpus/sample/input SHA-256 provenance；只有
manifest hash 相同的既有输出才会跳过，`--refresh` 必须显式指定，来自其他 corpus 的旧
JSON 会直接报错而不是静默复用。

Provider benchmark schema v6 将 serialized edge 拆为 `within_anchor`、
`between_anchors` 和 `direct_between_anchors`。一个 Provider paragraph 可以包含多条 oracle
line，因此 within-anchor edge 是 block 内的局部几何/分割证据，不代表模型正确排列了两个
block。归一化过程保留 Provider confidence，并写入 anchor assignment provenance。每条 direct
block transition 会记录 visual Y/X、box-flow、非平凡 recursive XY-cut tree edge，以及
relation-graph max-regret path-cover 真正选中 edge 对同一直接 successor 的 provenance，再输出
完整 support/confidence 曲线和 precision 95% Wilson 下界。Semantic label 只评分已经选出的
curve point；label-invariance 测试保证修改 `ro_linkings` 不会改变 eligible edge。

Comp-HRDoc 的 `ro_linkings` 是 partial label。Transition review v3 因此把阈值选中的边分成
`eligible`、`scorable` 与 `unscored`：只有 source/target 都出现在 relation endpoint universe
时才参与 precision，未标注边不能再被默认当作 false。Gate 同时要求最小
`scorable_fraction`，避免通过隐藏大量不可评分边得到虚假的高 precision。Suite v8 将这些计数
按 case、partition、layout 和 position 聚合；position audit 也使用同一口径。

`freeze_stratified_provider_transition_gate()` 现在写 gate v4。Artifact 把 `candidate_orders` 与
`support_candidate_names` 分开；默认只有 visual Y/X、box-flow 和 selected relation-graph 能计入
支持票。Recursive XY-cut 可用于显式 A/B 和审计，但默认不算独立一票。默认仍预声明
`minimum_native_support = 2`，单 candidate 边直接 abstain；layout×position×confidence rule
只在 fit label 上选择。随后按 `document_id` 的 SHA-256 顺序做 5-fold OOF：每个 fold 的 rule
只能读取其他文档，当前 fold 只评分。Aggregate、每个 fold 与每个 active bucket 都必须分别
满足 precision、Wilson、最小 scorable 数量和 `scorable_fraction`。最后 calibration 不能修改
rule，只能接受或否决。`cross_validation_folds = 0` 仅保留给 legacy diagnostic。

Artifact 固定 source report/corpus SHA-256、可观测/支持 candidate 集合、document fold、criteria
和逐项 criterion result，并始终保持 `runtime_reorder: false`。Train 与独立评估都会根据
`native_supporting_candidates` 重算支持数；v4 在需要过滤却缺少该 provenance 时 fail closed，
旧 v2/v3 artifact 则保留原始计数。只有 CV 与 calibration 同时通过，
`benchmark_provider_anchor_suite()` 才允许在另一语料加载该 gate；test 期间不会搜索 threshold。

`fetch_comphrdoc_provider_test_corpus()` 对官方 test annotation 使用独立的确定性 document/page
hash namespace。Calibration 与 test 共用 materializer，但 manifest 分别记录 official split、
选择字段、source revision 与答案边界。两个 fetcher 都支持 `--annotation-archive`；本地 130 MB
archive 仍必须匹配
`530f482b75523a80fe1b0a7480fd8273c44f9239e0189650a4841c0aae61d03d`。

同一已打开 32 页 test window 的 partial-label-aware v1 audit 为 `256/268`，另有 16 条
`unscored`；旧 `209/219` 是 partial-label-unaware 结果，已撤回为当前指标。页首为
`72/78`、Wilson `0.84216770`，页中为 `91/92`、Wilson `0.94097214`，页末为
`93/98`、Wilson `0.88607548`，所以只能继续否决 promotion。

扩大到 64 页 train-only suite 后，v4 默认三通道精确复现 25-document OOF aggregate
`192/195`，但 fold 2/3
的 Wilson 都只有 `0.83805895`；`graphical-multicolumn/middle` 只有 18 条，
`multicolumn/start` 只有 `8/9` 且 scorable fraction `0.75`。7 个 calibration 文档最终只保留
`21/21`，Wilson `0.84536098` 且未达到 30 条。显式加入 recursive XY-cut 后，OOF 变为
`173/176`，calibration 变为 `23/24`、Wilson `0.79758194`；新增错误是一条 visual Y/X 与
XY-cut 共同支持的边。Visual Y/X + box-flow 的 support-2 对照没有任何 fit bucket 达标。因此
gate 明确为 `document-cross-validation-rejected-review-only`，没有打开新的 test window。

`chunkr_benchmark.py` 增加第二个跨领域 block-level order 开发面。获取过程固定上游 Hugging
Face revision 和 1.9 MB COCO annotation 的 SHA-256；纯几何候选评分不需要下载 source
image。校验要求 annotation 全局唯一、每页至少一个有效且不越界的 bbox、category 已知，且
每页 id 连续递增。Id 定义发布顺序，但不会进入 candidate input order；category+bbox
fingerprint 会生成确定性、answer-free 的输入 permutation。

报告把完整 serialized order 与真正 evidence edge 分开。Order 指标包含 exact page match、
position accuracy、pairwise accuracy / Kendall tau 和 adjacent successor accuracy，并分别统计
all、non-trivial 与 10+ element 页面。Edge 指标分别评分 visual/box-flow adjacency、只有
非平凡 split 的 recursive XY-cut tree edge，以及 relation-graph path-cover 真正选中的 edge。
稳定三通道和全部四通道 support curve 独立报告，同时保留逐 domain 与逐 page 诊断。
Artifact 始终为 `development-benchmark-only`、`runtime_reorder: false`。

第一次全量运行在出分前发现 duplicate assignment：部分右侧 grid cell 同时属于 grid-island
token 与 sidebar secondary flow。Mixed island ordering 现在会把 island-owned item 排除在
artifact/sidebar/footnote 分类之外，每个输入元素只得到一个 assignment；固定 16-box 回归覆盖
该冲突。下一种 learned candidate 可以把 Chunkr 用于 development/cross-validation，但 runtime
或 promotion 仍必须依赖答案隔离的外部语料。

`chunkr_order_ranker.py` 实现了该 development candidate，但不进入 converter。训练从 9,267
个 block 生成 223,634 个有向 pair。68 个特征包含 source/target 归一化 geometry、overlap 与
方向、页面元素数、五种 answer-free geometry candidate 的 rank/direction/adjacency，以及
source/target 分离的 role one-hot。`HistGradientBoostingClassifier` 对每个无序 pair 的两个方向
分别打分，再将概率反对称化，通过 Borda score 生成 permutation；visual Y/X 只用于确定性的
同分 tie-break。默认继续使用 uniform pair weight：测试过的 focal 版本约耗时 `333 s`，而
uniform 约 `43 s`，且仍未恢复 selected-order successor accuracy。

Cross-validation 按 category/complexity 分层，并在 page scope 使用 SHA-256 分配。Feature
构造和 fold assignment 都不读取 annotation id；测试会反转答案 id，要求 feature/fold 完全
相同而 label 改变。由于 Chunkr 没有 document identifier，报告明确声明 page OOF 和
`test_split_claimed: false`。每个 fold 都同时保留 learned 与 baseline metrics，而不是只输出
aggregate。

训练会写入 model、相邻 manifest 与相邻 OOF report。加载默认 fail closed：schema、
review-only 状态、isolation policy、model filename/hash、OOF filename/hash、feature/role 契约、
report-to-model hash 必须全部一致，之后才反序列化 joblib；joblib artifact 仍只允许本地可信
来源。Prediction 会递归拒绝 successor、precedence、stream、显式 order、semantic-order 等
答案字段；id 和 role/bbox fingerprint 必须唯一，单页最多 256 个元素。输出始终是
`runtime_reorder: false` 的 review-only successor evidence。

Feature-level 1%/99% envelope 没有发现 ROOR transfer failure，模型即使高 confidence 也会
出错。因此新增 page profile，记录 element count、bbox width/height/area quantile、aspect、
role entropy/ratio，以及 selected/XY/relation candidate disagreement。诊断会输出每个 profile
数值及其越过的 lower/upper bound。ROOR replay 还会把 manifest path 限制在 corpus 内，并
严格分两阶段执行：先预测全部 structure 页面，再打开 semantic sidecar 评分。测试直接断言
事件顺序并拒绝 path traversal。

Chunkr OOF 的 exact/pairwise 从 selected `0.61255116/0.87452713` 提高到
`0.70259209/0.93686112`，但 successor 从 `0.75041012` 降到 `0.74349660`。在 49 个 ROOR
line-level 页面上，direct recall 只有 `0.19142420`，低于 selected `0.46592649`；precedence
为 `0.77067381`，selected 为 `0.83192956`，且所有页面都在 coarse-block page profile 之外。
这个 OOD rule 是观察该窗口后加入的，所以只是诊断，不是独立校准的 gate。架构结论是把
coarse-block 与 text-line ordering 分成两个层级：先推断/接受 block membership，再排序 block；
block 内的 line successor 保持原顺序或由独立 line-level 模型预测。

## 分层 Block/Line Proposal

`hierarchical_order_adapter.py` 已把分层原型接到真实 `DocumentIR`，并可读取归一化后的
PP-Structure、PaddleOCR-VL、OpenDataLoader、Surya 或 Docling 结构 JSON：

```bash
scriptorium build-hierarchical-order path/to/document.ir.json \
  --structure-json path/to/provider.json \
  --page-index 0 \
  --output outputs/hierarchical-order.proposal.json
```

Adapter 会显式声明 `element_granularity: fine` 与 `region_granularity: coarse`。Fine 层只接受
PDF 或 image-source IR 中可见且有文本的 element；源视觉层及空 shape/image anchor 会被排除。
Coarse filter 接受 provider block list、layout detector block，以及 Docling body/furniture leaf；
Paddle OCR 行、table cell、通用 `document` reference 和 reading-order sidecar 都会被拒绝并记录
原因。Provider sequence 数值会在构建 proposal 前删除，provider relation 数组完全不参与
adapter。改变 `block_order` 或 review successor edge 不会改变适配后的 element/region geometry。

结构去重现在区分粒度。同文本、同几何的 coarse `parsing_res_list` block 与 fine OCR line 会作为
两份证据保留；只有同一粒度类内部才会竞争去重。这样结构应用仍可使用精确 OCR anchor，而
hierarchy membership 不会丢失 parent block。

Membership 按证据强度 fail closed：

1. 兼容的显式 provider parent reference 至少需要 `0.5` element coverage。
2. 精确或包含式的字母数字文本只在轴向对齐、line-relative gap 有界、score `>= 0.74` 且
   competitor margin `>= 0.08` 时修复局部坐标漂移；当纯几何 parent 的文本与 element 冲突时，
   该证据也可以纠正归属。
3. 其余 element 使用 geometry coverage `>= 0.8` 和 runner-up margin `>= 0.1`；平局继续保持
   unassigned。

Block 内始终保留 answer-free selected local line order。默认 cross-region 层不再把 selected
coarse geometry 强制串成一条 adjacency chain。一次 fine relation-graph 推理会同时返回序列化的
诊断 order 和真正选中的 edge diagnostics；coarse region 只按 member completion rank 列出，供
审查使用。跨越两个已分配 region 的 selected edge 都会保留为 evidence；只有从 source stream
尾部连到 target stream 头部的 edge 才能成为 review transition。Region 级 degree-one 检查与
cycle suppression 保证这些 transition 仍是 partial DAG；未对齐边界及被抑制的 edge 会保留在
`cross_region_relation_evidence`，并带明确原因。

可选 Chunkr block model 继续作为显式的同粒度 A/B 路径；模型页落在 OOD 时仍抑制全部模型
transition。只有 selected relation edge 恰好形成完整 coarse chain，且所有 fine element 都已
分配时才允许 candidate expansion。所有局部边和 transition 仍只进入未接受的
`ScriptoriumReadingOrderSidecar` proposal，`total_order_asserted` 与 `runtime_reorder` 都保持
`false`。

首轮真实页面 geometry-only 与 text-plus-geometry 无标签审计如下：

| 页面/provider | Fine elements | Coarse regions | Assigned | Unassigned | Non-empty regions | Eligible cross-region transitions |
|---|---:|---:|---:|---:|---:|---:|
| Attention 第 1 页 / PP-Structure | 56 | 9 | 47 -> 52 | 9 -> 4 | 6 -> 9 | 1 -> 6 |
| 比亚迪年报第 136 页 / PP-Structure | 34 | 17 | 29 -> 33 | 5 -> 1 | 11 -> 15 | 7 -> 13 |
| JD image source / Docling | 64 | 93 | 49 -> 53 | 15 -> 11 | 31 -> 37 | 16 -> 20 |

这些数字只衡量 membership coverage，不是 semantic accuracy。JD Paddle replay 通过显式 id
解析了 `64/64` 个文本 anchor，但 54 个空 region boundary 仍会阻止页面 permutation。Attention
的 OpenDataLoader 路径解析 `56/56`，形成完整 21-transition review chain；没有独立标签时它也
不是 promotion 证据。下一道 gate 必须在未打开的年报、门户或 line-level 文档家族中，分别给
within-region successor 与 cross-region transition 评分。

该架构遵循 PAGE 的 region/line 嵌套和 ordered/unordered group 模型，也遵循
Detect-Order-Construct 的 coarse-to-fine 分解。复杂布局继续表示为局部 DAG relation，不强制成
一个全局 permutation：

- PAGE `TextRegion` / `TextLine`：https://ocr-d.de/en/gt-guidelines/pagexml/pagecontent_xsd_Complex_Type_pc_TextRegionType.html
- OCR-D PAGE reading order：https://ocr-d.de/en/gt-guidelines/trans/lyLeserichtung.html
- Detect-Order-Construct：https://arxiv.org/abs/2401.11874
- DLAFormer coarse-to-fine layout analysis：https://arxiv.org/abs/2405.11757
- Visually-rich document ordering relations：https://aclanthology.org/2024.emnlp-main.540/
- XY-Cut++ multi-granularity/cross-modal ordering：https://arxiv.org/abs/2504.10258
- GraphDoc relation graph（MIT，release 仍有 TODO）：https://github.com/yufanchen96/GraphDoc

### Hierarchy Relation-DAG 契约

`infer_relation_graph_order_evidence()` 会暴露一个不可变的
`RelationGraphOrderEvidence`，其中同时包含序列化 candidate index，以及每条进入 max-regret
path cover 的 edge 在选择时的 diagnostics。Hierarchy 同时需要两种视图时，不必重复构造二次
复杂度的 candidate graph；原有 order-only 与 selected-edge API 都委托给同一 primitive。

`hierarchical_order.py` 会用预测 membership 映射每条 fine selected edge。跨区域 candidate
保留 score、alternative、margin、selection regret、selection step 和 tie 状态。Boundary
candidate 按 untied、regret、score 和稳定 id 排序；selector 强制每个 region 最多一个 outgoing /
incoming edge，并拒绝会闭合 region cycle 的 edge。选中的记录进入 `review_transitions`；所有
未对齐边界或被拒绝的记录都留在 `cross_region_relation_evidence`，并标记
`not-local-stream-boundary`、degree conflict 或 cycle provenance。这样 abstention 可以审计，
不会通过序列化 path head 被静默掩盖。

答案隔离的 Comp-HRDoc hierarchy corpus 使用独立且带 hash 的 `inputs/` 与 `labels/`。
Materialization 会先读取全部 structure 页，再打开任何 semantic sidecar；evaluation 会先预测
全部 input，再解析任何 label path。测试会修改 relation label 并确认 input 不变、反转 source
input order、追踪两个读取阶段、拒绝 path traversal/hash tampering，并强制构造三 region cycle
验证 suppression。

另有一次非迭代 membership refinement，它只处理 `ambiguous-region-overlap`：无同分替代的
relation 前后邻居与 selected-order 前后邻居必须同时指向同一个 region，而且该 region 必须仍在
原始 geometry tie 内。修复后的 member 不会继续传播。本轮
`relation-base-continuity-parent` 共解除 8 个 membership（5 个 fit、3 个 calibration），全部
正确；在 4 个真实 provider replay 中触发数均为 0。

同一次 pass 还有独立的 boundary 分支。Relation 邻居与 selected-order 邻居必须按位置同时指向
两个不同 region，形成相同的 `A -> element -> B` 模式。Element 的 compact text 至少包含
`MIN_EXACT_TEXT_PARENT_CHARACTERS` 个字符，geometry tie 内必须只有一个 region 包含该文本，
而且该 region 必须是 `A` 或 `B`。方法名为 `relation-base-boundary-text-parent`，evidence 会记录
relation/base split 和 unique tied-region text containment。它又解除 13 个 membership（6 个
fit、7 个 calibration），全部正确。该分支读取原始 membership map，所以 boundary 与 interior
repair 都不能继续传播。

在 64 个 train-only 页面上，membership 达到 `5244/5257 = 0.99752711`，错误分配为 0，
unassigned 为 13；within-region F1 达到 `0.99297033`，fit/calibration 分别为
`0.99191794/0.99642675`。Line cross-region F1 达到 `0.93473962`，region-transition F1 达到
`0.90607029`。Calibration line/region F1 为 `0.92260062/0.89759036`：region 已超过 flat
control `0.88563050`，但 line 仍低于 `0.92879257`，因此 runtime 继续关闭。报告汇总 972 条
cross-region evidence、905 条 boundary candidate、67 条 non-boundary record、9 条 tied edge、
3 次 cycle suppression 和 902 条 emitted transition。两种 refinement 在当前 4 个真实 provider
replay 中都触发 0 次。本轮没有打开新的官方 test window。该 revision 的全仓测试为 379 项全部
通过。

两个被否决的 control 划定了当前设计边界。按所有 non-boundary relation edge 切分 local stream
会把 fit line F1 提高到 `0.94176373`，但 within F1 降到 `0.98873592`，删除 25 条正确 local
edge，并产生 1 个环。只在 flat order 也同意时加入 non-boundary relation edge，会使 partial line
F1 达到 `0.93891213`，但 region F1 降到 `0.89402390`；partial line label 还让 29 条新增边中的
22 条无法评分。因此 non-boundary record 继续只作 evidence，不能成为 translation handoff。

对翻译而言，每个已接受 coarse membership 仍定义一个有界 local stream。Partial DAG 以后可以
在这些 stream 之间表达 handoff，而不要求一个 page permutation。Non-boundary relation evidence
不能驱动 replacement 或 reflow；只有 structure provider、semantic model 或人工确认边界后才能
使用。这样既保留稳定的 translation fitting unit，也避免错误的全局顺序。
