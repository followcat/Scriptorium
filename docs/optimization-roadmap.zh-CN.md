<p align="center">
  <a href="../README.md"><img alt="返回首页" src="https://img.shields.io/badge/%E8%BF%94%E5%9B%9E%E9%A6%96%E9%A1%B5-README-2b6cb0"></a>
  <img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D-blue">
  <a href="optimization-roadmap.md"><img alt="English" src="https://img.shields.io/badge/English-Read-2f855a"></a>
</p>

# 优化路线

项目同时优化两类结果：

- 视觉保真：HTML 打印回 PDF 后尽量接近源文档或源图片。
- 语义保真：可编辑/导出的文本遵循人类阅读顺序，并保留文档结构。

## 已实现路径

- `recursive-xy-cut-v1` 通过水平/垂直 whitespace cut 递归分割页面，让章节标题和独立多栏区域保持正确顺序。
- `column-flow-v1` 检测二栏/三栏文本区域，按栏序再按纵向顺序组织正文。
- `spatial-graph-v1` 用水平重叠和 center proximity 串联弱 irregular column，但只在更强 table/repeated-anchor 路径拒绝后启用。
- `box-flow-v1` 用 pdfminer-style column-biased 候选处理弱列页面，但受 table、repeated-anchor、spatial-graph 和多项几何条件保护。
- `successor-consensus-arbitration-v1` 处理 sparse weak-column 页面：只有 box-flow 与 relation-graph 高度一致、visual-yx 在相邻后继边上失败、并且共识顺序出现明确 column handoff 时才接管。
- Caption-flow 识别 `Figure/Fig./Table/Algorithm + number` caption，把栏内 caption 留在本栏，把跨 gutter caption 标成局部 flow break，并把 figure/table caption 与附近 native image、local raster region 或推断出的 figure/table layout region 建立 proximity evidence。
- Box-flow、relation-graph、structure-relation、successor-consensus 都作为候选诊断进入 benchmark；runtime 只启用了非常窄的 successor-consensus 仲裁。
- `structure_relation` 候选会组合页眉页脚 artifact、脚注、边栏、caption-target proximity 和正文 relation-graph 顺序，形成结构感知候选；它只参与 sidecar 打分和诊断，不改变 runtime 默认顺序。
- Successor-consensus 会汇总 visual-yx、box-flow、relation-graph、structure-relation 和 external-structure 的相邻后继边，作为后续仲裁的支持/冲突证据。
- Reading-order assignment 现在会输出 page-local reading streams：`body-main`、脚注流、边栏流、页眉页脚 artifact 流、caption 流、table-island 流和 grid-island 流。这样编辑器、翻译器和 semantic sidecar 可以使用局部 thread，而不必依赖唯一全局 `semantic_order`。
- `mixed-grid-column-flow-v1` 会识别门户/电商式页面里的非表格重复卡片网格岛，保留局部 row-major 顺序，并导出为 `grid-island` 翻译流。这是通用结构证据，不是针对某个站点调规则；当 fidelity 模式胜出时，视觉相似度仍主要由源背景层保证。
- 正文 reading streams 现在会在结构断点明确时按 segment 暴露：第一条正文链仍是 `body-main`，后续链会变成 `body-segment-002` 等。这为复杂页面提供局部编辑/翻译流，但不会改变当前选中的全局 semantic order。
- Semantic sidecar 能直接给 selected、visual-yx、box-flow、relation-graph、structure-relation、successor-consensus、external-structure 候选打分，并输出候选是否值得考虑接管。
- Semantic sidecar 现在支持关系式和 stream-aware 标签：`successor_edges` 标相邻 labelled 节点，`precedence_edges` 标局部先后约束，`reading_streams` / `streams` 标正文、边栏、脚注、caption、表格岛或卡片网格岛的独立局部链。复杂页面不必被强制写成唯一全局 `text_sequence`。
- `table-row-major-v1` 明确保留表格行优先，不把表格误报为未知 visual-yx fallback。
- `mixed-table-column-flow-v1` 支持混合表格/正文页面：表格岛保持 row-major，周围正文继续按多栏排序。
- 页边 running header/footer、脚注、边栏/旁注会被标注为 secondary/page-artifact flow，保持可编辑但不污染主体列检测。
- Native PDF extraction 保留 image block、font profile、inline text run、SVG line/path、dense vector local raster fallback。
- 图片 source 是一等输入：PNG/JPEG/TIFF/WebP/BMP 会以 `source_type = "image"` 进入 `DocumentIR.source`，保留整页源 visual layer，用 `--image-dpi` 做坐标映射，并由 OCR/结构 JSON 生成可编辑语义锚点，而不是先封装成伪 PDF。
- `DocumentIR.metadata.semantic_layer` 会记录语义层来自 native PDF、结构 JSON、OCR JSON、OCR fallback 还是只有源 visual layer；benchmark 会把这些值输出为 `semantic_layer_driver`、`semantic_layer_payload_kind` 和 `semantic_layer_structure_role`。
- Image-only OCR fallback 为扫描/截图 PDF 增加透明 `native-ocr` 可编辑锚点。
- `--font-profile auto`、`--font-size-scale auto`、`--text-fit auto` 在 benchmark 中执行可重复候选 sweep。
- `--html-mode auto --fidelity-background auto` 比较 structured redraw、SVG fidelity 和 raster fidelity，选择最高视觉相似度路径。
- `fidelity` HTML 模式保留源 SVG/raster 背景，同时叠加透明可编辑坐标节点；编辑/翻译节点打印为局部白底 replacement overlay。
- HTML 打印会把导出 PDF page box 归一到源页面尺寸；当源页数已知时，还会删除 Chromium 追加的尾部空白伪页，避免空白页污染视觉 benchmark。
- Benchmark 输出 source-neutral `source`、兼容列 `source_pdf`、visual similarity、diff 分布、page/size match、semantic order、successor accuracy、reading-order strategy counts、`grid_island_element_count`、reading-order stream counts、risk diagnostics、OCR fallback count、semantic-layer driver counts、candidate diagnostics、fidelity replacement overflow/conflict/fit-scale 指标、stream-local replacement 诊断和外部结构证据匹配/重排/relation-reorder/stream/order-source/relation-edge 结果。候选诊断现在包括 `reading_order_candidate_page_recommendation_counts` 与 `reading_order_candidate_stream_recommendation_counts`，后者按 `reading_order_stream_id` 与 `stream_type` 做局部复核统计，避免边栏/脚注局部流差异被正文页级分数掩盖。
- PaddleOCR-VL / PP-StructureV3 / Docling JSON 可以通过 `--structure-json` 融合进 native IR，作为 role/order/table/formula 证据；显式 block order、Docling `body.children`、结构化 parsing-list 位置和嵌套子 block 顺序都可以在 bbox 匹配后成为 `external_structure_order`，但无序 `layout_det_res.boxes` 只作为 label/region 证据。结构 JSON 里的 `successor_edges`、`precedence_edges`、`relations`、`reading_streams` 现在也可以解析到已匹配元素，并优先形成安全的 path-cover 运行时重排；没有关系边时再回退到 block order。`reading_streams` / `streams` 也会写入面向翻译的 `reading_order_stream_*` 元数据，因此 structure-only sidecar 即使没有 region bbox，也能把 OCR/image 文本分成正文、边栏、表格、卡片/网格等局部流。更具体的子区域会在同分时优先于父区域，因此匹配到的模型 label 可以驱动 page-artifact、footnote、sidebar、caption、table-island，以及明确 card/grid/product/tile 类区域的 `grid-island` reading streams。普通 list label 只作为列表证据，不作为卡片网格证据。
- `benchmark-structure-ab` 会并行运行 native-only 和 native-plus-structure 报告，并输出 `structure_ab_report.json` / `structure_ab_summary.csv`，对比 visual similarity、reading-order risk、grid-island 元素、结构匹配数、page/stream `needs-structure-evidence`，以及存在 sidecar 时的 semantic successor 指标。
- Benchmark 已经可以用 `--input-kind image` 直接接收图片 source；视觉评分会按 `--image-dpi` 比较源图片 visual layer 和 HTML 打印结果，而 OCR/结构 JSON 继续主导语义层，并通过 `semantic_layer_*` case/summary 字段显式记录。
- `--translation-stress pseudo-expand` 会在 benchmark 中写入确定性伪扩展 `translated_text`，让翻译 replacement 风险可以在不绑定具体翻译服务的情况下被度量。
- 最新 JD/PUMA/web-HN 三样本翻译压力 rerun 覆盖 15 页，没有页数或尺寸 mismatch；平均视觉相似度为 `0.81899535`，但 567 个 replacement 里仍有 565 个报告邻近冲突，下一步应继续优化 mask、fitting 和冲突消解。
- Structured HTML 现在暴露 reading-order strategy、region、scope、artifact、sidebar、stream id/type/index、confidence、evidence 和显式 translation target/stream 属性。

## 当前基准覆盖

| 样本 | 多栏元素 | Mixed table-flow | 表格行优先 | Spatial graph | Box-flow 元素 | Caption | Box-flow pairwise | Box-flow successor | Page artifacts | 脚注 | 边栏 | OCR 文本 | Semantic GT | Order accuracy | Successor edges | Visual similarity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Built-in fixtures | 20 | 0 | 18 | 0 | 0 | 0 | 0.19494585 | 19/47 | 0 | 0 | 0 | 0 | yes | 1.0 | 47/47 | 0.9906702 |
| arXiv Attention paper | 163 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 33/33 | 0.96840246 |
| ACL Transformer-XL paper | 1213 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 41/41 | 0.95679576 |
| ACL Transformer-XL first 3 pages, caption-flow | 321 | 0 | 0 | 0 | 0 | 3 figure | 0.0825672 | 142/318 | 1 | 7 | 0 | 0 | partial | 1.0 | 41/41 | 0.98160664 |
| Hacker News print PDF | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 24/24 | 0.9800288 |
| PUMA 2024 Annual Report, first 12 pages | 217 | 238 | 0 | 0 | 0 | 0 | 0.17460108 | 199/509 | 20 | 2 | 36 | 0 | no | n/a | n/a | 0.9795117 |
| JD homepage screenshot PDF | 0 | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 127/133 | 0 | 0 | 0 | 134 | no | n/a | n/a | 0.99576887 |
| JD 首页截图 PNG | 0 | 0 | 0 | 0 | 0 | 0 | 0.43833464 | 128/133 | 8 | 0 | 0 | 134 | no | n/a | n/a | 0.99236799 |

当前公开样本没有触发 `spatial-graph-v1` 或 `box-flow-v1` 元素。这是有意结果：它们是弱列 fallback，应该在强 repeated-anchor、table、sidebar、caption、footnote、XY-Cut 证据都不适用时才接管。两条路径由专门的 weak-column 单元测试覆盖，并通过 benchmark counters 暴露真实文档中是否被使用。

## 视觉保真基线

`--font-size-scale auto --text-fit auto` 的 structured sweep：

| 样本 | 选择 text fit | 前一 structured 最佳 | Auto text-fit visual | Delta |
|---|---|---:|---:|---:|
| arXiv Attention paper | `0.99 + svg` | 0.93670278 | 0.96840246 | +0.03169968 |
| ACL Transformer-XL paper | `0.99 + svg` | 0.93358709 | 0.95679576 | +0.02320867 |
| Hacker News print PDF | `none` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.95010622 | 0.96840901 | +0.01830279 |

`--html-mode auto --fidelity-background auto` sweep：

| 样本 | Best structured | SVG fidelity | Raster fidelity | Auto visual | Selected path |
|---|---:|---:|---:|---:|---|
| arXiv Attention paper | 0.96840246 | 0.98809524 | 1.0 | 1.0 | `fidelity/raster` |
| ACL Transformer-XL paper | 0.95679576 | 0.97636829 | 0.98096887 | 0.98096887 | `fidelity/raster` |
| Hacker News print PDF | 0.9800288 | 0.99490923 | 1.0 | 1.0 | `fidelity/raster` |
| Three-sample mean | 0.96840901 | 0.98645759 | 0.99365629 | 0.99365629 | mixed |

Fidelity 路径已经具备最小编辑打印能力：编辑或翻译节点会作为局部白底 replacement overlay 打印。Raster background 在当前两个样本达到完美像素相似，但 SVG background 对矢量检查和未来非 raster 编辑仍然重要。

## 额外复杂样本

| 样本 | 范围 | Structured visual | SVG fidelity | Raster fidelity | Selected path | 说明 |
|---|---:|---:|---:|---:|---|---|
| PUMA 2024 Annual Report | first 12 / 345 pages | 0.73733248 | 0.97885835 | 0.9795117 | `fidelity/raster` | 815 元素，521 可编辑，99 direct column-flow，238 mixed-table-flow，20 header artifact，2 footnote，36 right sidebar，无 semantic sidecar 时仍 high risk |
| JD homepage screenshot PDF | 1 / 1 page | 0.99536129 | 0.99536129 | 0.99576887 | `fidelity/raster` | image-only 截图 PDF，134 个透明 OCR 编辑锚点，由 recursive XY-Cut 处理 |

JD 的收益不是视觉分数提升，而是结构/可编辑性提升：从 1 个 image 元素、0 个可编辑文本节点，变成 135 个元素和 134 个 `native-ocr` 可编辑锚点，同时保持源视觉层。

## 下一步优化选项

1. 扩展复杂 PDF 的真实 semantic ground truth

   Attention、Transformer-XL、Hacker News 已有部分 sidecar。PUMA 年报和 JD 截图 PDF 还没有 semantic sidecar，却暴露了复杂图文混排、边栏、表格岛、OCR 网页顺序等问题。下一步应覆盖年报、手册、表格、公式、脚注、附录和更多网页打印 PDF。对于存在多种合理全局序列的页面，优先使用 `successor_edges` / `precedence_edges` 标注局部阅读关系。

2. 递归 XY-Cut 和局部结构 refinement

   已实现 column-flow、table island、sidebar、footnote、caption、caption-target proximity、spatial graph、box-flow、relation graph、structure-relation 和候选诊断。下一步重点是基于 sidecar 校准 caption-target proximity，把 target 关系纳入候选仲裁，并继续组合 native heuristics、relation graph、structure-relation、successor consensus、role、table、caption、外部结构证据。

3. 矢量渲染 refinement

   SVG path 已支持部分 PyMuPDF drawing item，但 dense local raster fallback 仍牺牲图内编辑能力。下一步应保留 PDF clipping、blend mode、mask 和 grouped drawing order，让更多复杂 drawing 继续结构化。

4. Fidelity 模式的编辑 mask 和 replacement fitting

   当前 edited/translated 节点可以打印为局部白底 overlay。翻译应按 `data-scriptorium-translation-stream-id` 分批应用，让正文、边栏、表格岛和卡片网格岛可以独立替换。第一版 `fidelity-replacement-fit-v1` compositor 已能扩展局部 mask、用 padding 把文本对齐回源坐标、缩小长译文，并把 overflow/conflict/fit-scale 指标写入 benchmark。下一步应把 bbox 级估计升级为 glyph-extents-aware mask，并用真实翻译后的 JD/PUMA/门户样本同时优化 conflict count 和 visual similarity。

5. 更细粒度的字体、缩放和 text-fit 选择

   现在是文档级候选 sweep。下一步应转成 page-level 或 font-cluster-level 选择，避免正常转换时做完整多候选 print/compare，同时支持编辑状态切换和长翻译文本的 fitted layer。

6. Relation graph 和 successor consensus 仲裁

   当前 relation graph 已能降低若干复杂样本的 local successor disagreement，但 pairwise disagreement 仍高，不能直接默认接管。`structure_relation` 进一步把 page-scope、artifact/footnote/sidebar、caption-target 和正文 relation graph 结合为候选，用于 sidecar 评分和后续仲裁证据。下一步应把 relation edges、structure-relation evidence、candidate consensus、page-level recommendation、semantic sidecar、role/caption/table proximity 和外部模型证据结合起来，只在独立证据支持时切换顺序。

   Reading stream 层借鉴 PDF article threads 的架构：复杂排版可以显式暴露穿过非连续区域的局部阅读路径。Grid-island stream 把这套机制扩展到门户/卡片式版面：视觉还原主要由背景层解决，但翻译和编辑需要稳定的局部结构。它也和 relation/path-cover 类阅读顺序方法一致，因为复杂页面里更稳定的信号往往是局部 successor / precedence 关系，而不是一个脆弱的全局序列。

7. 真实模型证据 A/B

   `structure_evidence.py` 和 `--structure-json` 已就绪。需要运行真实 PaddleOCR-VL 1.6、PP-StructureV3、DoclingDocument JSON，对同一 PDF 比较 native-only 和 native-plus-structure。数字 PDF 优先用模型补 role/order/table/formula；扫描 PDF 可让模型成为主文本源。

8. OCR fallback refinement

   当前 image-only fallback 由无原生文本和高图像覆盖触发。下一步需要 OCR 置信度聚合、mixed native/scanned 页面局部 OCR、语言自动检测、隐藏 OCR 文本去重，以及 Paddle/PP-Structure OCR 作为 Tesseract 的强替代证据。

9. Semantic-order benchmark 扩展

   后续 benchmark 应继续报告 normalized edit distance、column order accuracy、successor-edge accuracy、relation successor / precedence accuracy、table row-major preservation、caption proximity、footnote/header/footer calibration，以及候选仲裁的 sidecar-scored delta。
