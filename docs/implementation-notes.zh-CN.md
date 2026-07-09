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
- 对图片 source，如果没有单独提供 `--ocr-json`，`--structure-json` 也可以先生成初始文本锚点。常见 `parsing_res_list` / `block_bbox` / `block_content`，以及 ROOR 风格 `document` segment 的 `box` / `text`，都会被归一成 `native-ocr` 文本节点，再由结构 evidence 反向融合标签、顺序和置信度。
- `DocumentIR.metadata.semantic_layer` 会记录当前语义层驱动。图片 case 会报告 `structure-json`、`ocr-json`、`ocr-fallback` 或 `visual-only`；原生 PDF case 报告 `native-pdf`，结构 JSON 默认作为增强证据。
- 原生 PDF 提取提供 `image-only` OCR fallback：当页面没有原生文字且图像覆盖面积很高时，生成透明的 `native-ocr` 可编辑锚点，同时保留原始图像元素。
- `structure_evidence.py` 能解析嵌套 `res`、`raw_results`、`pages`、`parsing_res_list`、`document`、`layout_det_res.boxes` 形状，也能解析 Docling `body.children`、`furniture.children`、`prov` bbox/page 证据和上下坐标原点差异，并把 ROOR 风格 `ro_linkings` 当作 successor edges。

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
- 简单 drawing 输出为 SVG line/path；密集矢量图可以局部 raster fallback，但仍然保留 bbox/source metadata。

这是一个明确的保真度和可编辑性权衡：普通文本、表格、分隔线、简单 drawing 和支持的 SVG path 尽量保持结构化；无法可靠结构化的复杂图形先以局部 raster 节点保真。

## 外部结构证据融合

PaddleOCR-VL、PP-StructureV3 和 Docling 应该作为可选证据提供者，而不是替换原生 PDF 提取。数字 PDF 的 native extraction 通常更适合保留字体、样式和 bbox；模型输出更适合补充 OCR、layout label、table/formula/chart region 和阅读顺序预测。

`src/scriptorium/structure_evidence.py` 当前提供：

- `normalize_structure_evidence(payload, document)`: 接受 Paddle 风格 JSON，包括带 `block_bbox`、`block_label`、`block_content`、`block_order` 的 `parsing_res_list`。
- 显式 block order 仍是最强顺序证据。`parsing_res_list`、`blocks` 或 `elements` 没有 `block_order` 时，列表位置会记录为较弱的 `implicit-list` 顺序；嵌套的 `children`、`sub_blocks`、`sub_regions`、`items`、`cells` 等子列表会按 depth-first 顺序遍历。纯 `layout_det_res.boxes` 检测框不会获得隐式顺序。
- 接受 DoclingDocument JSON，遍历 `body.children`，解析 `texts`、`tables`、`pictures`、`key_value_items`、`groups` 引用，并把 `prov` 转成页面局部结构区域。Docling `furniture.children` 也会作为非正文结构证据解析，用于 page header/footer 等 artifact 的角色和 stream 标注，但不生成正文 block-order 证据。
- 支持 PDF-point bbox、pixel bbox、top-left 和 bottom-left 坐标原点。
- `apply_structure_evidence(document, payload)`: 通过 bbox coverage 和文本相似度把模型区域对齐到原生元素。
- 当父区域和更具体的子区域同样覆盖某个文本时，会优先选择面积更小的子区域。这样嵌套 card/product/tile 结构可以驱动局部 reading stream，而不会被父级 grid bbox 吞掉。
- 匹配元素会获得 `structure_evidence`、`external_structure_label`、`external_structure_order` 和 `external_structure_order_source` 元数据。
- 结构 JSON 也可以通过 page-level 或 stream-level 的 `successor_edges`、`successor_relations`、`ro_linkings`、`reading_order_edges`、`reading_order_relations`、`reading_order_linkings`、`precedence_edges`、`order_edges`、`relations`、`reading_streams`、`streams` 提供关系证据。端点可以引用已匹配结构节点的 id/ref、OCR anchor 原始 id/ref，也可以直接用文本。解析成功的边会写到源元素的 `external_structure_successor_ids` 和 `external_structure_precedence_target_ids`。
- `reading_streams` / `streams` 不再只作为关系边来源，也会写入 stream 元数据。Stream 成员可以来自 text sequence、member list，也可以来自 stream-local 的 `ro_linkings`、`reading_order_linkings` 等关系别名。解析成功的成员会获得 `external_structure_stream_*` 以及 `reading_order_stream_id`、`reading_order_stream_type`、`reading_order_stream_index`，因此 OCR/image 页面即使结构 JSON 没有 region bbox，也能暴露面向翻译的正文、边栏、表格、卡片/网格等局部流。
- 只有关系边、没有显式 `reading_streams` 的 sidecar 现在也会自动派生翻译局部流。页面提供 `successor_edges`、`ro_linkings` 或等价 reading-order relations 时，Scriptorium 会从安全的 degree-constrained successor chains 生成 `external-relation-*` streams。显式外部 stream 优先：已有 `external_structure_stream_id` 的元素不会被 relation-derived stream 覆盖。
- 当解析后的 successor/precedence 边能形成安全的无环 path-cover 顺序时，可用 `external-structure-relation-fusion-v1` 重排文本阅读顺序。这样 image source 和复杂页面的语义层可以由 OCR/结构 JSON 的局部关系主导，而不是依赖一个容易歧义的全局 block permutation。
- 如果没有可用关系顺序，但页面至少匹配两个外部 block order，仍可用 `external-structure-fusion-v1` 重排。Benchmark 会输出 `structure_evidence_order_source_counts`，用于区分显式模型顺序、Docling body-tree 顺序、隐式列表顺序和无序检测框区域。
- 外部 label 还会进入 reading-order scope 和 stream metadata：header/footer/page-number 会成为 page artifact，footnote/sidebar 会成为局部 secondary stream，caption 会成为 caption stream，table 会成为 table-island stream，明确的 card/grid/product/tile 类区域会成为 `grid-island` 翻译/编辑流。普通 `list` 只作为列表角色证据，不自动提升为 grid stream，避免新闻/排行列表误标为卡片网格。

A/B 路径示例：

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark-structure-ab input.pdf --structure-json paddle.json --out-dir outputs/structure-ab
scriptorium benchmark page.png --input-kind image --structure-json page.structure.json --out-dir outputs/page-image
```

`benchmark-structure-ab` 会同时写出 `native-only/benchmark_report.json`、`native-plus-structure/benchmark_report.json`、`structure_ab_report.json` 和 `structure_ab_summary.csv`。A/B 报告会比较 visual similarity、reading-order risk、`grid_island_element_count`、结构区域/匹配/重排数、page/stream `needs-structure-evidence` 推荐数、review 推荐数、successor-disagreement 数，以及有 sidecar 时的 semantic successor 指标。

这个设计和阅读顺序研究方向保持一致：LayoutReader / ReadingBank 把 reading order 当作文档理解的一等任务；ROOR 把 reading order 建模为 layout element 之间的关系；新的 graph/path-cover 工作也把复杂页面视为多条 successor chain，而不是一个脆弱的视觉扫描序列。Scriptorium 不把模型运行时绑进核心路径，但接收同形态证据：局部 successor edges、precedence edges 和 stream memberships。

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

报告会记录 `source`、兼容列 `source_pdf`、`source_type_counts`、`input_kind`、`image_dpi`、`semantic_layer_driver`、`semantic_layer_payload_kind`、`semantic_layer_structure_role`、`structure_evidence_relation_reordered_page_count`、`structure_evidence_order_reordered_page_count`、`structure_evidence_stream_count`、`structure_evidence_resolved_stream_member_count`、`structure_evidence_relation_stream_count` 和 `structure_evidence_resolved_relation_stream_member_count`，用于区分 PDF case 与 image case、复现图片像素到 PDF point 的坐标映射，并确认语义层来自 native PDF、结构 JSON、OCR JSON、OCR fallback 还是仅有 visual layer，以及结构重排是由关系边还是 block order 驱动、结构流是否真正解析到元素。

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

翻译回渲染路径还会输出 fidelity replacement 风险指标：`fidelity_replacement_element_count`、`fidelity_replacement_overflow_count`、`fidelity_replacement_conflict_count`、`fidelity_replacement_conflict_target_count`、`fidelity_replacement_min_fit_scale`、`fidelity_replacement_mean_fit_scale` 和 `fidelity_replacement_policy_counts`。源文档无 replacement 时这些字段为 0/`null`；翻译写入 `translated_text` 后，它们用于衡量像素相似度之外的遮罩、压缩和邻近冲突风险。

同一套风险也会按局部 reading stream 聚合：`fidelity_replacement_stream_diagnostics`、`fidelity_replacement_stream_type_counts`、`fidelity_replacement_stream_type_overflow_counts`、`fidelity_replacement_stream_type_conflict_counts`、`fidelity_replacement_stream_id_counts`、`fidelity_replacement_stream_id_overflow_counts` 和 `fidelity_replacement_stream_id_conflict_counts`。这些字段用于判断翻译冲突集中在正文、卡片网格、边栏、脚注、表格岛还是页边 artifact。

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

同一个评测器也接受 ROOR/结构 JSON 风格的 page payload。Sidecar 可以在 page 级提供带 id 和文本的 `document`、`elements`、`blocks`、`parsing_res_list` 或 `layout_det_res.boxes`，再用 `ro_linkings`、`reading_order_edges`、`reading_order_relations` 或 `reading_order_linkings` 引用这些 id：

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

`successor_edges` 和 ROOR 风格 linkings 会评估 labelled 节点的相邻后继关系，`precedence_edges` 只要求 source 在 target 之前。通用 `relations` 列表也可以使用，但每个 item 必须显式声明 successor 或 precedence 的 type/kind。关系端点可以是文本、数组，也可以是使用 `source` / `target`、`from` / `to`、`head` / `tail`、`source_id` / `target_id` 等别名的字典；id 会先通过 page label map 解析成文本再评分。只有关系标签、没有 `text_sequence` 的页面会默认按 `ordered-subsequence` 处理，不因为未标注正文而扣 sequence 分。报告会输出 `semantic_relation_successor_accuracy`、`semantic_relation_precedence_accuracy`、relation missing text counts、每个候选的 relation-edge 指标，以及 `semantic_candidate_relation_successor_delta`；当 sequence 分数持平但 relation edge 变好时，候选仲裁也可以给出 `consider-<candidate>`。

Stream sidecar 使用同一套形状。`text_sequence`、`sequence` 或 `texts` 会被视为有序序列，并生成 stream-local successor/precedence 检查。`members`、`elements`、`items`、`children` 只用于声明 stream label 和 missing/coverage 诊断，不会单独暗示顺序；stream-local 的 `ro_linkings`、`reading_order_*` 或 typed `relations` 才提供显式顺序约束。

Stream sidecar 现在还会单独评估 IR 的流归属质量。`semantic_stream_successor_accuracy` / `semantic_stream_precedence_accuracy` 只看局部顺序约束；`semantic_stream_assignment_id_accuracy`、`semantic_stream_assignment_type_accuracy` 以及对应的 label/found/missing/correct count 字段，会把 sidecar 的 stream membership 和当前元素的 `reading_order_stream_id`、归一化后的 `reading_order_stream_type` 对比。这样 benchmark 可以判断 OCR/结构 JSON 是否真的把 image/source 文本分进了正确的正文、边栏、表格、caption、脚注或卡片网格翻译流，而不是只得到一个看似可用的全局顺序。候选顺序不会参与 assignment 评分，因为 stream membership/type 是语义层抽取结果，不是候选排序列表本身。

Semantic sidecar 现在也会给 `structure_relation` 候选打分，并与 visual-yx、box-flow、relation-graph、successor-consensus、external-structure 一起输出候选指标。这样可以观察 page-scope 和 caption-target 结构是否改善 local successor edge，而不把单个无标签样本直接升级成 runtime 规则。

## 研究参考

- PyMuPDF 文档说明 PDF 文本不一定是自然阅读顺序，并提供排序辅助: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- W3C PDF3/PDF14/PDF4 将复杂布局、页眉页脚、footnote、side-bar 视为需要显式阅读顺序修复的 PDF 场景。
- OCR-D PAGE reading-order 指南把 print space 外的 marginalia 排在 primary text/footnote 之后。
- pdfminer.six `LAParams.boxes_flow` 是 horizontal-vs-vertical box ordering 的经典启发式。
- LayoutReader / ReadingBank 把阅读顺序作为文档理解的一等任务。
- Docling rule-based reading order 使用 above/below adjacency 和 horizontal overlap 几何。
- Relation-based reading-order 和 graph/path-cover 方法提示应关注局部 successor edge，而不只看全局 y/x 排序。
