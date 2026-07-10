<p align="center">
  <a href="../README.md"><img alt="Home" src="https://img.shields.io/badge/Home-README-2b6cb0"></a>
  <a href="optimization-roadmap.zh-CN.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E9%98%85%E8%AF%BB-blue"></a>
  <img alt="English" src="https://img.shields.io/badge/English-current-2f855a">
</p>

# Optimization Roadmap

This project optimizes two different outcomes:

- visual fidelity: the HTML prints back to a PDF that looks like the source document or image
- semantic fidelity: editable/exported text follows human reading order and keeps document structure

## Current Implemented Path

- `recursive-xy-cut-v1` recursively segments pages with horizontal and vertical whitespace cuts, so section headings can stay between independent column regions.
- `column-flow-v1` detects common two- and three-column text regions and orders text by column before moving to the next column.
- `spatial-graph-v1` handles weak irregular columns by linking vertically adjacent text boxes through horizontal overlap and center proximity, but only after stronger table and repeated-anchor paths decline the page.
- `box-flow-v1` handles weak irregular columns with a pdfminer-style column-biased candidate order, but only after stronger table, repeated-anchor, and spatial-graph paths decline the page.
- `successor-consensus-arbitration-v1` handles sparse weak-column pages that would otherwise fall back to visual order, but only when box-flow and relation-graph agree, visual-yx loses adjacent successor edges, and the consensus order shows clear column handoff(s). Multi-handoff pages preserve sparse three-/four-column metadata.
- Caption-flow uses native/OCR text labels to identify `Figure/Fig./Table/Algorithm + number` captions, keeps column-local captions in-column, turns cross-gutter captions into local flow breaks, and now links figure/table captions to nearby native images, local raster regions, or inferred figure/table layout regions as proximity evidence.
- Column-biased box-flow candidate diagnostics still compare the selected semantic order against a horizontal-flow order and report pairwise disagreement for all benchmarked pages.
- Geometry-only relation-graph candidate diagnostics now compare the selected semantic order against local successor edges serialized through a max-regret path cover. This remains diagnostic-only until semantic/model evidence can arbitrate when it should override the selected order.
- Structure-relation candidate diagnostics now combine page artifacts, footnotes, sidebars, caption-target proximity, and relation-graph body order into a sidecar-scored semantic candidate. This remains benchmark-only and does not change runtime ordering.
- Successor-consensus diagnostics now vote over adjacent successor edges from visual-yx, box-flow, relation-graph, structure-relation, and optional external-structure candidates, producing an acyclic path-cover candidate for benchmark scoring plus support/coverage/conflict metrics for arbitration triage.
- Reading-order assignments now expose page-local reading streams: `body-main`, footnote streams, sidebar streams, header/footer artifact streams, caption streams, table-island streams, and grid-island streams. This gives editors, translators, and semantic sidecars explicit local threads without changing the selected global `semantic_order`.
- `mixed-grid-column-flow-v1` detects repeated non-table card/grid islands on portal and ecommerce-like pages, preserves their local row-major order, and exports them as `grid-island` translation streams. This is a structural signal, not a site-specific rule: visual parity still comes from source backgrounds when fidelity mode wins.
- Body reading streams are segment-aware when structural breaks are explicit: the first content chain remains `body-main`, while later chains become `body-segment-002` and beyond. This gives complex pages local editable/translation streams without changing the selected global semantic order.
- Page-level candidate diagnostics now compare the selected order against successor-consensus per page and emit `reading_order_candidate_page_recommendation_counts`, so unlabeled external PDFs can still surface pages needing structure evidence or review. Stream-level diagnostics now do the same by `reading_order_stream_id` and emit `reading_order_candidate_stream_diagnostics`, `reading_order_candidate_stream_count`, and `reading_order_candidate_stream_recommendation_counts`, which isolate sidebar/footnote/secondary-flow disagreement from the main narrative stream.
- Semantic sidecars now score named candidate orders directly. Benchmark emits selected, visual-yx, box-flow, relation-graph, structure-relation, successor-consensus, and external-structure semantic candidate metrics where ground truth exists, plus candidate-vs-selected arbitration recommendations. Future order switching can therefore be evaluated against human labels instead of only candidate-vs-selected disagreement.
- Semantic sidecars now also support relation-style and stream-aware labels: `successor_edges` / `ro_linkings` / `reading_order_*` / typed `relations` for local adjacent labelled nodes, `precedence_edges` for partial before/after constraints, and `reading_streams` / `streams` for independent body/sidebar/footnote/caption/table/grid chains. ROOR-style sidecars can reuse `document` segment ids or zero-based structure-list indices and resolve them back to text before scoring, and stream members can be labelled without implying order until explicit linkings are provided. OCR/structure JSON can therefore drive both the semantic layer and the benchmark labels. This lets complex pages be labelled as graph relations when a single global sequence would be too brittle.
- Semantic sidecars now also score selected-IR stream assignment quality. `semantic_stream_assignment_id_accuracy` and `semantic_stream_assignment_type_accuracy` check whether labelled members carry the expected `reading_order_stream_id` and normalized stream type, which is the metric to watch for image-source OCR/structure JSON and translated re-rendering. Stream successor/predecessor metrics still measure local order; assignment metrics measure whether the semantic layer created the right local translation streams. Type-confusion counts such as `grid-island=>body` make regressions actionable by stream class.
- Pure table-like grids use `table-row-major-v1`, so table cells stay row-major without being reported as an unknown visual-order fallback.
- Native PDF extraction now preserves image blocks, maps common paper fonts to closer browser font families, renders simple line drawings and supported non-rectangular drawing paths as SVG, and uses local raster fallback for dense vector figures.
- Image sources are first-class inputs: PNG/JPEG/TIFF/WebP/BMP files enter `DocumentIR.source` with `source_type = "image"`, a full-page source visual layer, image-DPI coordinate mapping, and OCR/structure-JSON-driven editable anchors instead of being wrapped as fake PDFs. Image-source IR keeps `source` / `source_path` as the identity and does not auto-fill `source_pdf`.
- `DocumentIR.metadata.semantic_layer` records whether the semantic layer came from native PDF extraction, structure JSON, OCR JSON, OCR fallback, or only the source visual layer. Benchmark reports expose those values as `semantic_layer_driver`, `semantic_layer_payload_kind`, and `semantic_layer_structure_role`.
- Native extraction now has an `image-only` OCR fallback for scanned/screenshot PDFs: textless high-image-coverage pages keep their source image layer and gain transparent `native-ocr` editable anchors.
- Native PDF extraction exposes benchmarkable font profiles: `browser-default` for stable baseline numbers and `local-urw` for explicit local Nimbus/DejaVu experiments.
- Benchmark `--font-profile auto` runs both stable and local-URW candidates, records both candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--font-size-scale auto` runs a small CSS font-size sweep, records candidate artifacts, and selects the higher visual-similarity case per PDF.
- Benchmark `--text-fit auto` compares normal editable HTML text with an SVG text-fit layer that uses PDF run bboxes and `textLength` to match line widths while retaining a transparent editable proxy.
- Benchmark `--html-mode auto` compares the structured redraw path with fidelity overlay paths and selects the higher visual-similarity case per PDF.
- Benchmark `--fidelity-background auto` compares SVG page backgrounds with raster page backgrounds for fidelity mode. SVG keeps a vector/zoom-friendly source layer; raster often wins strict pixel parity on complex pages.
- `fidelity` HTML mode keeps SVG or raster page backgrounds visible while overlaying transparent editable coordinate nodes. Print hides unchanged overlays so source-preservation measures the background layer, while edited/translated nodes print as local white-background replacement overlays.
- Benchmark printing normalizes exported page boxes to the source PDF dimensions, avoiding Chromium's 1px A4 page-size quantization from showing up as a persistent dimension mismatch. It also trims browser-added trailing blank pages after fixed-size HTML printing when the expected source page count is known.
- Structured HTML text lines use PDF bbox-width alignment (`text-align-last: justify`) to better reproduce justified PDF word spacing while keeping editable source text.
- Short superscript/subscript text runs can be positioned by source span bbox, with guards that avoid long baseline-only body lines.
- `column-flow-v1` can detect real academic two/three-column pages from repeated left-edge anchors, with coverage checks that avoid sparse author grids.
- Table-like grid protection now requires repeated anchors to look like text-flow columns before bypassing row-major order, so short financial/table cells are not read down columns.
- Mixed academic pages can now bypass the table-grid guard when repeated left-edge anchors strongly cover the body text, so formula/table noise no longer forces the whole page back to visual order.
- Mixed table/body pages can now use `mixed-table-column-flow-v1`: repeated short-cell table islands remain row-major, while surrounding non-table text still contributes to body-column detection.
- Weak-column pages can now fall back to `spatial-graph-v1` when repeated x anchors are unstable, while table-like pages and existing high-confidence column/table flows keep their current strategies.
- Weak-column pages can now also fall back to `box-flow-v1` when candidate-order disagreement, balanced x split, vertical overlap, and column separation all support a column-major order.
- Sparse weak-column pages can now use `successor-consensus-arbitration-v1` when selected fallbacks decline the page but independent non-visual candidates still agree on the local successor path; multiple detected handoffs become `column_count` / `column_index` metadata instead of collapsing to two columns.
- Benchmark records box-flow candidate disagreement so complex samples can be prioritized for semantic labels, model evidence, or rule refinement before changing the default ordering path.
- Table-dominated pages now use `table-row-major-v1`, separating intentional table reading order from low-confidence `visual-yx` fallback.
- Formula fragments are guarded by rejecting table-candidate rows that reuse the same repeated x slot, preserving Transformer-XL page-3 semantic order while keeping real table islands active.
- Running page headers/footers near the page margins are tagged as `page-artifact` and removed from body-column inference while staying editable/visible in the IR and HTML.
- Sidebar/marginalia detection estimates the main print space from wider body lines, routes narrow grouped text outside that space as `reading_order_scope = sidebar`, and orders it after the primary body flow instead of treating it as an extra body column.
- Footnote detection routes compact bottom-zone notes as `reading_order_scope = footnote`, keeping them after body columns but before sidebars and footer artifacts.
- Reading-order assignments now expose bounded heuristic confidence plus evidence tags such as `recursive-xy-cut`, `column-flow`, `repeated-left-edge`, `spatial-graph`, `horizontal-overlap-chain`, `multi-head-flow`, `table-row-major`, `table-island-row-major`, `grid-island-row-major`, `local-structure-grid`, `caption-label`, `caption-target-proximity`, `cross-column-caption`, `page-edge-artifact`, `footnote-secondary-flow`, `bottom-note-zone`, `sidebar-secondary-flow`, and `external-structure-order`.
- Dense list ordering uses a tighter row bucket so adjacent rows in web-to-PDF pages do not collapse into one reading-order row.
- PaddleOCR-VL / PP-StructureV3 / Docling / ROOR-style JSON can be loaded as external structure evidence and fused into native or image-source elements by bbox coverage and text similarity. Explicit block order, Docling `body.children`, structured parsing-list position, and nested child block order can become `external_structure_order` evidence when their provenance bboxes match elements; ROOR-style `document` lists are treated as unordered segments unless relation edges are present. PP-StructureV3 `table_res_list` and Docling `data.table_cells` / `grid` entries now become specific `table_cell` regions with row-major `external_structure_order_subindex`, so table islands can recover local cell order even under one parent table block. PP table cells can also seed image-source OCR anchors directly from `table_ocr_pred`, and PP `overall_ocr_res`, `text_paragraphs_ocr_res`, `formula_res_list`, and `seal_res_list` can seed text/formula/seal anchors for image sources while remaining unordered region evidence by default. Docling `furniture.children` feeds page-artifact role/stream evidence without becoming body order evidence; unordered `layout_det_res.boxes` stays label/region evidence only. Structure JSON relation fields such as `successor_edges`, `ro_linkings`, `reading_order_edges`, `precedence_edges`, `relations`, and `reading_streams` can resolve to matched elements, raw OCR anchor ids/refs, zero-based structure-list indices, or text aliases from text-only structure lists, then drive a safe path-cover reading-order reassignment before falling back to block order. Stream-level `ro_linkings` / `reading_order_*` aliases are also parsed as both relation edges and stream members. `reading_streams` / `streams` write translation-local `reading_order_stream_*` metadata, and relation-only sidecars now derive `external-relation-*` streams from safe successor chains, so structure-only sidecars can group OCR/image text into body/sidebar/table/grid/card streams even without explicit stream annotations. More specific child regions win ties over parent regions, so matched model labels can feed page-artifact, footnote, sidebar, caption, table-island, and explicit card/grid/product/tile `grid-island` reading streams. Plain list labels stay list evidence rather than card-grid evidence.
- `benchmark-structure-ab` now runs native-only and native-plus-structure reports side by side, then emits `structure_ab_report.json` / `structure_ab_summary.csv` with deltas for visual similarity, reading-order risk, grid-island elements, structure matches, page/stream `needs-structure-evidence`, same-stream/cross-stream replacement conflict targets, semantic successor metrics, semantic relation/stream/assignment missing-label counts, and semantic stream-assignment id/type accuracy when labels exist.
- Benchmark now accepts image sources directly with `--input-kind image`; visual scoring compares the source image layer against the rendered HTML-to-PDF output at `--image-dpi`, while OCR/structure JSON remains the semantic driver and is visible in `semantic_layer_*` case/summary fields.
- `--translation-stress pseudo-expand` now writes deterministic pseudo-expanded `translated_text` during benchmark runs, making translated replacement risk measurable without coupling the benchmark to any specific translation service.
- The current JD/PUMA/web-HN translation-stress rerun covers 15 pages with no page-count or dimension mismatches. Mean visual similarity is `0.81899535`; total replacement conflicts remain high at 565/567, making mask/fitting/conflict reduction the next fidelity target.
- Native PDF and OCR JSON paths share the same `scriptorium.reading_order` module.
- Structured HTML exposes reading-order strategy, region, scope, artifact, sidebar, stream id/type/index, confidence, evidence, and explicit translation target/stream attributes.
- Benchmark reports now include source-neutral `source`, compatibility `source_pdf`, `image_count`, `multi_column_element_count`, `column_flow_element_count`, `mixed_table_column_flow_element_count`, `grid_island_element_count`, `table_row_major_element_count`, `spatial_graph_element_count`, `box_flow_element_count`, `successor_consensus_arbitration_element_count`, `recursive_xy_cut_element_count`, `reading_order_stream_count`, `reading_order_stream_type_counts`, caption counts, caption target coverage/counts, box-flow/relation-graph/successor-consensus disagreement/support metrics, relation-style semantic sidecar metrics, semantic stream assignment accuracy and confusion metrics, structure-relation semantic candidate metrics, page-level candidate recommendation counts, stream-level candidate recommendation counts, fidelity replacement overflow/conflict/fit-scale metrics, stream-local replacement diagnostics, replacement conflict stream-pair attribution, `reading_order_strategy_counts`, semantic-layer driver counts, font profile, and structure evidence match/reorder/relation-reorder/stream/order-source/relation-edge counts.
- Benchmark reports now include text-run, mixed-inline-style, layout-region, raster-policy, raster-fallback, OCR fallback, auto font-profile candidate, reading-order footnote/sidebar/confidence/evidence counts, and detailed reading-order risk diagnostics.
- Built-in fixtures and selected external PDFs use `.semantic-order.json` sidecars and benchmark semantic order with pairwise order accuracy, labelled successor-edge accuracy, normalized sequence similarity, candidate-vs-selected disagreement, and sidecar-scored candidate order metrics.

Current benchmark coverage:

| Sample | Multi-column elements | Mixed table-flow elements | Table row-major | Spatial graph | Box-flow elements | Captions | Box-flow pairwise disagreement | Box-flow successor disagreement | Page artifacts | Footnotes | Sidebars | OCR text | Semantic GT | Order accuracy | Successor edges | Visual similarity |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Built-in fixtures | 20 | 0 | 18 | 0 | 0 | 0 | 0.19494585 | 19/47 | 0 | 0 | 0 | 0 | yes | 1.0 | 47/47 | 0.9906702 |
| arXiv Attention paper | 163 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 33/33 | 0.96840246 |
| ACL Transformer-XL paper | 1213 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 41/41 | 0.95679576 |
| ACL Transformer-XL first 3 pages, caption-flow | 321 | 0 | 0 | 0 | 0 | 3 figure | 0.0825672 | 142/318 | 1 | 7 | 0 | 0 | partial | 1.0 | 41/41 | 0.98160664 |
| Hacker News print PDF | 0 | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | n/a | 0 | partial | 1.0 | 24/24 | 0.9800288 |
| PUMA 2024 Annual Report, first 12 pages | 217 | 238 | 0 | 0 | 0 | 0 | 0.17460108 | 199/509 | 20 | 2 | 36 | 0 | no | n/a | n/a | 0.9795117 |
| JD homepage screenshot PDF | 0 | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 127/133 | 0 | 0 | 0 | 134 | no | n/a | n/a | 0.99576887 |
| JD homepage screenshot PNG | 0 | 0 | 0 | 0 | 0 | 0 | 0.43833464 | 128/133 | 8 | 0 | 0 | 134 | no | n/a | n/a | 0.99236799 |

Current reading-order evidence coverage:

| Sample | RO confidence | Table row-major | Footnote elements | Sidebar elements | Caption elements | Low-confidence RO elements | Evidence highlights |
|---|---:|---:|---:|---:|---:|---:|---|
| Built-in fixtures | 0.80113208 | 18 | 0 | 0 | 0 | 0 | 18 `table-row-major`, 20 `recursive-xy-cut`, 20 horizontal/vertical whitespace cuts |
| ACL Transformer-XL first 3 pages | 0.9552648 | 0 | 7 | 0 | 3 figure | 0 | 321 `column-flow`, 321 `repeated-left-edge`, 3 `caption-label`, 1 `cross-column-caption` |
| PUMA 2024 Annual Report, first 12 pages | 0.82476488 | 0 | 2 | 36 right | 0 | 0 | 36 `sidebar-secondary-flow`, 2 `footnote-secondary-flow`, 20 `page-edge-artifact`, 46 `table-island-row-major` |
| JD homepage screenshot PDF | 0.83 | 0 | 0 | 0 | 0 | 0 | 134 `recursive-xy-cut`, 134 horizontal/vertical whitespace cuts |
| JD homepage screenshot PNG | 0.77151567 | 0 | 0 | 0 | 0 | 0 | 134 `recursive-xy-cut`, 134 horizontal/vertical whitespace cuts, direct image-source OCR anchors |

The current built-in and external benchmark set reports 0 `spatial-graph-v1` and 0 `box-flow-v1` elements. That is intentional for this capability pass: both fallbacks are covered by weak-column unit tests and are guarded so they do not replace stronger repeated-anchor, table, sidebar, caption, footnote, or XY-Cut evidence on existing samples.

Box-flow disagreement is a triage metric, not a semantic score. Transformer-XL's low pairwise ratio (`0.0825672`) is consistent with its labeled semantic order staying at `1.0`, while its 142/318 successor disagreement shows the column-biased candidate still loses many immediate local edges. JD's high pairwise ratio (`0.42778588`) and extreme successor disagreement (127/133) flag dense OCR/web layout where semantic labels or model structure evidence are needed before changing ordering rules.

Current relation-graph candidate diagnostics:

| Sample | Relation pairwise disagreement | Relation successor disagreement | Box-flow successor disagreement | Reading-order decision |
|---|---:|---:|---:|---|
| Built-in fixtures | 6/277 | 3/47 | 19/47 | diagnostic only; selected semantic order remains unchanged |
| ACL Transformer-XL first 3 pages | 3526/17077 | 111/318 | 142/318 | lower local disagreement than box-flow, but pairwise disagreement blocks default selection |
| PUMA 2024 Annual Report, first 12 pages | 2473/15166 | 166/509 | 199/509 | candidate evidence for annual-report sidecar/model arbitration |
| JD homepage screenshot PDF | 1927/8911 | 117/133 | 127/133 | still high risk; needs semantic labels or external structure evidence |

The relation graph improves local successor disagreement on current complex samples, but this is not enough to promote it to the default orderer. The benchmark can now score relation-graph, structure-relation, successor-consensus, box-flow, visual-yx, and external-structure candidates directly against semantic sidecars when labels exist, then report whether the best candidate should be considered over the selected order. For unlabeled samples, page-level candidate diagnostics expose whether a high-support consensus disagrees with the selected order or whether the page simply lacks reliable structure evidence. The first runtime arbitration path is intentionally narrow and only repairs sparse visual-order fallbacks with strong non-visual agreement. The next capability step is broader runtime arbitration: combine selected native heuristics, relation-graph successor edges, structure-relation page-scope/caption-target evidence, candidate successor consensus, page-level recommendations, sidecar labels, roles, captions, tables, and optional Paddle/PP-Structure/Docling evidence, then only switch order when independent evidence supports it.

The reading-stream layer follows the same architecture as PDF article threads: complex layouts can expose explicit local paths through non-contiguous regions. Grid-island streams extend this to portal/card layouts where visual reconstruction is mostly solved by the background layer but translation/editing needs stable local structure. It also aligns with relation/path-cover reading-order methods, where the durable signal is often local successor or precedence relations rather than one brittle serialized order.

The first built-in semantic candidate baseline is `outputs/benchmark-semantic-candidate-metrics-v1`: selected order scores 47/47 successor edges, visual-yx scores 34/47, box-flow scores 28/47, and relation graph scores 44/47. This gives arbitration work a direct sidecar-scored signal without tuning against external complex samples that do not yet have labels.

Current `--font-profile auto` sweep:

| Sample | Selected profile | Default visual | Auto visual | Delta |
|---|---|---:|---:|---:|
| arXiv Attention paper | `local-urw` | 0.93202666 | 0.93871982 | +0.00669316 |
| ACL Transformer-XL paper | `browser-default` | 0.93358709 | 0.93358709 | +0.00000000 |
| Hacker News print PDF | `browser-default` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.94854752 | 0.95077857 | +0.00223105 |

Current `--font-size-scale auto` sweep with `browser-default`:

| Sample | Selected scale | Default visual | Auto-scale visual | Delta |
|---|---:|---:|---:|---:|
| arXiv Attention paper | `0.99` | 0.93202666 | 0.93670278 | +0.00467612 |
| ACL Transformer-XL paper | `1.0` | 0.93358709 | 0.93358709 | +0.00000000 |
| Hacker News print PDF | `1.0` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.94854752 | 0.95010622 | +0.00155870 |

Combined `--font-profile auto --font-size-scale auto` on Attention selected `local-urw` + `1.0`, matching the current best Attention score of `0.93871982`.

Current `--font-size-scale auto --text-fit auto` sweep with `browser-default`:

| Sample | Selected text fit | Previous best structured | Auto text-fit visual | Delta |
|---|---|---:|---:|---:|
| arXiv Attention paper | `0.99 + svg` | 0.93670278 | 0.96840246 | +0.03169968 |
| ACL Transformer-XL paper | `0.99 + svg` | 0.93358709 | 0.95679576 | +0.02320867 |
| Hacker News print PDF | `none` | 0.9800288 | 0.9800288 | +0.00000000 |
| Three-sample mean | mixed | 0.95010622 | 0.96840901 | +0.01830279 |

Current `--html-mode fidelity` SVG overlay sweep:

| Sample | Structured visual | Fidelity visual | Delta | Vector background pages |
|---|---:|---:|---:|---:|
| arXiv Attention paper | 0.93202666 | 0.98809524 | +0.05606858 | 15 |
| ACL Transformer-XL paper | 0.93358709 | 0.97636829 | +0.04278120 | 11 |
| Hacker News print PDF | 0.9800288 | 0.99490923 | +0.01488043 | 2 |
| Three-sample mean | 0.94854752 | 0.98645759 | +0.03791007 | 28 |

Current `--html-mode auto --fidelity-background auto` sweep:

| Sample | Best structured | SVG fidelity | Raster fidelity | Auto visual | Selected path |
|---|---:|---:|---:|---:|---|
| arXiv Attention paper | 0.96840246 | 0.98809524 | 1.0 | 1.0 | `fidelity/raster` |
| ACL Transformer-XL paper | 0.95679576 | 0.97636829 | 0.98096887 | 0.98096887 | `fidelity/raster` |
| Hacker News print PDF | 0.9800288 | 0.99490923 | 1.0 | 1.0 | `fidelity/raster` |
| Three-sample mean | 0.96840901 | 0.98645759 | 0.99365629 | 0.99365629 | mixed |

The fidelity path now has a minimal edit-print path: edited or translated nodes print as local white-background replacement overlays. Raster backgrounds pushed two current real samples to perfect visual parity and raised Transformer-XL from `0.97636829` to `0.98096887`, but SVG backgrounds remain important for vector inspection and future non-raster editing strategies. The remaining Transformer-XL difference is no longer reading-order driven: semantic order is `1.0`, risk is `0.08879982 / low`, and the worst raster diff is page 7 with `0.01903113`.

Current additional complex-source baselines:

| Sample | Scope | Structured visual | SVG fidelity | Raster fidelity | Selected path | Notes |
|---|---:|---:|---:|---:|---|---|
| PUMA 2024 Annual Report | first 12 / 345 pages | 0.73733248 | 0.97885835 | 0.9795117 | `fidelity/raster` | 815 elements, 521 editable, 99 direct column-flow elements, 238 mixed-table-flow elements, 20 header artifacts, 2 footnote elements, 36 right sidebar elements, high semantic-risk without sidecar |
| JD homepage screenshot PDF | 1 / 1 page | 0.99536129 | 0.99536129 | 0.99576887 | `fidelity/raster` | image-only screenshot PDF, 134 transparent OCR edit anchors, now handled by recursive XY-Cut rather than false table-flow |

The JD gain is not a visual-score gain; it is a structural/editability gain. The image-only PDF previously reported 1 image element and 0 editable text nodes. With generic OCR fallback it reports 135 elements, 134 editable `native-ocr` nodes, and keeps the same selected visual score. PUMA remains unchanged on OCR counts because its sampled pages expose native PDF text. Its latest mixed-table/artifact/sidebar/footnote pass keeps the pixel score unchanged, marks 20 repeated header candidates, routes 36 right-side marginal/sidebar text nodes and 2 bottom-zone footnote nodes as secondary flow, and reduces table-like visual-yx pages to 0, with reading-order risk `0.35 / high`.

Current reading-order risk diagnostics example:

| Sample | Risk score | Risk level | Text-flow column pages | Visual-yx column pages | Repeated-anchor pages | Max anchors | Table-like pages | Table-like visual-yx | Unlabeled risk text |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|
| Built-in fixtures | 0 | low | 3 | 0 | 3 | 3 | 1 | 0 | 0 |
| ACL Transformer-XL after mixed-layout guard refinement | 0.08879982 | low | 10 | 1 | n/a | n/a | n/a | n/a | 277 |
| ACL Transformer-XL first 3 pages, formula-slot guard | 0.21573209 | medium | 3 | 0 | 3 | 2 | 1 | 0 | 277 |
| PUMA Annual Report, first 12 pages | 0.35 | high | 5 | 0 | 5 | 3 | 4 | 0 | 521 |

The extra repeated-anchor/table-like/sidebar/caption/footnote/spatial-graph/box-flow/relation-graph/successor-consensus counters and evidence counts make the risk score actionable: built-in pure tables no longer produce a high-risk visual-yx false positive, Transformer-XL now identifies 3 figure captions including 1 cross-column caption without changing its `1.0` semantic sidecar score, PUMA identifies 36 right-side secondary-flow nodes plus 2 footnote-flow nodes, current samples show the weak-column fallbacks have not taken over unrelated pages, relation-graph lowers local successor disagreement on several complex samples, and JD still remains a priority for semantic labeling or external structure evidence. The next work should focus on complex-document semantic labels, confidence calibration, candidate-order arbitration, and external structure evidence rather than only stronger global column detection.

## Next Optimization Options

1. Expand real semantic ground truth for complex PDFs

   The arXiv Attention sidecar covers 5 representative pages and 38 labeled text nodes. The Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels. Current ignored-text diagnostics show 147 unlabeled Attention nodes, 277 Transformer-XL nodes, and 69 web-HN table-cell nodes. The PUMA annual report first-12-pages benchmark now adds a high-risk non-paper sample with 99 direct column-flow elements, 238 mixed-table-flow elements, 2 footnote elements, 36 sidebar elements, and no semantic sidecar. Built-in fixtures now explicitly score 18 pure-table nodes as `table-row-major-v1`. Expand this to more pages and more document families, especially annual reports, equations, tables, footnotes, appendices, manuals, and additional web-to-PDF pages. Use `successor_edges`, `ro_linkings`, `reading_order_*`, and `precedence_edges` when a page has several acceptable global reading orders but clear local relation constraints; prefer id-based `document` segments when labels come from OCR/structure JSON.

2. Recursive XY-Cut refinement

   The first backend is implemented. Column-flow now tolerates formula noise between repeated anchors, supports up to three repeated text-flow columns, can split mixed table/body pages with local table islands, routes print-space-external sidebars and bottom-zone footnotes as secondary flow, identifies shallow figure/table/algorithm caption labels, links figure/table captions to nearby object targets, and emits confidence/evidence metadata. Spatial graph and guarded box-flow now cover weak-column fallback paths when repeated anchors are unstable, while box-flow, relation-graph, structure-relation, and successor-consensus diagnostics expose candidate-order disagreement on all benchmarked pages. Next refinements should calibrate caption-target proximity against real semantic sidecars, use target proximity inside candidate-order arbitration, and combine native heuristics, model structure evidence, relation-graph predictions, structure-relation scope ordering, and successor-edge consensus.

3. Vector renderer refinement

   SVG path output now handles supported PyMuPDF drawing items (`l`, `c`, `re`, `qu`) without using rectangular approximations. Dense local raster fallback still sacrifices editability inside diagrams. A `tables` raster policy was tested but is not the default because current real-paper/web scores dropped. The next step is preserving PDF clipping, blend modes, masks, and grouped draw ordering so more complex drawings can remain structured.

4. Refine edit masks and replacement fitting for fidelity mode

   `fidelity` mode now preserves source visuals and prints edited/translated nodes as local white-background replacement overlays. Translation should be applied by `data-scriptorium-translation-stream-id` so body, sidebar, table, and grid-card streams can be replaced independently. The first `fidelity-replacement-fit-v1` compositor expands local masks, pads text back to the source coordinate, shrinks long translated text, and exports overflow/conflict/fit-scale metrics into benchmark reports. The next step is improving this from bbox-level estimates to glyph-extents-aware masks and running real translated JD/PUMA/portal samples to optimize conflict counts alongside visual similarity.

5. Finer evidence-driven font, scale, and text-fit selection

   Benchmark-time `--font-profile auto`, `--font-size-scale auto`, and `--text-fit auto` now select between global candidates per PDF. The next step is to move from whole-document selection to per-page or per-font-cluster selection, without requiring a full multi-candidate print/compare pass for normal conversion. Current research found that most paper fonts are embedded Type1/PFA, which browsers cannot directly consume; usable TTF extraction is therefore only a partial solution. SVG text-fit also needs edit-state switching and line-height/baseline refinements so long translated replacements can reuse the same fitted layer safely.

6. Relation-graph and successor-consensus candidate arbitration

   The first geometry-only relation-graph candidate is implemented. It scores local successor edges from bbox geometry, guards table-like grids, selects an acyclic degree-constrained path cover with max-regret edge selection, and reports pairwise plus successor-edge disagreement. The structure-relation candidate now layers page-scope metadata, artifact/footnote/sidebar routing, caption-target proximity, and relation-graph body order into a sidecar-scored diagnostic. The successor-consensus candidate adds an ensemble layer over visual-yx, box-flow, relation-graph, structure-relation, and external-structure successor edges, with selected-edge support, edge coverage, conflicted-edge ratio, and agreement-level counts. Runtime external relation evidence also derives local translation streams when no explicit `reading_streams` are present, making relation-only sidecars useful for both ordering and post-translation replacement. Benchmark scores relation graph, structure-relation, successor-consensus, box-flow, visual-yx, and external-structure candidates directly against semantic sidecars and reports candidate-vs-selected deltas; for unlabeled PDFs it emits page-level review recommendations from selected-vs-consensus diagnostics. Runtime arbitration has started with a conservative sparse-column path that excludes visual-yx from the vote, requires column handoff evidence, and preserves multi-handoff column metadata. The next step is not to tune it against the current samples, but to broaden runtime arbitration so it can combine relation edges with structure-relation evidence, candidate consensus, role, caption/figure/table proximity, semantic sidecars, page-level recommendation categories, and optional model evidence before changing selected order. This follows recent work that treats reading order as relations rather than a single fragile permutation and should continue to be evaluated with successor-edge accuracy in addition to pairwise sequence accuracy.

7. Real model evidence A/B

   The `structure_evidence.py` bridge and benchmark `--structure-json` input are now implemented. Run real PaddleOCR-VL 1.6, PP-StructureV3 `save_to_json`, DoclingDocument JSON, and relation-order JSON outputs against the same sources and compare native/visual-only versus structure-driven runs. For digital PDFs, use model output to improve role/order/table/formula metadata while preserving native text/style. For image sources and scanned PDFs, use model output as the primary text source.

8. OCR fallback refinement

   The first image-only fallback uses page-level native-text absence plus image coverage as its trigger. Next refinements should add OCR confidence aggregation, per-region OCR for mixed native/scanned pages, language auto-detection, duplicated-text suppression when PDFs contain invisible OCR text, and optional Paddle/PP-Structure OCR evidence as a stronger replacement for the local Tesseract fallback.

9. Semantic-order benchmark expansion

   The first sidecar-based benchmark is implemented, including relation-style labels for complex pages. Expand it with real/hand-labeled documents and report:

   - normalized edit distance between expected and exported source text
   - column order accuracy
   - successor-edge accuracy for local reading-order continuity
   - relation successor / precedence accuracy for graph-style labels
   - table row-major preservation
   - figure/table caption proximity
   - footnote/header/footer order calibration and edge-case coverage

## Research References

- PyMuPDF text extraction and reading-order notes: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- PyMuPDF image extraction notes: https://pymupdf.readthedocs.io/en/latest/recipes-images.html
- PyMuPDF page API for vector drawings and table detection: https://pymupdf.readthedocs.io/en/latest/page.html
- pdf2htmlEX feature list for native text, font/position preservation, clipping, and image+hidden-text fallback: https://github.com/pdf2htmlEX/pdf2htmlEX/wiki/Feature-List
- PDF Association "Deriving HTML from PDF" specification: https://pdfa.org/download-area/specifications/Deriving_HTML_from_PDF.pdf
- W3C PDF reading-order technique PDF3: https://www.w3.org/TR/WCAG-TECHS/PDF3.html
- W3C PDF14 running headers and footers as pagination artifacts: https://www.w3.org/WAI/WCAG22/Techniques/pdf/PDF14
- W3C PDF4 artifact examples including page headers/footers: https://www.w3.org/TR/WCAG20-TECHS/PDF4.html
- OCR-D PAGE reading-order guidelines for print-space-external marginalia: https://ocr-d.de/en/gt-guidelines/trans/lyLeserichtung.html
- EPUB accessibility logical reading order and `aside` semantics for secondary content: https://idpf.github.io/a11y-guidelines/content/semantics/order.html
- PRImA reading-order representation/evaluation for complex layouts: https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf
- pdfminer.six `LAParams.boxes_flow`: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- Kendall tau for information ordering evaluation: https://aclanthology.org/J06-4002.pdf
- Modeling reading order as relations for visually-rich documents: https://aclanthology.org/2024.emnlp-main.540/
- LayoutReader / ReadingBank reading-order benchmark: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut++ reading-order recovery: https://arxiv.org/html/2504.10258v1
- Reading order inference for complex document layouts with graph/path-cover ordering: https://arxiv.org/html/2607.01018
- Graph-based document structure analysis with reading-order and logical relations: https://proceedings.iclr.cc/paper_files/paper/2025/file/cf3d7d8e79703fe947deffb587a83639-Paper-Conference.pdf
- Docling technical report: https://arxiv.org/html/2408.09869v5
- DoclingDocument concept and JSON structure notes: https://docling-project.github.io/docling/concepts/docling_document/
- LayoutParser paper: https://arxiv.org/abs/2103.15348
- PP-StructureV3 pipeline usage and multi-column reading-order recovery: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL 1.6 model usage: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- MuPDF/SVG plus transparent text-layer PDF-to-HTML pattern: https://github.com/OskarLebuda/rs-pdf
- BuildVu discussion of SVG/HTML5 hybrid PDF-to-HTML layout preservation and text modes: https://blog.idrsolutions.com/convert-pdf-to-html5-preserving-layout/
- Render-and-compare visual evaluation dataset pattern for OCR/HTML reconstruction: https://huggingface.co/datasets/gt-free-ocr-metrics/omnidocbench-render-compare
- External benchmark sample manifest: docs/external-benchmarks.md
