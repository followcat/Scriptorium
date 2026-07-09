<p align="center">
  <a href="../README.md"><img alt="Home" src="https://img.shields.io/badge/Home-README-2b6cb0"></a>
  <a href="implementation-notes.zh-CN.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E9%98%85%E8%AF%BB-blue"></a>
  <img alt="English" src="https://img.shields.io/badge/English-current-2f855a">
</p>

# Implementation Notes

## Source Boundary

Scriptorium now treats the input as a document source, not as a synonym for PDF. `render_source()` dispatches to the PDF renderer or the image renderer:

- Image sources (`PNG`, `JPEG`, `TIFF`, `WebP`, `BMP`) render as one-page `RenderedDocument` objects with `source_type = "image"`.
- PDF sources keep the native extraction path: PyMuPDF text/images/drawings, optional image-only OCR fallback, and optional structure JSON fusion.
- Image coordinates use `--image-dpi` to map source pixels into PDF-point space. The original pixels become the page visual layer, while OCR/structure JSON contributes editable text anchors and reading-stream evidence.
- `DocumentIR.source` is the source-neutral primary path. `source_path` mirrors it for older callers, and the old `source_pdf` field remains as a compatibility alias for PDF sources, legacy JSON, reports, and XML; image-source IR no longer fills `source_pdf` unless an old payload explicitly provided it.
- Native PDF extraction explicitly rejects image sources. Image semantics should come from OCR JSON or Paddle/PP-Structure/Docling/ROOR-style structure JSON, so the semantic layer is model/evidence-driven rather than inferred from a fake PDF wrapper.

Example:

```bash
scriptorium convert page.png \
  --input-kind image \
  --image-dpi 96 \
  --structure-json page.structure.json \
  --out-dir outputs/page-image
```

## OCR Backend Boundary

The core pipeline consumes normalized JSON and turns it into `DocumentIR`. This is intentional:

- PaddleOCR-VL 1.6 official examples use `from paddleocr import PaddleOCRVL`, create `PaddleOCRVL(pipeline_version="v1.6")`, run `pipeline.predict(...)`, and persist results with `save_to_json(...)`.
- PP-StructureV3 is documented and described as producing structured JSON/Markdown for document parsing, with finer coordinate-oriented output than a pure VLM result.
- Scriptorium should therefore treat Paddle outputs as an OCR adapter concern. The renderer, geometry, IR, HTML export, editing, translation, and quality comparison should stay independent from model runtime details.

Current implementation status:

- `--ocr-json` is the stable tested input for conversion quality work.
- `PaddleOcrAdapter` is isolated in `scriptorium.ocr` and intentionally lazy-imports `paddleocr`.
- `--structure-json` is the stable lightweight bridge for real model output. It accepts PaddleOCR-VL / PP-StructureV3 style JSON, DoclingDocument JSON, and relation-style `document` / `ro_linkings` payloads, then fuses region bbox, label, content, confidence, and external reading order back into `DocumentIR`.
- For image sources, `--structure-json` can also seed the initial text anchor layer when no separate `--ocr-json` is provided. Common `parsing_res_list` / `block_bbox` / `block_content` payloads and ROOR-style `document` segments with `box` / `text` are normalized into `native-ocr` text nodes before structure evidence is fused back onto them.
- `DocumentIR.metadata.semantic_layer` records the active semantic driver. Image cases report `structure-json`, `ocr-json`, `ocr-fallback`, or `visual-only`; native PDF cases report `native-pdf` and treat structure JSON as augmenting evidence unless the source itself is image-based.
- Native extraction has an `image-only` OCR fallback for scanned or screenshot PDFs. It triggers only when a page has no native text and image blocks cover most of the page, then emits `native-ocr` text anchors without replacing the original image element.
- `structure_evidence.py` parses nested `res`, `raw_results`, `pages`, `parsing_res_list`, `document`, and `layout_det_res.boxes` shapes. It also parses Docling `body.children` and `furniture.children` trees, resolves refs such as `#/texts/0` and `#/groups/0`, reads `prov` bbox/page evidence, supports ROOR-style `ro_linkings` as successor edges, and supports both top-left and bottom-left bbox origins. The next model-specific step is running real PaddleOCR-VL, PP-StructureV3, Docling, and relation-order payloads through this bridge and tracking native-only versus native-plus-structure deltas.

## Annotation Layer

The structured HTML export must not rely on a hand-authored stylesheet to make a demo look right. The pipeline now has an explicit annotation pass:

1. Extraction writes raw evidence into `DocumentIR`:
   - native PDF lines become editable text elements with font size, font family, weight, color, bbox, and source metadata
   - native PDF spans are preserved under `element.metadata.text_runs` with run text, bbox, font, weight, style, color, script, and source coordinates
   - native PDF image blocks become local `image` elements with `source_crop`, bbox, dimensions, and `native-image` source metadata
   - native PDF drawings become shape elements with fill/stroke/border metadata and `shape_geometry`; simple lines keep `line_points_pdf`, and supported multi-item drawing paths keep `svg_path_pdf`
   - dense vector regions can become local raster fallback image elements with `native-raster-region` source metadata
   - OCR fallback elements keep bbox, type, confidence, text runs, style hints, `native-ocr` source metadata, OCR language, and OCR DPI
2. `annotate_document()` assigns recognized marks:
   - `role`: `heading`, `paragraph`, `table-cell-text`, `table-shape`, `figure-shape`, `separator-shape`, etc.
   - `source_kind`: `native-pdf`, `native-drawing`, `json-fallback`, etc.
   - `style_id`: stable style bucket recorded under `DocumentIR.metadata.styles`
   - `text_run_count` and `mixed_inline_style`: whether the text element contains multiple native PDF style runs
   - `layout_group_id`: shared region id such as `table-001`, `figure-001`, or `separator-001`
   - `layout_group_kind`: inferred region kind for downstream editing and translation tools
   - `semantic_order`, `visual_order`, `column_index`, `column_count`, `column_span`, and `flow_segment_index`
   - `reading_order_strategy` and `reading_order_region_path`
   - `reading_order_scope`, `reading_order_artifact_type`, and `reading_order_sidebar_type` for page-level running headers/footers, footnotes, and secondary sidebar/marginalia content
   - `reading_order_stream_id`, `reading_order_stream_type`, and `reading_order_stream_index` for page-local reading streams such as `body-main`, `body-segment-002`, `footnote`, `sidebar-right`, `page-artifact-header`, `caption-figure-*`, `table-island-001`, and `grid-island-001`
   - body segment streams are assigned only when the page has structural break evidence such as full-width flow breaks or multiple recursive XY-Cut regions; the first continuous body chain stays `body-main`
   - `reading_order_caption_target_id`, `reading_order_caption_target_kind`, `reading_order_caption_target_position`, `reading_order_caption_target_confidence`, and target bbox/source metadata when a figure/table caption is locally associated with an object
   - `reading_order_confidence`, `reading_order_evidence`, and `reading_order_evidence_summary` for explaining the geometry/model evidence behind each ordering decision
   - `editable` and `edit_target`: whether the node maps to editable text
   - `bbox_pdf` and `bbox_px`: original coordinate evidence
   - external structure labels from Paddle/PP-Structure/Docling evidence, mapped to roles such as `formula`, `running-header`, `footer`, `caption`, and `table-cell-text`
3. `DocumentIR.metadata.layout_regions` records each inferred region with its page index, bbox, kind, confidence, and contributing shape ids.
4. The HTML exporter exposes those marks as DOM attributes:
   - `data-scriptorium-role`
   - `data-scriptorium-source`
   - `data-scriptorium-style-id`
   - `data-scriptorium-layout-group`
   - `data-scriptorium-layout-kind`
   - `data-scriptorium-layout-confidence`
   - `data-scriptorium-shape-geometry`
   - `data-scriptorium-shape-line`
   - `data-scriptorium-run-index`
   - `data-scriptorium-run-style-id`
   - `data-scriptorium-run-script`
   - `data-scriptorium-semantic-order`
   - `data-scriptorium-visual-order`
   - `data-scriptorium-column-index`
   - `data-scriptorium-column-count`
   - `data-scriptorium-column-span`
   - `data-scriptorium-flow-segment`
   - `data-scriptorium-reading-order-strategy`
   - `data-scriptorium-reading-order-region`
   - `data-scriptorium-reading-order-scope`
   - `data-scriptorium-reading-order-artifact`
   - `data-scriptorium-reading-order-sidebar`
   - `data-scriptorium-reading-order-caption`
   - `data-scriptorium-reading-order-stream-id`
   - `data-scriptorium-reading-order-stream-type`
   - `data-scriptorium-reading-order-stream-index`
   - `data-scriptorium-caption-target-id`
   - `data-scriptorium-caption-target-kind`
   - `data-scriptorium-caption-target-source`
   - `data-scriptorium-caption-target-position`
   - `data-scriptorium-caption-target-distance`
   - `data-scriptorium-caption-target-confidence`
   - `data-scriptorium-reading-order-confidence`
   - `data-scriptorium-reading-order-evidence`
   - `data-scriptorium-editable`
   - `data-scriptorium-edit-target`
   - `data-scriptorium-translation-target`
   - `data-scriptorium-translation-stream-id`
   - `data-scriptorium-translation-stream-type`
   - `data-bbox-pdf`
   - `data-bbox-px`

In `structured` mode the exporter intentionally does not include the page background image. The result is made of editable text nodes, structural shape nodes, native image nodes, and local raster fallback nodes, all tied back to recognized evidence in the IR.

For image-only pages, the native image node remains the visual layer in `structured` mode. `native-ocr` nodes are transparent by default and become visible only on hover/focus, preventing duplicated text while still exposing editable DOM anchors and XML/IR text.

Text runs are a source-fidelity layer, not the edit storage model. When `edited_text` or `translated_text` is present, the exporter renders the replacement text as a plain editable node so stale source spans do not distort new content. Translation tools should use `data-scriptorium-translation-stream-id` / `data-scriptorium-translation-stream-type` to batch body, sidebar, footnote, table, and grid-card streams independently, then write replacements into `translated_text` for the same HTML/PDF rendering path.

In `fidelity` mode, edited/translated replacements use `fidelity-replacement-fit-v1`. The exporter expands the local white mask, records `data-scriptorium-replacement-mask-padding`, keeps the replacement text aligned to the original bbox with CSS padding, applies `data-scriptorium-replacement-fit-scale` for long text, and flags `data-scriptorium-replacement-overflow` / `data-scriptorium-replacement-conflict` with neighboring element ids when the replacement needs review.

## Native Visual Fidelity Layer

Complex scientific PDFs often lose visual score for reasons unrelated to reading order: embedded figures are image blocks, LaTeX fonts are not named like browser fonts, and dense vector graphics may depend on transparency, clipping, and draw ordering that a simple rectangle exporter cannot reproduce.

The native PDF path now handles these cases:

- `native-image`: PyMuPDF `get_text("dict")` image blocks are written as local image assets and exported as positioned image elements. These are source PDF image blocks, not whole-page screenshots.
- `native-ocr`: when a page has no native text and image area coverage is at least 60%, PyMuPDF/Tesseract OCR is attempted with the requested `--ocr-language` and `--ocr-dpi`. If OCR succeeds, the page keeps its native image element and gains transparent editable text anchors.
- Font family normalization maps common PDF names such as `NimbusRomNo9L`, `CMR`, `CMMI`, `CMSY`, `SFTT`, `LiberationSans`, and Nimbus/Courier variants to closer browser families.
- Native extraction records a `font_profile`. `browser-default` is the stable default used for public benchmark numbers; `local-urw` is an explicit A/B profile that prefers locally installed Nimbus/DejaVu families for papers whose PDF fonts match those metrics better.
- Current A/B evidence is mixed: `local-urw` improved Attention from `0.93202666` to `0.93871982`, but reduced Transformer-XL from `0.93358709` to `0.90096092`. Keep `browser-default` as the default until profile selection can be driven by page/font evidence rather than a global switch.
- Benchmark-time `--font-profile auto` now runs both `browser-default` and `local-urw`, records both candidate artifacts, and selects the higher visual-similarity result per PDF. On the current real sample set it keeps Transformer-XL and Hacker News on `browser-default`, selects `local-urw` for Attention, and raises the three-sample mean visual similarity from about `0.94855` to about `0.95078`.
- Native extraction accepts `font_size_scale`, and benchmark `--font-size-scale auto` runs `0.99` and `1.0` candidates. On the current real sample set this improves Attention with `browser-default` from `0.93202666` to `0.93670278`, while Transformer-XL and Hacker News keep `1.0`.
- Structured export accepts `text_fit`. `--text-fit svg` emits a per-text-node SVG layer using source run bboxes, baseline origins, and SVG `textLength` / `lengthAdjust="spacingAndGlyphs"` to fit PDF line widths. The same DOM node keeps a transparent editable proxy so the source text remains addressable for later editing.
- Benchmark `--text-fit auto` runs normal HTML text and SVG text-fit candidates, records both artifacts, and selects the higher visual-similarity result per PDF. Combined with `--font-size-scale auto`, it improves current structured paper scores from Attention `0.93670278` to `0.96840246` and Transformer-XL `0.93358709` to `0.95679576`; Hacker News selects `none` because ordinary browser text remains closer there.
- `fidelity` HTML mode renders the original page as an SVG or raster background while keeping transparent editable text nodes aligned by PDF coordinates. During print, unchanged fidelity overlay nodes are hidden and only the source-preservation background is printed. Edited or translated nodes are printed as local white-background replacement overlays, so the mode now has a minimal edit/translation-print path.
- `fidelity_background="svg"` keeps the page background vector/zoom-friendly when PyMuPDF SVG export is available. `fidelity_background="raster"` uses the already rendered page PNG, which often reduces browser SVG interpretation and anti-aliasing differences during print/compare.
- Benchmark `--fidelity-background auto` evaluates both SVG and raster fidelity backgrounds. On the current three real samples, raster was selected for all three: Attention `0.98809524 -> 1.0`, Transformer-XL `0.97636829 -> 0.98096887`, and web-HN `0.99490923 -> 1.0`.
- Benchmark `--html-mode auto` evaluates structured redraw candidates and fidelity overlay candidates, then keeps the higher `visual_similarity` result. Fidelity candidates collapse no-op calibration axes (`font-size-scale auto`, `text-fit auto`, and `font-profile auto`) to one browser-default candidate because the visible output comes from the source background rather than redrawn browser text. On the current three real samples, `--html-mode auto --fidelity-background auto` selects `fidelity/raster` for all three and raises the mean from `0.96840901` structured to `0.99365629` auto.
- Benchmark PDF printing passes known source page sizes into `print_html_to_pdf()`, which normalizes the exported PDF page boxes after Chromium printing. This fixes the previous Transformer-XL A4 width quantization where every rendered comparison page was 1px narrower than the source.
- When source page count is known, HTML print export also trims browser-added blank tail pages that contain no text, images, or annotations and only empty/white drawings. This keeps translation-stress reports from being dominated by Chromium pagination artifacts while preserving real overflow pages that contain content.
- Structured text lines keep `white-space: pre` and add `text-align-last: justify` in the default `structured` mode. Each line still uses its extracted PDF bbox, but the browser can expand word spacing to match justified PDF lines more closely. SVG text-fit is now the stronger optional path when browser font metrics are the dominant error.
- Short mixed text runs with script positioning, such as author footnote marks and compact superscripts, can be rendered as positioned child spans. The gate intentionally excludes long baseline-only citation/body lines because fully absolute run placement caused Transformer-XL page scaling and major visual regressions.
- `native-drawing`: simple lines render as SVG `<line>`. Supported non-rectangular drawing items (`l`, `c`, `re`, `qu`) render as positioned SVG `<path>` with fill/stroke opacity, avoiding the previous rectangular approximation for polygons and rounded paths.
- `native-raster-region`: when a page has a dense vector cluster with many line drawings, Scriptorium clips just that local region from the source PDF and exports it as one image node. Text and shape nodes whose centers fall inside that region are hidden to avoid duplicate rendering. Captions and surrounding body text remain editable.
- `--raster-policy tables` is available for explicit experiments with complex table vector regions, but it is not the default. On the current Attention, Transformer-XL, and Hacker News set it reduced visual similarity because Chrome's reprinted bitmap regions introduced more anti-aliasing/compression difference than the structured table renderer did.

This is an explicit fidelity/editability tradeoff: ordinary text, tables, separators, simple drawings, and supported SVG paths stay structured; very dense diagrams become local raster nodes until the vector renderer supports the required clipping, grouping, and blend-mode semantics.

## External Structure Evidence Fusion

PaddleOCR-VL, PP-StructureV3, and Docling are best treated as optional evidence providers rather than replacements for native PDF extraction. Native extraction usually gives better font/style/bbox fidelity for digital PDFs, while document models can add missing OCR, layout labels, table/formula/chart regions, and reading-order block predictions.

`src/scriptorium/structure_evidence.py` implements the current bridge:

- `normalize_structure_evidence(payload, document)` accepts common Paddle JSON shapes, including `parsing_res_list` blocks with `block_bbox`, `block_label`, `block_content`, and `block_order`.
- Explicit block order remains the strongest order evidence. When `parsing_res_list`, `blocks`, or `elements` omit `block_order`, their list position is recorded as weak `implicit-list` order; nested `children`, `sub_blocks`, `sub_regions`, `items`, `cells`, and similar child lists are traversed in depth-first order. Pure `layout_det_res.boxes` detector output does not get implicit order.
- It also accepts DoclingDocument JSON. The parser traverses `body.children` in order, resolves JSON pointer refs into `texts`, `tables`, `pictures`, `key_value_items`, and `groups`, and turns item `prov` entries into page-local structure regions. Docling `furniture.children` is also parsed as non-body structure evidence for page headers, footers, and similar artifacts; those regions feed roles/streams but do not create body block-order evidence.
- Docling bboxes are treated as PDF page coordinates. `coord_origin = TOPLEFT` is used directly; `coord_origin = BOTTOMLEFT` is flipped through the current page height before matching native elements.
- Pixel bboxes are converted to PDF-point bboxes using the page render scale already stored in `DocumentIR`.
- `apply_structure_evidence(document, payload)` aligns model regions to native elements by element bbox coverage and text similarity.
- When a parent region and a more specific child region both cover the same text equally well, the smaller child region wins the match. This lets nested card/product/tile structures drive local reading streams instead of being swallowed by the parent grid bbox.
- Matched text elements receive `structure_evidence`, `external_structure_label`, `external_structure_order`, and `external_structure_order_source` metadata.
- Structure JSON can also provide relation evidence through page-level or stream-level `successor_edges`, `successor_relations`, `ro_linkings`, `reading_order_edges`, `reading_order_relations`, `reading_order_linkings`, `precedence_edges`, `order_edges`, `relations`, `reading_streams`, or `streams`. Endpoints may reference matched structure node ids/refs or text. Resolved edges are stored on source elements as `external_structure_successor_ids` and `external_structure_precedence_target_ids`.
- `reading_streams` / `streams` are also applied as stream metadata, not only relation sources. Resolved members receive `external_structure_stream_*` plus `reading_order_stream_id`, `reading_order_stream_type`, and `reading_order_stream_index`, so OCR/image pages can expose translation-local body/sidebar/table/grid/card streams even when the structure JSON has no region bboxes.
- When resolved successor/precedence edges form a safe acyclic path-cover order, the text reading order is reassigned with `reading_order_strategy = external-structure-relation-fusion-v1`. This lets OCR/structure JSON drive the semantic layer for image sources and complex pages without requiring one brittle global block permutation.
- When no relation order is available but at least two external block orders are matched on a page, the text reading order can still be reassigned with `reading_order_strategy = external-structure-fusion-v1`. Benchmark reports expose `structure_evidence_order_source_counts`, so A/B runs can distinguish explicit model order, Docling body-tree order, implicit list order, and unordered detector regions.
- Reordered elements also append `external-structure-order` to `reading_order_evidence` and preserve the model confidence under `reading_order_confidence` when it is stronger than the native heuristic confidence.
- External labels also feed reading-order scope and stream metadata: header/footer/page-number labels become page artifacts, footnotes and sidebars become secondary local streams, caption labels become caption streams, table labels become table-island streams, and explicit card/grid/product/tile labels become `grid-island` streams for translation/editing. Plain `list` labels remain list-role evidence instead of being promoted to grid streams, so ranked/news pages do not get false card-grid structure.
- The annotation pass maps external labels into roles, so labels such as `formula`, `header`, `footer`, `table_caption`, and `table` can affect the structured HTML metadata.

This gives the project an A/B path:

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark input.pdf --font-profile local-urw --out-dir outputs/benchmark-local-urw
scriptorium benchmark page.png --input-kind image --structure-json page.structure.json --out-dir outputs/page-image
```

The benchmark command accepts one or more `--structure-json` files, matched by argument order or by names such as `<source-stem>.structure.json` and `<parent-dir>.<source-stem>.structure.json`. The next quality step is to run real PaddleOCR-VL 1.6, PP-StructureV3, Docling, or relation-order payloads and compare `native` versus `native-plus-structure` with the same benchmark reports. For images and scanned PDFs, the model evidence can become the primary text source; for digital papers, it should first be used as role/order/table/formula evidence while preserving native text and style.

## Reading Order Layer

PDF text is positioned drawing evidence, not guaranteed semantic text order. The current implementation keeps visual element IDs stable, then writes semantic ordering metadata:

- `visual_order`: top-left visual order from bbox sorting.
- `semantic_order`: reading order used by XML/DOM/export consumers.
- `recursive-xy-cut-v1`: a hierarchical backend that recursively cuts whitespace into top/bottom and left/right regions, then records the region path for downstream HTML/editing inspection.
- `column-flow-v1`: a lightweight multi-column fallback that detects repeated two- or three-column text flows, keeps tables row-major, and orders each flow segment by column then vertical position.
- `spatial-graph-v1`: a conservative weak-column fallback that builds vertical predecessor/successor chains from horizontal overlap and center proximity when repeated left-edge anchors are too unstable.
- `box-flow-v1`: a guarded weak-column fallback that uses a pdfminer-style column-biased candidate only after table, repeated-anchor column flow, and spatial graph decline the page.
- `successor-consensus-arbitration-v1`: a narrow runtime arbitration path for sparse weak-column pages that would otherwise remain `single-column-visual-order`. It only accepts when non-visual candidates agree strongly, visual-yx disagrees on adjacent successor edges, and the consensus path contains clear column handoff(s). Multiple handoffs are preserved as `column_count` / `column_index` metadata for sparse three- or four-column pages.
- `infer_box_flow_order()`: a reusable pdfminer-style candidate sorter with a continuous `boxes_flow` control. Benchmark uses the same primitive for pairwise disagreement diagnostics even when `box-flow-v1` is not selected.
- `infer_relation_graph_order()`: a geometry-only successor-graph candidate sorter. It builds local successor edges, selects a degree-constrained path cover with a max-regret rule, and serializes the chains for benchmark diagnostics without replacing the selected semantic order.
- `structure_relation`: a benchmark-only semantic candidate that combines `reading_order_scope` metadata, page artifacts, footnotes, sidebars, caption-target proximity, and relation-graph body ordering into one structure-aware candidate order. It is deliberately diagnostic-only until sidecars or external model evidence prove when it should override the selected order.
- `infer_successor_consensus_order()`: a candidate-arbitration primitive. It takes adjacent successor edges from visual-yx, box-flow, relation-graph, structure-relation, and optional external-structure candidates, votes on those edges under acyclic one-predecessor/one-successor constraints, then serializes a path-cover candidate for benchmark scoring.
- `successor_consensus_diagnostics()`: exposes the same candidate plus support metrics: candidate count, candidate/unique edge counts, selected-edge support ratio, selected-edge coverage ratio, conflicted-edge ratio, and `high` / `medium` / `low` / `unavailable` agreement level.
- `reading_order_caption_type`: shallow caption evidence inferred from native/OCR text labels such as `Figure 1`, `Fig. 2`, `Table 3`, or `Algorithm 1`. Column-local captions stay in their column; captions that cross the column gutter become local flow breaks and carry `caption-label`, `figure-caption`/`table-caption`, and `cross-column-caption` evidence.
- `reading_order_caption_target_*`: local figure/table caption association evidence. The annotation layer links eligible figure/table captions to nearby native image elements, local raster regions, or inferred figure/table layout regions by type-compatible bbox proximity and horizontal alignment. It deliberately excludes near-full-page image backgrounds so scanned/screenshot OCR pages do not treat the page image as a figure target.
- `mixed-table-column-flow-v1`: a local table-island backend for mixed pages. It detects consecutive rows with repeated short-cell column slots, preserves those islands as row-major subregions, and infers surrounding prose columns from non-table text so table cells do not distort body-column detection.
- `mixed-grid-column-flow-v1`: a local grid-island backend for portal, ecommerce, and dense card-grid pages. It detects repeated non-table x slots across adjacent compact rows, preserves each grid island as row-major, marks `root/grid-island-###` / `grid-island-row-major` / `local-structure-grid`, and exposes a `grid-island` reading stream so translation can process cards separately from body prose.
- `table-row-major-v1`: a pure table-grid backend for table-dominated pages. It preserves row-major order with explicit table evidence instead of reporting an unqualified `visual-yx` fallback.
- `reading_order_confidence`: a bounded, conservative heuristic confidence for the assigned ordering path. It is not a semantic accuracy score; it summarizes evidence strength so later editors/translators can route low-confidence nodes to review or model assistance.
- `reading_order_evidence`: a machine-readable evidence list such as `single-column-visual-order`, `recursive-xy-cut`, `horizontal-whitespace-cut`, `column-flow`, `repeated-left-edge`, `spatial-graph`, `horizontal-overlap-chain`, `multi-head-flow`, `table-row-major`, `table-island-row-major`, `grid-island-row-major`, `local-structure-grid`, `page-edge-artifact`, `footnote-secondary-flow`, `bottom-note-zone`, `sidebar-secondary-flow`, or `external-structure-order`.
- Caption detection is deliberately lexical and conservative: it requires a leading figure/table/algorithm label plus a plausible text-line geometry, then optionally absorbs up to two tightly adjacent continuation lines with strong horizontal overlap. Mixed table-flow only runs caption detection on non-table-island text so table cells are not re-labeled as captions.
- Caption target matching is deliberately non-mutating: it adds `caption-target-proximity`, `<kind>-target`, and position evidence to the caption node, but it does not change semantic order yet. This keeps target proximity available for benchmark triage and later candidate arbitration without overfitting current external samples.
- Repeated-left-edge detection catches real academic columns whose long text boxes have overlapping center-x clusters. It now evaluates up to three repeated anchor columns, requires enough anchors per column and at least 45-48% coverage of candidate body lines, so sparse author grids do not become false column pages while formula/noise boxes between columns do not force fallback.
- Marginal page artifacts are detected with a conservative geometry gate at the top/bottom page edge. They remain editable/visible elements, but get `reading_order_scope = page-artifact`, `reading_order_artifact_type = header|footer`, `artifact-header` / `artifact-footer` column spans, and annotation roles such as `running-header` or `footer`.
- Sidebar and marginalia detection estimates the main print space from wider body lines, then identifies narrow grouped text outside that space at the left or right edge. These nodes remain editable, receive `reading_order_scope = sidebar`, `reading_order_sidebar_type = left|right`, `sidebar-left` / `sidebar-right` spans, and are ordered after the primary body flow instead of becoming an extra body column.
- Footnote detection identifies compact bottom-zone note clusters below the primary body flow. These nodes remain editable, receive `reading_order_scope = footnote`, `footnote` spans, `footnote-secondary-flow` / `bottom-note-zone` evidence, and are ordered after body columns but before sidebars and footer artifacts.
- Visual row ordering uses a small row bucket to absorb tiny PDF extraction offsets while keeping dense list rows separate, which matters for web-to-PDF ranked lists.
- `auto`: uses recursive XY-Cut only when the page has both horizontal and vertical structure; otherwise it falls back to `column-flow-v1` or visual order.
- `column_index` and `column_count`: column assignment for downstream translation/editing surfaces.

The table guard intentionally preserves obvious three-or-more-column grids as row-major order, preventing spreadsheet-like rows from being read down columns. Mixed pages are handled conservatively: repeated anchors are computed before the table-grid guard, and a grid-looking page may still use `column-flow-v1` only when at least two anchored columns cover 60% or more of candidate text and each anchor looks like a text-flow column rather than short table cells. Local table islands are detected only when there are at least three consecutive aligned rows, repeated x slots, a majority of short cells, and no duplicate repeated slot within a row; this last guard prevents formula/math fragments from being treated as table cells. Pages where table-like rows dominate the text now use `table-row-major-v1`, which keeps the same semantic order while making the intent auditable through `table-row-major` evidence. This lets pages with a table/formula area plus normal two/three-column prose keep human reading order without breaking pure tables.

Non-table grid islands are separate from table detection. They require repeated compact x slots across at least two adjacent rows and reject broad text-flow slots, which makes them useful for complex portal/card layouts without forcing card groups to masquerade as tables. The visual layer can still rely on source backgrounds for pixel parity; the added value is that each card grid becomes an explicit local reading and translation stream.

`spatial-graph-v1` runs after table and repeated-anchor column checks, not before them. It filters out table-like grids, ignores tiny boxes, links nearby vertically ordered boxes that overlap horizontally or have close centers, and accepts the result only when at least two significant chains cover 65% or more of non-full-width text, have enough horizontal separation, and overlap vertically. This keeps it useful for irregular two-column OCR/PDF boxes without letting it replace stronger existing column, table, artifact, sidebar, or footnote paths.

`box-flow-v1` is deliberately guarded. It runs after the table guard, repeated-anchor column flow, and spatial graph fallback. It splits the page on full-width visual breaks, compares visual order with a column-biased candidate inside each segment, and accepts a segment only when candidate disagreement, balanced two-way x split, vertical overlap, and horizontal separation all pass. The selected order then becomes column-major within that weak segment, with `box-flow`, `candidate-order-disagreement`, and `column-biased-flow` evidence.

The box-flow diagnostic is separate from strategy selection. It compares the current semantic order against a column-biased continuous order and reports pairwise disagreement counts. Low disagreement on labeled two-column papers is evidence that the current structural path agrees with a generic horizontal-flow candidate; high disagreement on dense webpage/OCR pages identifies samples that need semantic labels or external structure evidence before changing the default order.

The relation-graph diagnostic is also separate from strategy selection. It compares the current semantic order against a geometry-only local successor graph and reports both pairwise and adjacent-successor disagreement. The successor metric is the more relevant signal for this candidate because the graph predicts immediate next-node relations before serialization. Current results show lower local successor disagreement than box-flow on the complex samples, but pairwise disagreement remains high enough that the graph must stay candidate-only until semantic sidecars or external model evidence can arbitrate when it should take over.

The successor-consensus diagnostic is the next arbitration layer. It does not create new geometry rules; instead, it asks whether independent candidates agree on local successor edges. This follows relation/path-cover reading-order work: shared immediate edges are stronger evidence than broad global rank agreement, while disagreement highlights pages that need semantic sidecars, model structure evidence, or manual review. The support/conflict metrics are intentionally separate from correctness: a high-support consensus can still be wrong, but a low-support or high-conflict page should not be automatically reordered without stronger evidence.

The runtime successor-consensus arbitration path is deliberately stricter than the benchmark diagnostic. It excludes visual-yx from the internal vote and requires high agreement between box-flow and relation-graph candidates, a visual-vs-consensus successor disagreement, and large upward x-shift handoff(s) consistent with moving from the bottom of one column to the top of the next. This targets sparse multi-column pages that do not meet the minimum evidence thresholds for repeated-anchor column flow, spatial graph, or selected box-flow fallback.

Benchmark page diagnostics now expose the same idea one level closer to runtime arbitration. For every page with at least two editable text nodes, `reading_order_candidate_page_diagnostics` compares the selected semantic order against the successor-consensus order, records candidate names, support/coverage/conflict metrics, pairwise and successor-edge disagreements, and assigns a review recommendation. This works even when no semantic sidecar exists. The recommendation is a triage label, not an automatic order switch.

Semantic sidecar scoring now includes the `structure_relation` candidate next to visual-yx, box-flow, relation-graph, successor-consensus, and external-structure. This lets benchmark reports show whether page-scope and caption-target structure improve local successor edges without promoting a single unlabeled sample into runtime behavior.

The sidebar and footnote rules follow the same principle as page artifacts: secondary material should stay addressable but should not distort the primary narrative flow. They are deliberately geometry-only and conservative, so regular three-column papers still keep three body columns while annual-report marginal notes and bottom-zone note clusters can be routed as secondary content.

The current heuristic is intentionally modular in `src/scriptorium/reading_order.py`. It can be replaced or augmented by:

- A pdfminer.six-style box-flow scorer for pages that need a continuous horizontal-vs-vertical ordering tradeoff.
- Optional model/layout backends such as Docling, LayoutParser, PaddleOCR-VL, or PP-Structure outputs when available.

Research references used for this pass:

- PyMuPDF documents that PDF text may not appear in natural reading order and exposes sorting helpers: https://pymupdf.readthedocs.io/en/latest/recipes-text.html
- W3C PDF14 describes running headers and footers as pagination artifacts: https://www.w3.org/WAI/WCAG22/Techniques/pdf/PDF14
- W3C PDF4 lists page headers/footers among artifact examples: https://www.w3.org/TR/WCAG20-TECHS/PDF4.html
- W3C PDF3 notes that complex PDF layouts with graphics, tables, footnotes, and side-bars often need explicit reading-order repair: https://www.w3.org/WAI/WCAG22/Techniques/pdf/PDF3
- OCR-D PAGE reading-order guidelines treat marginalia outside the print space as regions to be ordered after primary text/footnote regions: https://ocr-d.de/en/gt-guidelines/trans/lyLeserichtung.html
- EPUB accessibility guidance uses `aside` semantics for secondary material so it does not interrupt the primary logical reading order: https://idpf.github.io/a11y-guidelines/content/semantics/order.html
- pdfminer.six exposes `LAParams.boxes_flow` for horizontal-vs-vertical text box ordering: https://pdfminersix.readthedocs.io/en/latest/reference/composable.html
- LayoutReader / ReadingBank treats reading order as a first-class document understanding task and provides a large weakly supervised benchmark: https://aclanthology.org/2021.emnlp-main.389/
- Docling's rule-based reading-order implementation uses above/below adjacency and horizontal overlap style geometry, which informed the conservative spatial-graph fallback: https://github.com/docling-project/docling-ibm-models/blob/73cf24d321f74f77de5f974e6c048da0e1512a3d/docling_ibm_models/reading_order/reading_order_rb.py
- DoclingDocument stores document items under `texts`, `tables`, `pictures`, `key_value_items`, and groups, with logical order represented by the `body` tree: https://docling-project.github.io/docling/concepts/docling_document/
- Relation-based reading-order research frames page ordering as pairwise layout relations rather than only global y/x sorting: https://arxiv.org/html/2409.19672v1
- Reading order inference for complex layouts can be formulated as graph/path-cover ordering over local relations: https://arxiv.org/html/2607.01018
- GraphDoc is a reference for graph-based document structure analysis with reading-order and logical relations: https://proceedings.iclr.cc/paper_files/paper/2025/file/cf3d7d8e79703fe947deffb587a83639-Paper-Conference.pdf
- PRImA reading-order work is useful context for representation and evaluation on complex layouts: https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf
- Sparse graph segmentation work is another reference point for graph-based reading-order recovery: https://arxiv.org/pdf/2305.02577
- Reading-order evaluation can use pairwise ordering measures such as Kendall tau: https://aclanthology.org/J06-4002.pdf
- ReadingBank is a reading-order benchmark built for document images: https://aclanthology.org/2021.emnlp-main.389/
- XY-Cut / XY-Cut++ is a common document reading-order recovery family: https://arxiv.org/html/2504.10258v1
- Docling targets detailed PDF layout and reading-order reconstruction: https://arxiv.org/html/2408.09869v5
- LayoutParser provides model-oriented document layout structures and tooling: https://arxiv.org/abs/2103.15348
- PP-StructureV3 documents multi-column reading-order recovery and outputs layout blocks with coordinates/order/content: https://www.paddleocr.ai/latest/en/version3.x/pipeline_usage/PP-StructureV3.html
- PaddleOCR-VL 1.6 documents `PaddleOCRVL(pipeline_version="v1.6")`, prediction, and JSON saving: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6

## Semantic-Order Benchmark

The built-in benchmark fixtures now write a sidecar file next to each generated PDF:

```text
example.semantic-order.json
```

The sidecar stores a per-page `text_sequence` ground truth. During `scriptorium benchmark`, `semantic_quality.py` first looks next to the source document and then under `benchmarks/semantic-ground-truth/` for matching repo sidecars. Repo-level lookup supports both `<source-stem>.semantic-order.json` and `<parent-dir>.<source-stem>.semantic-order.json`, so generic files like `web-hn/input.pdf` can have stable tracked labels without colliding with other `input.pdf` samples. It compares the extracted semantic order against that sequence and writes `semantic/semantic_quality_report.json` per case.

Sidecars can also store relation-style ground truth, which is better for complex pages where several global sequences are acceptable:

```json
{
  "version": 2,
  "pages": [
    {
      "page_index": 0,
      "match_mode": "ordered-subsequence",
      "text_sequence": ["Article title", "First body line"],
      "successor_edges": [["Article title", "First body line"]],
      "precedence_edges": [
        {"source": "Sidebar heading", "target": "Sidebar detail"},
        {"from": "Figure 1.", "to": "The caption continues."}
      ]
    }
  ]
}
```

`successor_edges` score immediate adjacency among labelled nodes, ignoring unlabelled actual text between them. `precedence_edges` only require the source label to appear before the target label. If a page has relation edges but no `text_sequence`, `match_mode` defaults to `ordered-subsequence` so the page is not penalized for unlabelled text. Candidate orders receive the same relation metrics as the selected order.

Supported page match modes:

- `full-sequence`: the default mode for generated fixtures; expected and actual page text should match exactly except for reported missing/extra nodes.
- `ordered-subsequence`: intended for real PDFs with partial human labels; only the listed text nodes are scored, unlisted actual text is counted as ignored, and pairwise order is still evaluated across the labeled nodes.

Metrics:

- `semantic_order_pair_accuracy`: pairwise order correctness across expected text nodes; this is Kendall-tau-like and catches left/right column swaps.
- `semantic_successor_accuracy`: adjacent successor-edge correctness across expected text nodes. In `ordered-subsequence` mode, unlabelled actual text between two labelled nodes is ignored, but a labelled node inserted between them, a reversed adjacent pair, or a missing adjacent node breaks the edge. Reports also expose `semantic_successor_correct_count` and `semantic_successor_total_count`.
- `semantic_relation_successor_accuracy`: correctness for explicit `successor_edges`. This is the relation-style metric to watch when evaluating relation-graph, structure-relation, and successor-consensus candidates on complex layouts.
- `semantic_relation_precedence_accuracy`: correctness for explicit `precedence_edges`, useful when a page has several valid global reading orders but still has local before/after constraints.
- `semantic_relation_missing_text_count`: unique relation labels that were not found in the extracted page text.
- `semantic_sequence_similarity`: normalized Levenshtein similarity between expected and actual text sequences.
- `semantic_exact_page_match_rate`: page-level exact sequence match rate.
- `ignored_text_count`: unlabelled actual text ignored by `ordered-subsequence` pages.
- `ignored_text_zone_counts`, `ignored_text_role_counts`, and `ignored_text_source_counts`: where ignored text lives and what the annotation layer thinks it is, useful for deciding which unlabeled regions should become future ground truth.
- `semantic_missing_text_count`: expected text nodes not found in extraction.
- `semantic_extra_text_count`: extracted text nodes not present in the ground truth.

For external PDFs without a sidecar in either location, semantic metrics are reported as unavailable while visual metrics still run normally. The tracked arXiv Attention sidecar currently covers 5 representative pages and 38 labeled text nodes. The tracked Transformer-XL sidecar covers 3 real ACL two-column pages and 44 labeled text nodes. The tracked Hacker News web-to-PDF sidecar covers 2 pages and 26 dense-list/footer labels.

`compare_semantic_reading_order()` also accepts optional `candidate_orders`, keyed by candidate name, page index, and ordered element ids. Benchmark uses this to score `visual_yx`, `box_flow`, `relation_graph`, `structure_relation`, `successor_consensus`, and, when external structure relations or block orders are matched, `external_structure` candidates against the same sidecar. This creates an arbitration-ready evidence layer: disagreement diagnostics still show how far candidates are from the selected order, while sidecar-scored candidate metrics show whether a candidate is actually closer to labelled human order.

## Useful References

- PaddleOCR GitHub: https://github.com/PaddlePaddle/PaddleOCR
- PaddleOCR-VL-1.6 model usage: https://huggingface.co/PaddlePaddle/PaddleOCR-VL-1.6
- PyMuPDF image rendering: https://pymupdf.readthedocs.io/en/latest/recipes-images.html
- Playwright PDF output: https://playwright.dev/python/docs/api/class-page#page-pdf
- MuPDF/SVG plus transparent text-layer PDF-to-HTML pattern: https://github.com/OskarLebuda/rs-pdf
- BuildVu SVG/HTML5 hybrid layout preservation discussion: https://blog.idrsolutions.com/convert-pdf-to-html5-preserving-layout/
- OmniDocBench render-and-compare OCR/HTML reconstruction dataset pattern: https://huggingface.co/datasets/gt-free-ocr-metrics/omnidocbench-render-compare

## Benchmark Metrics

The benchmark command is the baseline for future optimization:

```bash
scriptorium benchmark --out-dir outputs/benchmark-baseline --dpi 192
```

Large external documents can be scored with a stable front-matter sample:

```bash
scriptorium benchmark data/external/puma-2024-annual-report.pdf \
  --max-pages 12 \
  --html-mode auto \
  --fidelity-background auto \
  --out-dir outputs/external/puma-2024-annual-report-benchmark \
  --dpi 144
```

Translated re-rendering can be stress-tested without a live translation service:

```bash
scriptorium benchmark input.pdf \
  --html-mode fidelity \
  --fidelity-background auto \
  --translation-stress pseudo-expand \
  --out-dir outputs/translation-stress \
  --dpi 144
```

This writes deterministic pseudo-expanded text into `translated_text`, then scores visual similarity plus replacement overflow/conflict/fit-scale metrics. It is useful for JD/PUMA/portal samples where the source page may look perfect with a background layer but translated replacements can still collide locally. The current JD/PUMA/web-HN stress rerun scores 15 pages with no page-count or dimension mismatches; mean visual similarity is `0.81899535`, but 565/567 replacements still report neighbor conflicts, so conflict reduction remains an active quality target.

External structure evidence can be evaluated with a paired A/B run:

```bash
scriptorium benchmark-structure-ab input.pdf \
  --structure-json input.structure.json \
  --out-dir outputs/structure-ab \
  --dpi 144
```

This writes `native-only/benchmark_report.json`, `native-plus-structure/benchmark_report.json`, `structure_ab_report.json`, and `structure_ab_summary.csv`. The A/B report records deltas for visual similarity, reading-order risk, `grid_island_element_count`, structure-evidence region/match/reorder counts, page/stream `needs-structure-evidence` recommendations, review recommendations, successor-disagreement counts, and semantic successor metrics when sidecars exist.

Image sources use the same benchmark command:

```bash
scriptorium benchmark page.png \
  --input-kind image \
  --image-dpi 96 \
  --structure-json page.structure.json \
  --html-mode structured \
  --out-dir outputs/page-image-benchmark
```

The visual comparison renders the exported PDF at `image_dpi` so the source image layer and printed output are compared at the same pixel dimensions. Structure JSON can seed the initial `native-ocr` anchor layer before structure evidence is fused back onto those anchors.

Metrics:

- `source`: source-neutral input path for each case. `source_pdf` remains in reports/CSV as a compatibility column.
- `source_type_counts`: count of `pdf` and `image` cases in a benchmark run.
- `input_kind`: requested source detection mode, `auto`, `pdf`, or `image`.
- `image_dpi`: pixel density used to map image source pixels into PDF-point coordinates and to render the exported PDF for image-source visual scoring.
- `semantic_layer_driver`, `semantic_layer_payload_kind`, and `semantic_layer_structure_role`: case-level diagnostics that show whether the semantic layer came from native PDF extraction, structure JSON, OCR JSON, OCR fallback, or only the visual layer.
- `max_pages`: optional first-N-pages benchmark limit. The source document remains intact; render, extraction, print, visual comparison, and semantic sidecar matching operate on the sampled pages only.
- `page_ranges`: optional explicit 1-based source page sampling such as `1-12,136-160,220`. It is mutually exclusive with `max_pages`. Rendered pages keep their original source `page_index`, so semantic sidecars and Paddle/PP-Structure/Docling structure JSON can still align by source page number instead of the sampled list position.
- `sampled_page_numbers`: the exact 1-based source page numbers scored by the run when `page_ranges` is used.
- `max_diff_ratio`: maximum normalized page difference between original PDF render and structured HTML-to-PDF render. Missing/extra pages are scored as `1.0`; page dimension mismatches add a size penalty instead of silently resizing away the mismatch.
- `mean_diff_ratio`: average page difference across all matched and unmatched pages.
- `p95_diff_ratio`: 95th percentile diff ratio for the compared page set.
- `worst_page`: 1-based page number with the largest effective diff ratio.
- `visual_similarity`: `1 - max_diff_ratio`; higher is better.
- `translation_stress`, `translation_stress_element_count`, `translation_stress_source_char_count`, `translation_stress_translated_char_count`, and `translation_stress_char_expansion_ratio`: deterministic pseudo-translation input used to stress translated re-rendering. `pseudo-expand` intentionally lengthens source text; it does not measure translation quality.
- `fidelity_replacement_element_count`, `fidelity_replacement_overflow_count`, `fidelity_replacement_conflict_count`, `fidelity_replacement_conflict_target_count`, `fidelity_replacement_min_fit_scale`, `fidelity_replacement_mean_fit_scale`, and `fidelity_replacement_policy_counts`: replacement-risk diagnostics for edited/translated nodes in fidelity mode. These fields stay zero/`null` for source-only runs and become the primary non-visual quality signal for translated HTML-to-PDF round trips.
- `fidelity_replacement_stream_diagnostics`, `fidelity_replacement_stream_type_counts`, `fidelity_replacement_stream_type_overflow_counts`, `fidelity_replacement_stream_type_conflict_counts`, `fidelity_replacement_stream_id_counts`, `fidelity_replacement_stream_id_overflow_counts`, and `fidelity_replacement_stream_id_conflict_counts`: stream-local replacement diagnostics. They split the same replacement risk by `reading_order_stream_id` / `reading_order_stream_type`, so JD/PUMA/portal runs can show whether translated collisions live in the body flow, card grids, sidebars, footnotes, table islands, or page artifacts.
- `page_count_match`: whether expected and actual PDFs have the same page count.
- `dimension_match`: whether every reported page has matching render dimensions.
- `total_seconds`: wall-clock time for render, extraction, annotation, HTML export, PDF print, and comparison.
- `timings`: per-stage timing split.
- `element_count`: total generated IR elements.
- `editable_element_count`: elements that map to editable text.
- `image_count`: native image and local raster fallback elements.
- `shape_count`: structural drawing nodes.
- `style_count`: inferred style buckets.
- `annotation_count`: elements with annotation metadata.
- `text_run_count`: source span/run count preserved for structured rendering.
- `mixed_inline_style_element_count`: text elements containing multiple rendered run styles.
- `multi_column_element_count`: editable text nodes assigned to a multi-column flow.
- `column_flow_element_count`: editable text nodes ordered by `column-flow-v1`.
- `mixed_table_column_flow_element_count`: editable text nodes ordered by `mixed-table-column-flow-v1`.
- `table_row_major_element_count`: editable text nodes ordered by `table-row-major-v1`.
- `spatial_graph_element_count`: editable text nodes ordered by `spatial-graph-v1`.
- `box_flow_element_count`: editable text nodes ordered by `box-flow-v1`.
- `recursive_xy_cut_element_count`: editable text nodes ordered by `recursive-xy-cut-v1`.
- `reading_order_artifact_element_count`: editable text nodes identified as page-level artifacts such as running headers or footers.
- `reading_order_caption_element_count`: editable text nodes with `reading_order_caption_type`, plus per-type `reading_order_caption_counts`.
- `reading_order_caption_targeted_element_count`, `reading_order_caption_orphan_element_count`, `reading_order_caption_target_coverage_ratio`, and `reading_order_caption_target_counts`: how many captions are linked to nearby figure/table/image targets and which target kinds they use.
- `reading_order_artifact_counts`: per-artifact-type counts in the JSON summary and case reports.
- `reading_order_footnote_element_count`: editable text nodes identified as bottom-zone footnotes ordered after the body flow.
- `reading_order_sidebar_element_count`: editable text nodes identified as secondary sidebar or marginalia content.
- `reading_order_sidebar_counts`: per-side sidebar counts in the JSON summary and case reports.
- `reading_order_strategy_counts`: per-strategy count of editable text nodes in the JSON report summary and per case.
- `reading_order_confidence_element_count`: editable text nodes carrying reading-order confidence metadata.
- `reading_order_mean_confidence`: average per-element reading-order confidence for a case or weighted benchmark summary.
- `reading_order_low_confidence_element_count`: editable text nodes below the current confidence review threshold.
- `reading_order_evidence_counts`: per-evidence count in the JSON summary and per case, useful for seeing whether a sample is driven by visual order, XY-Cut, column-flow, spatial graph, pure table row-major, table-islands, grid-islands, page artifacts, footnotes, sidebars, or external model order.
- `grid_island_element_count`: editable text nodes routed through non-table `grid-island` reading streams.
- `successor_consensus_arbitration_element_count`: editable text nodes ordered by the conservative runtime successor-consensus arbitration path.
- `reading_order_box_flow_pair_count`: text-element pairs compared against the column-biased box-flow candidate.
- `reading_order_box_flow_disagreement_pair_count`: compared pairs whose order differs from the current semantic order.
- `reading_order_box_flow_disagreement_ratio`: disagreement pairs divided by compared pairs; diagnostic only, not a correctness score.
- `reading_order_box_flow_disagreement_page_count`: pages with at least one box-flow disagreement.
- `reading_order_box_flow_successor_edge_count`: adjacent reference successor edges compared against the column-biased box-flow candidate.
- `reading_order_box_flow_successor_disagreement_count`: adjacent successor edges that are not preserved by the candidate.
- `reading_order_box_flow_successor_disagreement_ratio`: successor disagreement divided by compared successor edges; diagnostic only, not a correctness score.
- `reading_order_box_flow_successor_disagreement_page_count`: pages with at least one box-flow successor-edge disagreement.
- `reading_order_relation_graph_pair_count`: text-element pairs compared against the geometry-only relation-graph candidate.
- `reading_order_relation_graph_disagreement_pair_count`: compared pairs whose order differs from the current semantic order.
- `reading_order_relation_graph_disagreement_ratio`: relation-graph pairwise disagreement divided by compared pairs; diagnostic only, not a correctness score.
- `reading_order_relation_graph_disagreement_page_count`: pages with at least one relation-graph pairwise disagreement.
- `reading_order_relation_graph_successor_edge_count`: adjacent reference successor edges compared against the relation-graph candidate.
- `reading_order_relation_graph_successor_disagreement_count`: adjacent successor edges that are not preserved by the relation-graph candidate.
- `reading_order_relation_graph_successor_disagreement_ratio`: relation-graph successor disagreement divided by compared successor edges; diagnostic only, not a correctness score.
- `reading_order_relation_graph_successor_disagreement_page_count`: pages with at least one relation-graph successor-edge disagreement.
- `reading_order_successor_consensus_pair_count`: text-element pairs compared against the successor-consensus candidate.
- `reading_order_successor_consensus_disagreement_pair_count`: compared pairs whose order differs from the current semantic order.
- `reading_order_successor_consensus_disagreement_ratio`: successor-consensus pairwise disagreement divided by compared pairs; diagnostic only, not a correctness score.
- `reading_order_successor_consensus_disagreement_page_count`: pages with at least one successor-consensus pairwise disagreement.
- `reading_order_successor_consensus_successor_edge_count`: adjacent reference successor edges compared against the successor-consensus candidate.
- `reading_order_successor_consensus_successor_disagreement_count`: adjacent successor edges that are not preserved by the successor-consensus candidate.
- `reading_order_successor_consensus_successor_disagreement_ratio`: successor-consensus successor disagreement divided by compared successor edges; diagnostic only, not a correctness score.
- `reading_order_successor_consensus_successor_disagreement_page_count`: pages with at least one successor-consensus successor-edge disagreement.
- `reading_order_successor_consensus_candidate_page_count`: pages with at least two editable text nodes where successor-consensus could be evaluated.
- `reading_order_successor_consensus_mean_candidate_count`: average number of source candidates available per evaluated page.
- `reading_order_successor_consensus_candidate_edge_count`: total adjacent edges contributed by all source candidates.
- `reading_order_successor_consensus_unique_edge_count`: distinct adjacent edges proposed by the source candidates.
- `reading_order_successor_consensus_selected_edge_count`: edges selected into the consensus path cover.
- `reading_order_successor_consensus_selected_edge_vote_count`: total votes behind selected consensus edges.
- `reading_order_successor_consensus_selected_edge_support_ratio`: selected-edge votes divided by selected-edge capacity across available source candidates.
- `reading_order_successor_consensus_selected_edge_coverage_ratio`: selected consensus edges divided by the maximum page-local successor edges.
- `reading_order_successor_consensus_conflicted_edge_count`: candidate edges whose source or target participates in more than one proposed successor/predecessor relation.
- `reading_order_successor_consensus_conflicted_edge_ratio`: conflicted candidate edges divided by unique candidate edges.
- `reading_order_successor_consensus_high_agreement_page_count`, `reading_order_successor_consensus_medium_agreement_page_count`, `reading_order_successor_consensus_low_agreement_page_count`, and `reading_order_successor_consensus_unavailable_page_count`: page-level agreement buckets for runtime-arbitration triage.
- `reading_order_candidate_page_diagnostics`: per-case JSON-only page diagnostics for selected-vs-successor-consensus arbitration. Each entry includes page index, text element count, candidate names/count, consensus agreement level, selected-edge support/coverage/conflict ratios, pairwise disagreement, successor-edge disagreement, recommendation, and reason.
- `reading_order_candidate_page_recommendation_counts`: case, summary, and CSV counts for page triage recommendations. Values currently include `keep-selected-supported`, `keep-selected-low-consensus`, `review-consensus`, `review-disagreement`, `needs-structure-evidence`, and `unavailable`.
- `reading_order_candidate_stream_diagnostics`: per-case JSON-only stream diagnostics for selected-vs-successor-consensus arbitration. Entries are scoped by `stream_id` and `stream_type`, so body/sidebar/footnote stream disagreements are isolated from one another.
- `reading_order_candidate_stream_count`: number of stream-local candidate diagnostics emitted for the case.
- `reading_order_candidate_stream_recommendation_counts`: case, summary, and CSV counts for stream triage recommendations.
- `reading_order_risk_score`: benchmark diagnostic for pages that likely need stronger order evidence. It combines column-like geometry still using mostly visual order, missing/extra semantic text, partial-label ignored text, and absent ground truth.
- `reading_order_risk_level`: `low`, `medium`, or `high` bucket for the risk score.
- `reading_order_column_geometry_page_count`: pages with repeated anchors that look like text-flow columns, not just short table cells.
- `reading_order_visual_yx_column_page_count`: column-like pages where more than 60% of text still uses `visual-yx`.
- `reading_order_repeated_anchor_page_count`: pages with repeated left-edge anchors and overlapping vertical extent, before table-vs-text-flow filtering.
- `reading_order_max_repeated_anchor_columns`: maximum repeated-anchor column count detected in a case.
- `reading_order_table_like_page_count`: pages whose text boxes look like a three-or-more-column grid.
- `reading_order_table_like_visual_yx_page_count`: table-like pages that still stay mostly unqualified `visual-yx`; intentional table protection should now appear as `table-row-major-v1` or `mixed-table-column-flow-v1`.
- `reading_order_unlabeled_text_risk_count`: ignored text count for partial sidecars, or all text when no semantic ground truth is available.
- `font_profile`: CSS font fallback profile used by native extraction, useful for comparing default browser fallback with local URW/DejaVu paper-font experiments. With benchmark `--font-profile auto`, each case also records `font_profile_candidates`, `font_profile_request`, and `font_profile_selected`.
- `font_size_scale`: CSS font-size multiplier used by native extraction. With benchmark `--font-size-scale auto`, each case also records `font_size_scale_candidates`, `font_size_scale_request`, and `font_size_scale_selected`.
- `text_fit`: structured text rendering strategy. With benchmark `--text-fit auto`, each case records `text_fit_candidates`, `text_fit_request`, and `text_fit_selected`.
- `html_mode`: benchmarked HTML rendering mode: `structured`, `fidelity`, or `auto`. `structured` redraws editable elements; `fidelity` prints page backgrounds while preserving transparent coordinate/edit anchors in the HTML; `auto` records `html_mode_candidates`, `html_mode_request`, and `html_mode_selected`.
- `fidelity_background`: background strategy for fidelity mode: `svg`, `raster`, or benchmark request `auto`. Auto cases record `fidelity_background_candidates`, `fidelity_background_request`, and `fidelity_background_selected`.
- `vector_background_page_count`: pages with optional SVG page backgrounds available for fidelity overlay experiments.
- `raster_policy`: native local-raster fallback policy.
- `ocr_fallback`: native OCR fallback policy, currently `image-only` or `off`.
- `ocr_fallback_applied_page_count`: pages where native text was absent, image coverage was high, and OCR produced text.
- `ocr_text_count`: editable elements whose source is `native-ocr`.
- `image_only_candidate_page_count`: pages that matched the no-native-text/high-image-coverage trigger, regardless of OCR success.
- `textless_page_count`: pages that still have no editable text after native extraction and OCR fallback.
- `layout_region_counts`: inferred table/figure/separator region counts.
- `raster_fallback_count`, `rasterized_text_count`, `rasterized_image_count`, and `rasterized_shape_count`: editability cost of local raster fallback regions.
- `structure_evidence_source`: optional JSON evidence source used by the case.
- `structure_evidence_region_count`: normalized external regions loaded from Paddle/PP-Structure/Docling structure JSON.
- `structure_evidence_relation_edge_count` and `structure_evidence_resolved_relation_edge_count`: external successor/precedence edges loaded from structure JSON and successfully resolved to current page elements.
- `structure_evidence_stream_count`, `structure_evidence_resolved_stream_member_count`, and `structure_evidence_stream_conflict_count`: external reading streams loaded from structure JSON, members resolved to current elements, and overlapping stream assignments that were skipped.
- `structure_evidence_matched_element_count`: native elements matched to those regions by bbox/text evidence.
- `structure_evidence_reordered_page_count`: pages whose text order was reassigned from external structure evidence.
- `structure_evidence_relation_reordered_page_count` and `structure_evidence_order_reordered_page_count`: pages driven by relation path-cover evidence versus block-order evidence.
- `semantic_order_pair_accuracy`: pairwise semantic order score when ground truth is available.
- `semantic_successor_accuracy`: labelled adjacent successor-edge score when ground truth is available.
- `semantic_successor_correct_count`, `semantic_successor_total_count`: raw successor-edge counts used for case and summary aggregation.
- `semantic_relation_successor_accuracy`, `semantic_relation_successor_correct_count`, and `semantic_relation_successor_total_count`: explicit relation-edge adjacency score from sidecar `successor_edges`.
- `semantic_relation_precedence_accuracy`, `semantic_relation_precedence_correct_count`, and `semantic_relation_precedence_total_count`: explicit before/after relation score from sidecar `precedence_edges`.
- `semantic_best_candidate_by_relation_successor`: candidate with the highest explicit relation successor accuracy when relation labels are available.
- `semantic_candidate_order_metrics`: sidecar-scored semantic metrics for benchmark candidate orders such as `visual_yx`, `box_flow`, `relation_graph`, `structure_relation`, `successor_consensus`, and `external_structure`.
- `semantic_best_candidate_by_successor`: candidate name with the highest labelled successor-edge accuracy, using pairwise accuracy as the tie-breaker.
- `semantic_best_candidate_successor_accuracy`: successor-edge accuracy of that best candidate.
- `semantic_candidate_arbitration_recommendation`: sidecar-scored benchmark recommendation, currently `keep-selected`, `consider-<candidate>`, or `unavailable`.
- `semantic_candidate_arbitration_candidate`: best scored candidate used for the recommendation.
- `semantic_candidate_successor_delta`: best candidate successor accuracy minus selected-order successor accuracy.
- `semantic_candidate_pairwise_delta`: best candidate pairwise accuracy minus selected-order pairwise accuracy.
- `semantic_candidate_relation_successor_delta`: best relation-edge candidate successor accuracy minus selected-order relation successor accuracy when `successor_edges` are available. Relation successor deltas can drive `consider-<candidate>` even when sequence metrics are tied.
- `semantic_visual_yx_order_pair_accuracy`, `semantic_visual_yx_successor_accuracy`, `semantic_box_flow_order_pair_accuracy`, `semantic_box_flow_successor_accuracy`, `semantic_relation_graph_order_pair_accuracy`, `semantic_relation_graph_successor_accuracy`, `semantic_structure_relation_order_pair_accuracy`, `semantic_structure_relation_successor_accuracy`, `semantic_successor_consensus_order_pair_accuracy`, `semantic_successor_consensus_successor_accuracy`, `semantic_external_structure_order_pair_accuracy`, and `semantic_external_structure_successor_accuracy`: flattened candidate metrics for CSV/report comparisons. Candidate relation metrics use the same prefix pattern with `_relation_successor_accuracy` and `_relation_precedence_accuracy`.
- `semantic_sequence_similarity`: normalized sequence similarity against the sidecar sequence.
- `semantic_ignored_text_count`: actual text nodes ignored by partial `ordered-subsequence` labels.
- `semantic_ignored_text_zone_counts`, `semantic_ignored_text_role_counts`, `semantic_ignored_text_source_counts`: ignored-text diagnostics aggregated across semantic cases.

Benchmark PDF export normalizes page boxes to source dimensions when the `DocumentIR` page sizes are available, so a browser print-unit mismatch is reported as visual content difference only if pixels still differ after the page dimensions match. It also removes only trailing blank browser artifact pages beyond the expected source page count; nonblank translated overflow pages remain visible and are scored.

Current baseline artifacts live under `outputs/benchmark-baseline/`, with external sample commands in `docs/external-benchmarks.md`. Future optimizations should report delta against `benchmark_report.json` and `benchmark_summary.csv`.

Latest successor-metric validation:

| Sample | Command Output | Visual Similarity | Pairwise Order | Successor Accuracy | Successor Edges | Notes |
|---|---|---:|---:|---:|---:|---|
| Built-in fixtures | `outputs/benchmark-successor-metrics-v1` | 0.9906702 | 1.0 | 1.0 | 47/47 | generated full-sequence sidecars across 5 cases |
| arXiv Attention paper | `outputs/external/attention-successor-metrics-v1` | 0.96840246 | 1.0 | 1.0 | 33/33 | partial sidecar with 38 labelled text nodes |
| Transformer-XL first 3 pages | `outputs/external/transformer-xl-successor-metrics-v1` | 0.98160664 | 1.0 | 1.0 | 41/41 | partial real-paper sidecar with 44 labelled text nodes |
| Hacker News print PDF | `outputs/external/web-hn-successor-metrics-v1` | 0.9800288 | 1.0 | 1.0 | 24/24 | partial web-to-PDF sidecar with 26 labelled text nodes |

The successor metric complements pairwise order accuracy. Pairwise accuracy is stable for broad regressions such as swapped columns; successor accuracy is stricter about local continuity and is the metric to watch when relation-graph or model-evidence ordering starts predicting immediate next-node edges.

Latest box-flow fallback and successor-disagreement validation:

| Sample | Command Output | Visual Similarity | Semantic Order | RO Confidence | Box-Flow Elements | Pairwise Disagreement | Successor Disagreement | Spatial Graph | Table Row-Major | Footnotes | Sidebars | Key Evidence |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| Built-in fixtures | `outputs/benchmark-successor-disagreement-v1` | 0.9906702 | 1.0 | 0.80113208 | 0 | 0.19494585 | 19/47 | 0 | 18 | 0 | 0 | `table-row-major`, `recursive-xy-cut`, whitespace cuts |
| Transformer-XL first 3 pages | `outputs/external/transformer-xl-successor-disagreement-v1` | 0.98160664 | 1.0 | 0.9552648 | 0 | 0.0825672 | 142/318 | 0 | 0 | 7 | 0 | `column-flow`, `repeated-left-edge`, `footnote-secondary-flow` |
| PUMA 2024 Annual Report first 12 pages | `outputs/external/puma-2024-annual-report-successor-disagreement-v1` | 0.9795117 | n/a | 0.82476488 | 0 | 0.17460108 | 199/509 | 0 | 0 | 2 | 36 right | `sidebar-secondary-flow`, `footnote-secondary-flow`, `table-island-row-major` |
| JD homepage screenshot PDF | `outputs/external/jd-home-successor-disagreement-v1` | 0.99576887 | n/a | 0.83 | 0 | 0.42778588 | 127/133 | 0 | 0 | 0 | 0 | `recursive-xy-cut`, OCR anchors |

The external and built-in samples above currently report `spatial_graph_element_count = 0` and `box_flow_element_count = 0`. That is expected for this pass: existing stronger paths already cover those pages, and both weak-column backends are guarded so they do not inflate scores by taking over unrelated benchmark cases. The box-flow pairwise disagreement ratio remains a triage signal for broad candidate-order differences; successor disagreement is the relation-graph-oriented signal for local next-node edge differences. JD's 127/133 successor disagreement is therefore a stronger local-continuity warning than its already high pairwise ratio. The spatial trigger is covered by `tests/test_reading_order.py::test_spatial_graph_orders_overlapping_weak_columns`; the selected box-flow fallback is covered by `tests/test_reading_order.py::test_box_flow_fallback_orders_relaxed_irregular_columns`; the reusable candidate sorter is covered by `tests/test_reading_order.py::test_box_flow_candidate_exposes_horizontal_vs_vertical_ordering`; successor diagnostics are covered by `tests/test_reading_order.py::test_successor_disagreement_counts_adjacent_candidate_edges`.

Latest relation-graph diagnostics validation:

| Sample | Command Output | Visual Similarity | Semantic Order | Relation Pairwise Disagreement | Relation Successor Disagreement | Box-Flow Successor Disagreement | Notes |
|---|---|---:|---:|---:|---:|---:|---|
| Built-in fixtures | `outputs/benchmark-relation-graph-diagnostics-v1` | 0.9906702 | 1.0 | 6/277 | 3/47 | 19/47 | relation graph improves local successor continuity on generated fixtures |
| Transformer-XL first 3 pages | `outputs/external/transformer-xl-relation-graph-diagnostics-v1` | 0.98160664 | 1.0 | 3526/17077 | 111/318 | 142/318 | lower local disagreement than box-flow, but still candidate-only |
| PUMA 2024 Annual Report first 12 pages | `outputs/external/puma-2024-annual-report-relation-graph-diagnostics-v1` | 0.9795117 | n/a | 2473/15166 | 166/509 | 199/509 | useful annual-report candidate signal before sidecar/model arbitration |
| JD homepage screenshot PDF | `outputs/external/jd-home-relation-graph-diagnostics-v1` | 0.99576887 | n/a | 1927/8911 | 117/133 | 127/133 | dense OCR/web layout still needs semantic labels or external structure evidence |

These relation-graph numbers are not correctness scores. They show that a local successor graph is a promising additional candidate, especially against box-flow successor disagreement, but broad pairwise disagreement and missing complex-page semantic ground truth prevent using it as the default order yet. The implementation is covered by `tests/test_reading_order.py::test_relation_graph_candidate_orders_column_successor_paths`, `tests/test_reading_order.py::test_relation_graph_candidate_keeps_table_like_grid_visual`, and benchmark field assertions in `tests/test_benchmark.py`.

Latest semantic candidate scoring validation:

- `semantic_quality.py` now scores named candidate element-id orders against sidecars and reports `semantic_candidate_order_metrics`.
- Sidecars can include `successor_edges` and `precedence_edges`, so complex layouts can be evaluated as relation constraints instead of only as one serialized `text_sequence`.
- `scriptorium benchmark` automatically supplies `visual_yx`, `box_flow`, `relation_graph`, `structure_relation`, and `successor_consensus` candidates for each page.
- `external_structure` is supplied when Paddle/PP-Structure/Docling evidence has either resolved successor/precedence relations or at least two distinct `external_structure_order` values on a page. Relation edges are converted into a path-cover order first; explicit block order remains the fallback when no relation edges are present.
- Case reports and CSV include flattened candidate accuracies such as `semantic_relation_graph_successor_accuracy`, `semantic_structure_relation_successor_accuracy`, `semantic_successor_consensus_successor_accuracy`, and relation-edge variants such as `semantic_structure_relation_relation_successor_accuracy`.
- Summary reports aggregate candidate successor accuracy and `semantic_best_candidate_by_successor_counts`.
- Case and summary reports also include arbitration diagnostics, including candidate-vs-selected successor/pairwise deltas and recommendation counts.
- Case reports also include page-level candidate diagnostics that do not require sidecar labels, so external complex PDFs can be queued for review before ground truth exists.
- Unit coverage lives in `tests/test_semantic_quality.py::test_candidate_orders_are_scored_against_semantic_ground_truth`, `tests/test_reading_order.py::test_successor_consensus_diagnostics_report_support_and_conflict`, `tests/test_reading_order.py::test_successor_consensus_diagnostics_downgrades_cycle_conflict`, `tests/test_reading_order.py::test_successor_consensus_arbitration_orders_sparse_two_column_page`, `tests/test_reading_order.py::test_successor_consensus_arbitration_preserves_sparse_three_column_metadata`, `tests/test_benchmark.py::test_candidate_page_diagnostics_recommend_review_for_supported_disagreement`, and benchmark field assertions in `tests/test_benchmark.py`.

Built-in semantic candidate baseline:

| Command Output | Selected Successor | Visual-YX Candidate | Box-Flow Candidate | Relation-Graph Candidate | Best Candidate Counts |
|---|---:|---:|---:|---:|---|
| `outputs/benchmark-semantic-candidate-metrics-v1` | 47/47 | 34/47 | 28/47 | 44/47 | `relation_graph: 2`, `visual_yx: 3` |

This validates the metric and the candidate generation path. It does not promote relation graph to the selected order because selected semantic order already scores 47/47 on these fixtures, and complex external samples still need sidecars or model evidence for correctness scoring.

Built-in arbitration baseline:

| Command Output | Recommendation Counts | Candidate Counts | Mean Successor Delta | Mean Pairwise Delta |
|---|---|---|---:|---:|
| `outputs/benchmark-semantic-arbitration-v1` | `keep-selected: 5` | `relation_graph: 2`, `visual_yx: 3` | -0.06 | -0.02181818 |

This validates that the benchmark can keep a strong selected order even when another candidate is close. A future runtime selector should only switch when independent evidence predicts a positive delta before sidecar labels are known.

Latest caption-flow validation:

| Sample | Command Output | Visual Similarity | Semantic Order | Caption Elements | Caption Counts | Cross-Column Captions | Key Evidence |
|---|---|---:|---:|---:|---|---:|---|
| Built-in fixtures | `outputs/benchmark-caption-flow-v1` | 0.9906702 | 1.0 | 0 | `{}` | 0 | no caption labels in fixtures |
| Transformer-XL first 3 pages | `outputs/external/transformer-xl-caption-flow-v1` | 0.98160664 | 1.0 | 3 | `figure: 3` | 1 | `caption-label`, `figure-caption`, `cross-column-caption` |
| PUMA 2024 Annual Report first 12 pages | `outputs/external/puma-2024-annual-report-caption-flow-v1` | 0.9795117 | n/a | 0 | `{}` | 0 | no leading figure/table labels in sampled text |
| JD homepage screenshot PDF | `outputs/external/jd-home-caption-flow-v1` | 0.99576887 | n/a | 0 | `{}` | 0 | OCR anchors remain `recursive-xy-cut-v1` |

The caption-flow path is covered by `tests/test_reading_order.py::test_cross_column_caption_creates_local_flow_break` and by the native PDF image fixture, which verifies that `Figure 1:` becomes annotation role `caption`, links to the adjacent `native-image` as a figure target, and exports both `data-scriptorium-reading-order-caption="figure"` and `data-scriptorium-caption-target-kind="figure"`.
