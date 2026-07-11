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
- Geometry-only relation-graph 除了 max-regret path-cover candidate 外，现在还会保留每条已选边在选择时的 source/target alternative、margin、selection regret 和 exact-tie 标记。同分边只进入 sidecar review，不会因为任意几何 tie-break 被提升为 strict；在语义/model evidence 可仲裁前，relation-graph 仍只作为诊断。
- `structure_relation` 候选会组合页眉页脚 artifact、脚注、边栏、caption-target proximity 和正文 relation-graph 顺序，形成结构感知候选；它只参与 sidecar 打分和诊断，不改变 runtime 默认顺序。
- Successor-consensus 会汇总 visual-yx、box-flow、relation-graph、structure-relation 和 external-structure 的相邻后继边，作为后续仲裁的支持/冲突证据。
- Reading-order assignment 现在会输出 page-local reading streams：`body-main`、脚注流、边栏流、页眉页脚 artifact 流、caption 流、table-island 流和 grid-island 流。这样编辑器、翻译器和 semantic sidecar 可以使用局部 thread，而不必依赖唯一全局 `semantic_order`。
- `mixed-grid-column-flow-v1` 会识别门户/电商式页面里的非表格重复卡片网格岛，保留局部 row-major 顺序，并导出为 `grid-island` 翻译流。这是通用结构证据，不是针对某个站点调规则；当 fidelity 模式胜出时，视觉相似度仍主要由源背景层保证。
- 正文 reading streams 现在会在结构断点明确时按 segment 暴露：第一条正文链仍是 `body-main`，后续链会变成 `body-segment-002` 等。这为复杂页面提供局部编辑/翻译流，但不会改变当前选中的全局 semantic order。
- 原生 table/grid island 的 strict proposal edge 现在进入独立的 `local_structure_*` 证据通道，而不是再投一张全页 consensus 票。它报告 island stream、潜在/严格边覆盖率、selected/reference 覆盖率，以及通用 consensus 没有保留的严格局部边。只有完整覆盖的岛会得到 `keep-selected-local-structure`；正文和跨 stream handoff 仍保持可复核。`benchmark-structure-ab` 会输出相同的 native/structure 计数及 delta。
- 显式且连续的模型 `block_order` 现在会在 primary text block 间生成带完整 provenance 的 review-only transition。Secondary/table/grid/caption 边界、缺失 tier、同序歧义、implicit order 和弱 region coverage 都会阻止提案；strict transition 始终为 0。Surya FastLayout 增加了真正的 learned-order provider，并带显式许可确认和拒绝 raster fallback 的 fail-closed guard。它在固定 ROOR 前缀上达到 `30/41` 已标注 transition 正确，但 held-out Transformer-XL 降到 `2/9`；因此 `semantic_policy: review-only` 会把模型 label、stream、relation 与 order 从 runtime 隔离，同时保留 proposal 评分。
- OpenDataLoader PDF 2.4.7 增加面向数字 PDF 的确定性 Apache-2.0 CPU/Java XY-Cut++ provider。它把 raw bottom-left PDF JSON 归一成稳定的 review-only block 与 successor edge。Held-out Transformer external successor 为 `34/41`（`0.82926829`），高于 Surya 的 `21/41`，但 selected native order 仍为 `41/41`；PP/OpenDataLoader consensus 得到 9 条 review edge，已标注 precision 为 `4/4`，覆盖只有 `4/41`。因此它仍是 proposal evidence，不是 runtime order。
- Docling 2.111.0 增加基于 MIT/API 的 PDF 与图片 provider，底层使用 Apache-2.0 Heron 学习式布局检测器及规则式 reading-order 阶段。标准化语义、关系和顺序均为 review-only，streams 被禁用，候选与 successor consensus 隔离。ROOR validation split 全量 49 页中，selected native 保持 `1274/2612`（`0.48774885`），Docling 为 `785/1888`（`0.41578390`）；38 个完整可比页面中仅 3 页更好、3 页相同、32 页更差。因此不能提升到 runtime，下一步应寻找许可清晰、真正以 relation 训练的 predictor。
- Provider isolation 现在是通用契约：任意 structure payload 声明 `candidate_consensus_policy: isolated` 后仍可独立评分，但不能改变 successor consensus 或 page/stream recommendation。仅供研究的 HURIDOCS 在 held-out Transformer-XL 第 2 页得到 `13/16`，介于 native selected 的 `16/16` 和几何候选的 `8/16` 之间；其推理代码未声明许可，因此不能集成。ROOR 和 FocalOrder 仍因没有发布可复现微调权重而无法成为可执行 provider。
- 项目自有的轻量 relation ranker 现在可以从 ROOR 官方 train split 可复现训练，包含 UID-hash calibration、答案 relation 输入拒绝、model manifest hash 和 isolated review-only 输出。独立校准的 branch gate 对每个 source 最多输出一条 rank-2 edge。49 个 held-out validation 页面上，branching 直接边 precision/recall/F1 为 `0.67794118/0.70597243/0.69167292`；解码后的 external candidate 把官方 relation accuracy 从 native `1274/2612`（`0.48774885`）提高到 `1529/2612`（`0.58537519`），并在 35 页上最佳。多页 `DocumentIR` 现在是一等推理输入；跨域 replay 在 Transformer-XL 为 `29/41`、Attention 为 `29/33`，高于几何候选但低于 native selected。仅由 fit 数据计算的 feature envelope 在模型仍高 confidence 时，仍能标出 Attention、PUMA、JD 的更强域漂移。它继续留在 runtime 外；下一步聚焦独立年报/门户 relation label 和 OOD-aware rejection calibration。
- 固定版本的 Comp-HRDoc fetcher 现在可以从官方 test annotation 与本地渲染的 arXiv PDF 生成答案分离的 oracle-layout benchmark。固定前两个 test 文档各取 5 页，ranker 直接边 precision/recall/F1 为 `0.93472585/0.99444444/0.96366083`；decoded external 把 native `346/360` 提高到 `360/360`。Visual-yx 和 relation graph 也达到 `360/360`，说明该 slice 验证的是局部 textline continuity，而不是困难的 floating/table/grid transition。下一个独立数据集应针对这些结构，不能继续堆叠相似论文页。
- Comp-HRDoc 图形 node 和 floating label 现已保留。review-only structure-role geometry 层会输出可审计的 `figure -> caption` 和 `caption -> table` edge，并通过不含答案的 block membership 支持多行 caption。更强的 annotation-only benchmark 使用固定 250 页 prefix，覆盖 53 个 test 文档和 347 条图形 label。Role fusion 把整体 raw F1 从 `0.84129386` 提高到 `0.85716953`；图形 precision/recall/F1 为 `0.86257310/0.85014409/0.85631350`，取代早期 18/18 小样本的乐观外推。官方 order id 仍只在 sidecar。下一步是 train-only floating-pair gate，再在该未改动 test prefix 上校准 rejection。
- `consensus-reading-sidecars` 只有在稳定 element 的 id/text/bbox fingerprint 完全一致后，才对独立 provider 的显式 transition 求交。有标签汇总 precision 为 `6/6`，但正确覆盖只有 `6/255`，所以 consensus 仍是 `runtime_reorder: false` 的 review 降噪手段，不是自动晋升路径。
- `protected_successor_consensus` 现在用于验证下一层 relation，但不改变 runtime order：有效的原生 table/grid strict edge 会在通用 candidate edge 之前作为 hard local constraint 进入 acyclic path cover。它报告 protected 与 unresolved edge、明确的拒绝原因，以及约束序列化后仍缺失的严格边。PUMA/JD 的 103/103 保留只证明 constraint serializer 正常；完整 ROOR validation 使用稳定 element ID 保留全部 2,612 条官方 relation（避免重复 segment text 被压缩），直接标注 native local edge 只有 `316/617`（`0.51215559`），适用范围内的 protected candidate 为 `0.41918103`，低于 selected native order 的 `0.48774885`。因此原生 geometry edge 继续只作为 review/translation-stream evidence，不得成为 runtime hard constraint；hard constraint 必须来自显式 external successor/stream relation、独立验证过的 relation predictor 或 accepted human review。
- Semantic sidecar 能直接给 selected、visual-yx、box-flow、relation-graph、structure-relation、successor-consensus、诊断专用 protected-successor-consensus、external-structure 候选打分，并输出候选是否值得考虑接管。
- Semantic sidecar 现在支持关系式和 stream-aware 标签：`successor_edges` / `ro_linkings` / `reading_order_*` / typed `relations` 标相邻 labelled 节点，`precedence_edges` 标局部先后约束，`reading_streams` / `streams` 标正文、边栏、脚注、caption、表格岛或卡片网格岛的独立局部链。ROOR 风格 sidecar 可以复用 `document` segment id 或 0-based 结构列表下标，并在评分前解析回文本；stream members 也可以先声明成员标签，不会在没有显式 linkings 时强行暗示顺序。因此 OCR/结构 JSON 可以同时主导语义层和 benchmark 标签。复杂页面不必被强制写成唯一全局 `text_sequence`。
- Semantic sidecar 现在也会评估 selected IR 的 stream assignment 质量。`semantic_stream_assignment_id_accuracy` 和 `semantic_stream_assignment_type_accuracy` 会检查 labelled stream 成员是否带有预期的 `reading_order_stream_id` 和归一化 stream type；这是 image-source OCR/结构 JSON 和翻译回渲染要重点看的指标。Stream successor/predecessor 指标看局部顺序，assignment 指标看语义层是否真的生成了正确的局部翻译流。`grid-island=>body` 这类 type-confusion 计数能把退化定位到具体 stream 类型。
- 现在已实现可审查的 `ScriptoriumReadingOrderSidecar` proposal layer：严格可执行的局部边、低置信 review edge 和跨 stream transition 被分开，且 proposal 在显式改为 `accepted` 前不会修改 IR。有标注 benchmark 会分别报告 strict-edge precision/coverage 与 strict-plus-review coverage；下一步应通过独立模型或人工 relation evidence 提高 coverage，同时保持高 precision，不能因为 raw edge count 或单个无标签门户页而自动晋升。
- constrained-consensus 已完成 page-local constraint 实验，不会把严格的 `table-local-order` / `grid-local-order` 当成额外 selected-order 投票。ROOR 的独立 relation 结果否定了将原生 geometry chain 自动提升的前提，因此该候选继续只用于诊断，必须分别报告受保护和仍未解决的 successor edge，并保持 island 边界。
- `table-row-major-v1` 明确保留表格行优先，不把表格误报为未知 visual-yx fallback。
- `mixed-table-column-flow-v1` 支持混合表格/正文页面：表格岛保持 row-major，周围正文继续按多栏排序。
- 页边 running header/footer、脚注、边栏/旁注会被标注为 secondary/page-artifact flow，保持可编辑但不污染主体列检测。
- Paddle 文档中的 `aside_text` 布局标签现在和 `sidebar_text` 一样被当作 sidebar，因此边侧次要内容会保持独立的可编辑/翻译流，不再混进正文。
- `run-pp-structure` 现在可以从 PDF 或图片 source 产出可重放的 PP-StructureV3 JSON。它默认只跑 layout，避免普通多栏证据运行时加载表格/公式/region 模块；需要完整 table-cell/formula 证据的 benchmark 可再显式开启对应 flag。CPU compatibility mode 仅属于这个可选 provider，不会影响核心转换依赖集。
- Native PDF extraction 保留 image block、font profile、inline text run、SVG line/path、dense vector local raster fallback。
- 图片 source 是一等输入：PNG/JPEG/TIFF/WebP/BMP 会以 `source_type = "image"` 进入 `DocumentIR.source`，保留整页源 visual layer，用 `--image-dpi` 做坐标映射，并由 OCR/结构 JSON 生成可编辑语义锚点，而不是先封装成伪 PDF。image-source IR 以 `source` / `source_path` 作为身份字段，不再自动填充 `source_pdf`。
- `DocumentIR.metadata.semantic_layer` 会记录语义层来自 native PDF、结构 JSON、OCR JSON、OCR fallback 还是只有源 visual layer；benchmark 会把这些值输出为 `semantic_layer_driver`、`semantic_layer_payload_kind` 和 `semantic_layer_structure_role`。
- Image-only OCR fallback 为扫描/截图 PDF 增加透明 `native-ocr` 可编辑锚点。
- `--font-profile auto`、`--font-size-scale auto`、`--text-fit auto` 在 benchmark 中执行可重复候选 sweep。
- `--html-mode auto --fidelity-background auto` 比较 structured redraw、SVG fidelity 和 raster fidelity，选择最高视觉相似度路径。
- `fidelity` HTML 模式保留源 SVG/raster 背景，同时叠加透明可编辑坐标节点；编辑/翻译节点打印为局部白底 replacement overlay。
- HTML 打印会把导出 PDF page box 归一到源页面尺寸；当源页数已知时，还会删除 Chromium 追加的尾部空白伪页，避免空白页污染视觉 benchmark。若 Playwright 返回视觉全空的 PDF，则会改走带 3 秒 virtual-time 资源等待的 Chromium CLI 打印。
- Benchmark 输出 source-neutral `source`、兼容列 `source_pdf`、visual similarity、diff 分布、page/size match、semantic order、successor accuracy、semantic stream assignment accuracy/confusion、reading-order strategy counts、`grid_island_element_count`、reading-order stream counts、risk diagnostics、OCR fallback count、semantic-layer driver counts、candidate diagnostics、relation-graph path-cover tie/margin 指标、fidelity replacement overflow/conflict/fit-scale 指标、stream-local replacement 诊断、replacement conflict stream-pair 归因和外部结构证据匹配/重排/relation-reorder/stream/order-source/relation-edge 结果。候选诊断现在包括 `reading_order_candidate_page_recommendation_counts` 与 `reading_order_candidate_stream_recommendation_counts`，后者按 `reading_order_stream_id` 与 `stream_type` 做局部复核统计，避免边栏/脚注局部流差异被正文页级分数掩盖。
- PaddleOCR-VL / PP-StructureV3 / Docling / ROOR 风格 JSON 可以通过 `--structure-json` 融合进 native 或 image-source IR，作为 role/order/table/formula/reading-order 证据；显式 block order、Docling `body.children`、结构化 parsing-list 位置和嵌套子 block 顺序都可以在 bbox 匹配后成为 `external_structure_order`，ROOR 风格 `document` 列表会被当作无序 segment，只有关系边存在时才驱动顺序。PP-StructureV3 `table_res_list` 和 Docling `data.table_cells` / `grid` 现在会变成具体的 `table_cell` 区域，并带 row-major `external_structure_order_subindex`，因此一个父 table block 下的表格岛也能恢复局部单元格顺序。PP table cell 还可以从 `table_ocr_pred` 直接生成 image-source OCR anchors；PP 的 `overall_ocr_res`、`text_paragraphs_ocr_res`、`formula_res_list`、`seal_res_list` 也可以为 image source 生成文本、公式和印章/特殊文本锚点，但默认仍只作为无序区域证据。Docling `furniture.children` 会提供 page-artifact 角色/stream 证据但不成为正文顺序证据，无序 `layout_det_res.boxes` 只作为 label/region 证据。结构 JSON 里的 `successor_edges`、`ro_linkings`、`reading_order_edges`、`precedence_edges`、`relations`、`reading_streams` 现在可以解析到已匹配元素、OCR anchor 原始 id/ref、0-based 结构列表下标，或只有文本列表时的文本 alias，并优先形成安全的 path-cover 运行时重排；没有关系边时再回退到 block order。Stream-level 的 `ro_linkings` / `reading_order_*` 别名也会同时作为关系边和 stream members 解析，并且 alias fallback 会写到已解析 edge/member metadata，而不只是总数指标。`reading_streams` / `streams` 会写入面向翻译的 `reading_order_stream_*` 元数据；只有关系边、没有显式 stream 的 sidecar 也会从安全 successor chains 派生 `external-relation-*` streams，因此 structure-only sidecar 即使没有 region bbox 或显式 stream，也能把 OCR/image 文本分成正文、边栏、表格、卡片/网格等局部流。更具体的子区域会在同分时优先于父区域，因此匹配到的模型 label 可以驱动 page-artifact、footnote、sidebar、caption、table-island，以及明确 card/grid/product/tile 类区域的 `grid-island` reading streams。普通 list label 只作为列表证据，不作为卡片网格证据。
- 一个结构 relation 现在可以指向覆盖多条 native/OCR 行的 block。运行时只会展开共享同一已匹配结构区域 signature 的候选，保留其局部顺序并补入 internal successor edge，再在 block 边界施加外部 relation；同时记录每页已解析/未解析 relation 与 stream-member 诊断，避免重复 label 或短文本模糊匹配悄悄生成错误边。
- 落在明确 native column 上的 root-body Docling 证据现在被显式标为 secondary：它保留 `external_structure_stream_*` 和 relation 诊断，但不能替换 native 翻译流、触发全局 path-cover 重排，或在 sidecar 生成时重新变成 generic external block partition。这样 Transformer-XL 的 strict anchor path 保持 native 的 `32/41`，同时保留 JD 门户页的 stream/disagreement 改善。
- 稀疏外部 block order 现在会被融合成相邻 tier 的 precedence 约束，而不是全局排序。native order 是稳定拓扑排序的 tie-breaker，未匹配内容保持本地位置；image、figure、furniture、footnote、sidebar 等非正文 implicit-list block 不再成为顺序约束。通用外部 text order 也会让位给 native table/grid/caption/artifact/secondary stream，除非模型给出同样具体的 table/grid label。这个改动消除了真实 PP-StructureV3 PUMA 页中图内说明被拖入正文的误排序，也阻止 PP text block 降低 JD grid-stream 诊断，同时没有削弱 Attention 和 Transformer-XL 的已标注结果。
- `benchmark-structure-ab` 会并行运行 native-only 和 native-plus-structure 报告，并输出 `structure_ab_report.json` / `structure_ab_summary.csv`，对比 visual similarity、reading-order risk、grid-island 元素、结构匹配数、结构关系/stream alias/group 解析数、未解析 relation/stream ref 数、page/stream `needs-structure-evidence`、same-stream/cross-stream replacement conflict targets，以及存在 sidecar 时的 semantic successor、semantic relation/stream/assignment missing-label counts 和 semantic stream-assignment id/type accuracy 指标。
- Benchmark 已经可以用 `--input-kind image` 直接接收图片 source；视觉评分会按 `--image-dpi` 比较源图片 visual layer 和 HTML 打印结果，而 OCR/结构 JSON 继续主导语义层，并通过 `semantic_layer_*` case/summary 字段显式记录。
- `--translation-stress pseudo-expand` 会在 benchmark 中写入确定性伪扩展 `translated_text`，让翻译 replacement 风险可以在不绑定具体翻译服务的情况下被度量。
- v3 browser-layout 翻译压力 rerun 覆盖相同 15 页，没有页数或尺寸 mismatch。它会在字体和 print media ready 后在 Chromium 中 fitting replacement text，把 source render pixel 换算到 96-DPI 打印坐标，并为深色 raster 边缘上的亮色文本采样深色 mask。端到端平均视觉相似度为 `0.92760169`；保留的静态 estimate 是 326 个 overflow，Chromium 实测裁切为 81 个。两者是不同测量，不能理解为直接前后降幅。JD 占 79 个真实裁切，下一步重点是通用的局部 stream/region reflow。
- 下一步 reflow 应以受结构保护的 stream/region 为单位，而不是继续全局缩字号：先在原 bbox fitting，再压缩行高；只有 collision 检查允许时才沿书写方向扩展，最后才降低字号。这个顺序借鉴 BabelDOC 的 typesetting 思路，同时保留 Scriptorium 的浏览器实测和冲突诊断。
- Fidelity benchmark 还会单独输出静态与浏览器实测的 replacement overflow、fit scale、line-height compaction、mask-color 来源和 per-stream diagnostics；`fidelity_replacement_layout_report.json` 保留每个 replacement 的 DOM 尺寸与裁切证据，避免把静态 predictor 误当成实际渲染结果。
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

   Attention、Transformer-XL、Hacker News 已有部分 sidecar。PUMA 年报和 JD 截图 PDF 还没有 semantic sidecar，却暴露了复杂图文混排、边栏、表格岛、OCR 网页顺序等问题。下一步应覆盖年报、手册、表格、公式、脚注、附录和更多网页打印 PDF。对于存在多种合理全局序列的页面，优先使用 `successor_edges`、`ro_linkings`、`reading_order_*` 和 `precedence_edges` 标注局部阅读关系；如果标签来自 OCR/结构 JSON，优先保留 `document` segment id，让评测侧按 id 解析到文本。

2. 递归 XY-Cut 和局部结构 refinement

   已实现 column-flow、table island、sidebar、footnote、caption、caption-target proximity、spatial graph、box-flow、relation graph、structure-relation、可审查 reading-order sidecar 和候选诊断。下一步重点是基于 sidecar 校准 caption-target proximity，把 target 关系纳入候选仲裁，并继续组合 native heuristics、relation graph、structure-relation、successor consensus、role、table、caption、外部结构证据；只有有标注 relation coverage 提升后才考虑扩大自动接管范围。

3. 矢量渲染 refinement

   SVG path 已支持部分 PyMuPDF drawing item，但 dense local raster fallback 仍牺牲图内编辑能力。下一步应保留 PDF clipping、blend mode、mask 和 grouped drawing order，让更多复杂 drawing 继续结构化。

4. Fidelity 模式的编辑 mask 和 replacement fitting

   当前 edited/translated 节点可以打印为局部 replacement overlay。翻译应按 `data-scriptorium-translation-stream-id` 分批应用，让正文、边栏、表格岛和卡片网格岛可以独立替换。`fidelity-replacement-fit-v3-browser` 会保留保守的 padding guard，再在字体加载后实测 Chromium 字形排版、搜索受限 scale、必要时压缩行高，并把静态 estimate 与真实 clipping 分开记录。打印专用几何会用 `96 / render_dpi` 换算 render-pixel mask、padding 和字号；深色背景采样 mask 会保持图像上亮色文本可见。15 页 v3 运行达到 `0.92760169` 平均视觉相似度，但 JD 仍有 79 个真实裁切。下一步是有碰撞与结构证据保护的 stream/region 级 reflow 和容量共享，解决真正密集的翻译卡片/OCR 内容，而不是继续整体缩小或写样例特例。

5. 更细粒度的字体、缩放和 text-fit 选择

   现在是文档级候选 sweep。下一步应转成 page-level 或 font-cluster-level 选择，避免正常转换时做完整多候选 print/compare，同时支持编辑状态切换和长翻译文本的 fitted layer。

6. Relation graph 和 successor consensus 仲裁

   当前 relation graph 已能降低若干复杂样本的 local successor disagreement，但 pairwise disagreement 仍高，不能直接默认接管。`structure_relation` 进一步把 page-scope、artifact/footnote/sidebar、caption-target 和正文 relation graph 结合为候选，用于 sidecar 评分和后续仲裁证据。下一步应把 relation edges、structure-relation evidence、candidate consensus、page-level recommendation、semantic sidecar、role/caption/table proximity 和外部模型证据结合起来，只在独立证据支持时切换顺序。

   Reading stream 层借鉴 PDF article threads 的架构：复杂排版可以显式暴露穿过非连续区域的局部阅读路径。Grid-island stream 把这套机制扩展到门户/卡片式版面：视觉还原主要由背景层解决，但翻译和编辑需要稳定的局部结构。它也和 relation/path-cover 类阅读顺序方法一致，因为复杂页面里更稳定的信号往往是局部 successor / precedence 关系，而不是一个脆弱的全局序列。

   Paddle/PP-Structure/Surya 的显式 block order 现在只会作为连续 primary-block review transition 进入 sidecar，并保留 provider/order/label/bbox/member provenance 与独立的 direct/path 指标。Image benchmark 共享稳定的 `--ocr-json` anchors，并保持 `--structure-json` 只进入 provider 分支；固定 ROOR 五页的答案 relation 只进入评测 sidecar。PP 精确 OCR 行与唯一 ordered parent 的 companion fusion 也被隔离为 review-only order，不能触发 runtime reorder 或派生 block stream。Surya 在固定 ROOR 的 `30/41` 没有泛化到 held-out Transformer 的 `2/9`；独立 provider consensus 虽把已标注 precision 提高到 `6/6`，却只覆盖 `6/255`。下一步是扩大 held-out relation/stream 覆盖或接入许可清晰的独立 relation predictor，而不是放宽 block-order/consensus gate。

7. 真实模型证据 A/B

   `structure_evidence.py` 和 `--structure-json` 已就绪；真实 PP-StructureV3 `save_to_json` 输出现已覆盖 Attention、Transformer-XL、JD 和 PUMA 页面的 native-only / native-plus-structure A/B。两篇有标注论文均保持 `1.0` pair/successor accuracy；JD 门户页降低了 successor-consensus disagreement，但也暴露模型没有 relation/stream edge 的剩余问题。A/B 现在还会单独比较 native local table/grid stream、strict edge、被 generic consensus 打断的 strict edge 与 `keep-selected-local-structure` 推荐数，防止只看一个分歧数字就把 block order 当作语义净提升。接下来继续让 PaddleOCR-VL 1.6、DoclingDocument JSON 和关系式 reading-order JSON 跑同一组 source。数字 PDF 优先用模型补 role/order/table/formula；image source 和扫描 PDF 可让模型成为主文本源。

   OpenDataLoader 现在提供与 learned、OCR/layout provider 并列的、可复现的确定性 PDF-only control。它在 Transformer 上提供有用候选，但没有超过 selected native order，与 PP 的一致边也仍稀疏。下一步 provider 工作应扩大 held-out relation/stream 覆盖，尤其补充 Paddle/Docling 的 image/扫描文档证据或许可清晰的 relation predictor；不能依据两篇论文放宽 runtime gate。

   PaddleOCR-VL 1.6 现在已有真实 PUMA 第 5 页的重放路径。匹配 bbox 前会归一化它的 `width` / `height` 模型输入画布，包括嵌套 result wrapper，因此同一份 96-DPI raw JSON 在 96 和 144 DPI benchmark 渲染下都得到相同的 24 个结构匹配。显式 ordered 的正文 block 会在 native 成员共享同一个 flow segment 和列时派生受限的 `external-block-body-*` 从属翻译分组；主 native/relation stream 保持不变，HTML 通过独立的 `data-scriptorium-structure-stream-*` 暴露 block 级翻译/fitting 边界。当前 PP 重放中，Transformer-XL 第 1-3 页保留 21 个分组/158 个成员，同时 strict anchor-path coverage 从 `0.78048780` 提升到 `0.80487805`，stream `needs-structure-evidence` 从 `3` 降到 `2`；JD 先前的 `1 -> 2` 回退修复为 `1 -> 1`。Selected order 和 visual delta 均为 0。下一类 provider 应提供局部 relation/stream 证据，或接入专门的 relation predictor 生成可审查 successor edge，然后再扩大运行时顺序仲裁。

8. OCR fallback refinement

   当前 image-only fallback 由无原生文本和高图像覆盖触发。下一步需要 OCR 置信度聚合、mixed native/scanned 页面局部 OCR、语言自动检测、隐藏 OCR 文本去重，以及 Paddle/PP-Structure OCR 作为 Tesseract 的强替代证据。

9. Semantic-order benchmark 扩展

   现已加入 ROOR 官方完整 49 页 validation：生成的 layout-anchor-only structure JSON 只保留 text/bbox anchor，`ro_linkings` 只用于评测，绝不传入转换器；因此它隔离 relation prediction 与 OCR 质量，也避免把 raster-fidelity 的 `1.0` 误读为 semantic 成功。它应作为 candidate/local-edge precision 的回归 gate，而不是用于对样本做规则特调。后续 benchmark 应继续报告 normalized edit distance、column order accuracy、successor-edge accuracy、relation successor / precedence accuracy、table row-major preservation、caption proximity、footnote/header/footer calibration，以及候选仲裁的 sidecar-scored delta。
