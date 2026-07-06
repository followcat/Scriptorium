<p align="center">
  <a href="../README.md"><img alt="返回首页" src="https://img.shields.io/badge/%E8%BF%94%E5%9B%9E%E9%A6%96%E9%A1%B5-README-2b6cb0"></a>
  <img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D-blue">
  <a href="implementation-notes.md"><img alt="English" src="https://img.shields.io/badge/English-Read-2f855a"></a>
</p>

# 实现说明

## OCR 后端边界

核心 pipeline 只消费归一化 JSON，并把它转换为 `DocumentIR`。这是刻意设计的边界：

- PaddleOCR-VL 1.6 的官方示例使用 `from paddleocr import PaddleOCRVL`、创建 `PaddleOCRVL(pipeline_version="v1.6")`、运行 `pipeline.predict(...)`，再用 `save_to_json(...)` 保存结果。
- PP-StructureV3 文档将其定位为文档解析结构化 JSON/Markdown 输出，坐标和版面结构比纯 VLM 结果更细。
- 因此 Scriptorium 把 Paddle、Docling、PP-Structure 输出当作 OCR/结构适配器输入；渲染、几何、IR、HTML 导出、编辑、翻译和质量比较不依赖具体模型运行时。

当前状态：

- `--ocr-json` 是稳定测试入口，适合转换质量工作。
- `PaddleOcrAdapter` 隔离在 `scriptorium.ocr`，并且延迟导入 `paddleocr`。
- `--structure-json` 是真实模型输出的轻量桥接入口，支持 PaddleOCR-VL / PP-StructureV3 风格 JSON 和 DoclingDocument JSON。
- 原生 PDF 提取提供 `image-only` OCR fallback：当页面没有原生文字且图像覆盖面积很高时，生成透明的 `native-ocr` 可编辑锚点，同时保留原始图像元素。
- `structure_evidence.py` 能解析嵌套 `res`、`raw_results`、`pages`、`parsing_res_list`、`layout_det_res.boxes` 形状，也能解析 Docling `body.children`、`prov` bbox/page 证据和上下坐标原点差异。

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
  data-scriptorium-reading-order-confidence="0.83"
  data-scriptorium-reading-order-evidence="recursive-xy-cut,horizontal-whitespace-cut"
  data-scriptorium-edit-target="edited_text"
  data-bbox-pdf="76.99,212.49,117.83,224.22"
  contenteditable="true"
>
  PDF text
</div>
```

`structured` 模式不会放整页背景图。输出由可编辑文本节点、结构 shape 节点、原生 image 节点和局部 raster fallback 节点组成，每个节点都能追溯到 IR 中的识别证据。

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
- `--html-mode auto --fidelity-background auto` 会比较 structured redraw、SVG fidelity 和 raster fidelity，并保留更高分候选。
- 打印后的 PDF page box 会归一到源 PDF 尺寸，避免 Chromium A4 1px 量化误差污染视觉指标。
- 简单 drawing 输出为 SVG line/path；密集矢量图可以局部 raster fallback，但仍然保留 bbox/source metadata。

这是一个明确的保真度和可编辑性权衡：普通文本、表格、分隔线、简单 drawing 和支持的 SVG path 尽量保持结构化；无法可靠结构化的复杂图形先以局部 raster 节点保真。

## 外部结构证据融合

PaddleOCR-VL、PP-StructureV3 和 Docling 应该作为可选证据提供者，而不是替换原生 PDF 提取。数字 PDF 的 native extraction 通常更适合保留字体、样式和 bbox；模型输出更适合补充 OCR、layout label、table/formula/chart region 和阅读顺序预测。

`src/scriptorium/structure_evidence.py` 当前提供：

- `normalize_structure_evidence(payload, document)`: 接受 Paddle 风格 JSON，包括带 `block_bbox`、`block_label`、`block_content`、`block_order` 的 `parsing_res_list`。
- 接受 DoclingDocument JSON，遍历 `body.children`，解析 `texts`、`tables`、`pictures`、`key_value_items`、`groups` 引用，并把 `prov` 转成页面局部结构区域。
- 支持 PDF-point bbox、pixel bbox、top-left 和 bottom-left 坐标原点。
- `apply_structure_evidence(document, payload)`: 通过 bbox coverage 和文本相似度把模型区域对齐到原生元素。
- 匹配元素会获得 `structure_evidence`、`external_structure_label`、`external_structure_order` 元数据。
- 当一个页面至少匹配两个外部 block order 时，可用 `external-structure-fusion-v1` 重排文本阅读顺序。

A/B 路径示例：

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
```

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
- 页眉页脚、脚注、边栏/旁注会标记为 `page-artifact`、`footnote`、`sidebar`，保持可编辑但不干扰主体叙事流。
- caption-flow 识别以 `Figure`、`Fig.`、`Table`、`Algorithm` 开头的浅 caption 证据，并区分栏内 caption 和跨 gutter caption。
- `reading_order_caption_target_*` 是局部 figure/table caption 关联证据。annotation 层会把符合类型的 caption 与附近 native image、local raster region 或推断出的 figure/table layout region 关联，要求 bbox proximity 和水平对齐；接近整页的截图/扫描背景会被排除，避免 image-only OCR 页面把整页背景误当成 figure target。

Caption target matching 当前只添加证据，不直接改变 semantic order。caption 节点会获得 `caption-target-proximity`、`<kind>-target` 和位置证据，并通过 HTML `data-scriptorium-caption-target-*` 属性导出；后续可以把它作为 relation graph / successor consensus 仲裁的一项独立结构证据。

Benchmark 会输出 pairwise order accuracy、successor-edge accuracy、sequence similarity、候选顺序分歧、selected-edge support、edge coverage、conflicted-edge ratio、page-level recommendation 和 reading-order risk。后续优化不应对单个 PDF 调阈值，而应通过 semantic sidecar、外部结构证据和候选仲裁证明泛化能力提升。

Semantic sidecar 除了 `text_sequence`，现在还支持关系式标签：

```json
{
  "pages": [
    {
      "page_index": 0,
      "successor_edges": [["Article title", "First body line"]],
      "precedence_edges": [
        {"source": "Sidebar heading", "target": "Sidebar detail"},
        {"from": "Figure 1.", "to": "The caption continues."}
      ]
    }
  ]
}
```

`successor_edges` 评估 labelled 节点的相邻后继关系，`precedence_edges` 只要求 source 在 target 之前。只有关系标签、没有 `text_sequence` 的页面会默认按 `ordered-subsequence` 处理，不因为未标注正文而扣 sequence 分。报告会输出 `semantic_relation_successor_accuracy`、`semantic_relation_precedence_accuracy`、relation missing text counts、每个候选的 relation-edge 指标，以及 `semantic_candidate_relation_successor_delta`；当 sequence 分数持平但 relation edge 变好时，候选仲裁也可以给出 `consider-<candidate>`。

Semantic sidecar 现在也会给 `structure_relation` 候选打分，并与 visual-yx、box-flow、relation-graph、successor-consensus、external-structure 一起输出候选指标。这样可以观察 page-scope 和 caption-target 结构是否改善 local successor edge，而不把单个无标签样本直接升级成 runtime 规则。

## 研究参考

- PyMuPDF 文档说明 PDF 文本不一定是自然阅读顺序，并提供排序辅助: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- W3C PDF3/PDF14/PDF4 将复杂布局、页眉页脚、footnote、side-bar 视为需要显式阅读顺序修复的 PDF 场景。
- OCR-D PAGE reading-order 指南把 print space 外的 marginalia 排在 primary text/footnote 之后。
- pdfminer.six `LAParams.boxes_flow` 是 horizontal-vs-vertical box ordering 的经典启发式。
- LayoutReader / ReadingBank 把阅读顺序作为文档理解的一等任务。
- Docling rule-based reading order 使用 above/below adjacency 和 horizontal overlap 几何。
- Relation-based reading-order 和 graph/path-cover 方法提示应关注局部 successor edge，而不只看全局 y/x 排序。
