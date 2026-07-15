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

- `--ocr-json` is the stable OCR/layout-anchor input. `benchmark` and `benchmark-structure-ab` accept it as well; both A/B branches share the same anchors, while only native-plus-structure receives `--structure-json`. This prevents a different text-node inventory from being counted as a structure-model gain.
- `PaddleOcrAdapter` and `PpStructureAdapter` are isolated in `scriptorium.ocr` and intentionally lazy-import `paddleocr`. `scriptorium run-paddleocr-vl` and `scriptorium run-pp-structure` both render source pages, preserve the source page index through Paddle result wrappers, and write replayable JSON. The PP-Structure runner defaults to layout-only execution; `--table-recognition`, `--formula-recognition`, and `--region-detection` opt into heavier evidence modules.
- `run-pp-structure` defaults to a CPU compatibility mode that sets Paddle 3.3's PIR/oneDNN guards before importing PP-StructureV3 and passes `enable_mkldnn=False`. GPU deployments can explicitly use `--no-cpu-compatibility-mode` after validating their local Paddle stack.
- `--structure-json` is the stable lightweight bridge for real model output. It accepts PaddleOCR-VL / PP-StructureV3 style JSON, DoclingDocument JSON, and relation-style `document` / `ro_linkings` payloads, then fuses region bbox, label, content, confidence, and external reading order back into `DocumentIR`.
- For image sources, `--structure-json` can also seed the initial text anchor layer when no separate `--ocr-json` is provided. Common `parsing_res_list` / `block_bbox` / `block_content` payloads, PP OCR dictionaries such as `overall_ocr_res`, and ROOR-style `document` segments with `box` / `text` are normalized into `native-ocr` text nodes before structure evidence is fused back onto them. The adapter follows common `res`, `result`, `data`, `pages`, `page_results`, `raw_results`, and `results` wrappers while preserving page-index fallbacks.
- `DocumentIR.metadata.semantic_layer` records the active semantic driver. Image cases report `structure-json`, `ocr-json`, `ocr-fallback`, or `visual-only`; native PDF cases report `native-pdf`. When independent OCR anchors already own text and bboxes, a region/role/order-only structure payload remains `augmenting-evidence`; explicit relations/streams or an actual reorder still promote it to `semantic-driver`.
- Native extraction has an `image-only` OCR fallback for scanned or screenshot PDFs. It triggers only when a page has no native text and image blocks cover most of the page, then emits `native-ocr` text anchors without replacing the original image element.
- `structure_evidence.py` parses nested `res`, `raw_results`, `pages`, `parsing_res_list`, `document`, and `layout_det_res.boxes` shapes. It also parses Docling `body.children` and `furniture.children` trees, resolves refs such as `#/texts/0` and `#/groups/0`, reads `prov` bbox/page evidence, supports ROOR-style `ro_linkings` as successor edges, and supports both top-left and bottom-left bbox origins. Real PP-StructureV3 JSON has now exercised this bridge on papers and a portal screenshot; PaddleOCR-VL, Docling, and model-supplied relation/stream JSON remain separate validation tracks.

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
   - `data-scriptorium-structure-stream-id`
   - `data-scriptorium-structure-stream-type`
   - `data-scriptorium-structure-stream-index`
   - `data-scriptorium-structure-stream-primary`
   - `data-scriptorium-structure-stream-kind`
   - `data-bbox-pdf`
   - `data-bbox-px`

In `structured` mode the exporter intentionally does not include the page background image. The result is made of editable text nodes, structural shape nodes, native image nodes, and local raster fallback nodes, all tied back to recognized evidence in the IR.

For image-only pages, the native image node remains the visual layer in `structured` mode. `native-ocr` nodes are transparent by default and become visible only on hover/focus, preventing duplicated text while still exposing editable DOM anchors and XML/IR text.

Text runs are a source-fidelity layer, not the edit storage model. When `edited_text` or `translated_text` is present, the exporter renders the replacement text as a plain editable node so stale source spans do not distort new content. Translation tools should first use `data-scriptorium-translation-stream-id` / `data-scriptorium-translation-stream-type` to preserve the primary order of body columns, sidebars, footnotes, tables, and grid cards. They may then use `data-scriptorium-structure-stream-*` to batch model paragraph/block subgroups inside one primary stream for translation and fitting. `structure-stream-primary="false"` means that subgroup must not replace the primary reading stream. Replacements still go into `translated_text` for the same HTML/PDF rendering path.

In `fidelity` mode, edited/translated replacements use `fidelity-replacement-fit-v3-browser`. The exporter still computes a conservative static scale and local mask padding, then independently clamps each side at adjacent visible boxes while ignoring enclosing page/background containers. After fonts are ready, `window.ScriptoriumFitting` measures the actual Chromium layout, binary-searches a scale from `0.62` to `1`, and tries a bounded `1.0` line height only when it materially raises the usable scale. It records the static estimate in `data-scriptorium-replacement-estimated-overflow` and the measured result in `data-scriptorium-replacement-rendered-overflow`, `data-scriptorium-replacement-rendered-fit-scale`, `data-scriptorium-replacement-rendered-line-height`, and `data-scriptorium-replacement-rendered-fit-policy`; `data-scriptorium-replacement-overflow` reflects the rendered result after fitting. The applied padding and its constraints remain visible in `data-scriptorium-replacement-mask-padding`, `data-scriptorium-replacement-padding-constrained`, `data-scriptorium-replacement-padding-constraint-ids`, and `data-scriptorium-replacement-padding-constraints`.

The mask defaults to white for ordinary dark source text. For light source text on a dark sampled raster edge, it uses an edge-sampled dark RGB mask so a white replacement is not hidden by a white overlay. Fidelity element geometry originates in source render pixels, while browser print geometry is 96-DPI CSS pixels; print-only `--print-*` variables convert the mask bbox, padding, and font size by `96 / render_dpi` before Chromium generates the PDF. This keeps replacement coordinates aligned at render DPIs such as 144 rather than scaling or shifting overlays. The same browser fitting pass is invoked after print media is selected and before PDF generation.

### Browser Edit Patches

Generated HTML includes a small browser bridge at `window.ScriptoriumEdits`. Editing an editable node promotes it from a transparent fidelity anchor to a visible local replacement, retains the change in the current browser session, and exposes a portable JSON patch through `collect()` or `download()`. The patch format is `scriptorium-html-edits/v1` and records the document id, element id, target field (`edited_text` or `translated_text`), replacement text, and exported source text.

Apply a downloaded patch to the original IR before exporting or printing again:

```bash
scriptorium apply-html-edits outputs/document.ir.json document.scriptorium-edits.json
scriptorium export-html outputs/document.ir.json --out-dir outputs/html --display-mode fidelity
scriptorium print-pdf outputs/html/index.html --pdf outputs/edited.pdf
```

The importer rejects a different document id, unknown element id, or changed source text by default, so a stale browser patch cannot silently write to the wrong anchor. `--allow-document-mismatch` and `--allow-source-mismatch` exist only for reviewed migrations.

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
- Some local Chromium builds can return a successful but visually blank Playwright PDF before local image assets are ready. `print_html_to_pdf()` now rejects an all-blank Playwright result, then retries through the Chromium CLI; the CLI advances 3 seconds of virtual time before printing so a cold local asset cache is rendered deterministically.
- Structured text lines keep `white-space: pre` and add `text-align-last: justify` in the default `structured` mode. Each line still uses its extracted PDF bbox, but the browser can expand word spacing to match justified PDF lines more closely. SVG text-fit is now the stronger optional path when browser font metrics are the dominant error.
- Short mixed text runs with script positioning, such as author footnote marks and compact superscripts, can be rendered as positioned child spans. The gate intentionally excludes long baseline-only citation/body lines because fully absolute run placement caused Transformer-XL page scaling and major visual regressions.
- `native-drawing`: simple lines render as SVG `<line>`. Supported non-rectangular drawing items (`l`, `c`, `re`, `qu`) render as positioned SVG `<path>` with fill/stroke opacity, avoiding the previous rectangular approximation for polygons and rounded paths.
- `native-raster-region`: when a page has a dense vector cluster with many line drawings, Scriptorium clips just that local region from the source PDF and exports it as one image node. Text and shape nodes whose centers fall inside that region are hidden to avoid duplicate rendering. Captions and surrounding body text remain editable.
- `--raster-policy tables` is available for explicit experiments with complex table vector regions, but it is not the default. On the current Attention, Transformer-XL, and Hacker News set it reduced visual similarity because Chrome's reprinted bitmap regions introduced more anti-aliasing/compression difference than the structured table renderer did.

This is an explicit fidelity/editability tradeoff: ordinary text, tables, separators, simple drawings, and supported SVG paths stay structured; very dense diagrams become local raster nodes until the vector renderer supports the required clipping, grouping, and blend-mode semantics.

## External Structure Evidence Fusion

PaddleOCR-VL, PP-StructureV3, and Docling are best treated as optional evidence providers rather than replacements for native PDF extraction. Native extraction usually gives better font/style/bbox fidelity for digital PDFs, while document models can add missing OCR, layout labels, table/formula/chart regions, and reading-order block predictions.

Paddle's documented `aside_text` layout label is normalized as a sidebar translation stream, alongside `sidebar_text`. This keeps page-side repository metadata, marginal notes, and similar secondary content out of the main body stream while retaining a visible, editable anchor.

`src/scriptorium/structure_evidence.py` implements the current bridge:

- `normalize_structure_evidence(payload, document)` accepts common Paddle JSON shapes, including `parsing_res_list` blocks with `block_bbox`, `block_label`, `block_content`, and `block_order`.
- Explicit block order remains the strongest block-order evidence. When `parsing_res_list`, `blocks`, or `elements` omit `block_order`, their list position can be recorded as weak `implicit-list` order only for text-flow, table, and explicit card/grid labels; nested `children`, `sub_blocks`, `sub_regions`, `items`, `cells`, and similar child lists are traversed in depth-first order. Images, figures, charts, page furniture, footnotes, and sidebars remain region/role evidence only, so a visual block's serialization position cannot pull an image caption or page artifact through the body flow. Pure `layout_det_res.boxes` detector output does not get implicit order.
- PP-StructureV3 `table_res_list` cells are normalized from `cell_box_list` or `table_ocr_pred.rec_boxes` / `rec_polys` plus `rec_texts` / `rec_scores`. When a matching parent table block exists, cells inherit that block's order and use row-major `external_structure_order_subindex`; otherwise they remain weak `implicit-table-cell` order evidence. For image sources, the same payload can seed initial `native-ocr` table-cell anchors before structure evidence is fused back.
- PP-StructureV3 OCR result dictionaries are also parsed directly: `overall_ocr_res` and `text_paragraphs_ocr_res` can seed text anchors from `rec_boxes` / `rec_polys` plus `rec_texts` / `rec_scores`, `formula_res_list` can seed `formula` anchors from `rec_formula`, and `seal_res_list` can seed seal-text anchors. These entries are unordered region evidence unless stronger block order, relation edges, or reading streams are present.
- Near-duplicate OCR entries with the same normalized text and highly overlapping bboxes are deduplicated before IR or structure regions are emitted. More specific labels such as `formula`, `seal`, and `table_cell` win over plain text, and `text_paragraphs_ocr_res` wins over the more generic `overall_ocr_res` when the boxes are otherwise equivalent.
- It also accepts DoclingDocument JSON. The parser traverses `body.children` in order, resolves JSON pointer refs into `texts`, `tables`, `pictures`, `key_value_items`, and `groups`, and turns item `prov` entries into page-local structure regions. Docling `furniture.children` is also parsed as non-body structure evidence for page headers, footers, and similar artifacts; those regions feed roles/streams but do not create body block-order evidence.
- Docling body-tree relations and streams are generated only from contiguous, same-page textual siblings inside one container. A group, table, picture, unresolved ref, page transition, or a root-body geometry break terminates the local run. This preserves useful local successor evidence without treating Docling's serialized body order as a global page permutation.
- A generic root-body Docling run is split again when it meets a stronger native table, grid, caption, sidebar, footnote, or page-artifact stream. The protected island retains its native stream; the applied segments keep their own `native-segment-*` provenance, and skipped boundaries are recorded for review. This prevents a portal card grid from being silently folded into a body translation stream.
- When a root-body Docling run lands on a concrete native column, its membership is retained as `external_structure_stream_*` with `external_structure_stream_primary = false`. Its relation record carries `secondary_native_column_flow = true`: the edge stays available to sidecar diagnostics, but cannot reroute the global path cover or re-enter as a generic external block stream. This keeps native multi-column translation streams primary while still exposing the model evidence for review; nested Docling groups remain executable.
- Docling table `data.table_cells` / `grid` entries with bbox and text are promoted to specific `table_cell` regions. They inherit the parent table's page provenance when needed, keep row/column/span/header metadata, and write `external_structure_order_subindex` from row-major cell coordinates so table-only islands can be reordered inside a shared parent block order.
- Docling bboxes are treated as PDF page coordinates. `coord_origin = TOPLEFT` is used directly; `coord_origin = BOTTOMLEFT` is flipped through the current page height before matching native elements.
- Pixel bboxes are converted to PDF-point bboxes using the page render scale already stored in `DocumentIR`.
- Nested model wrappers such as `page_results[*].data`, `res`, `result`, `raw_results`, and `pages` inherit the nearest explicit parent page index for region, relation, and stream evidence. When raw PP-StructureV3 `save_to_json` leaves `page_index` empty, Scriptorium also recognizes a rendered `input_path` basename such as `page_0005.png` and recovers source page index 4. This keeps sampled long-document pages aligned by source page number instead of accidentally falling back to wrapper-list position or requiring a hand-written JSON wrapper.
- `apply_structure_evidence(document, payload)` aligns model regions to native elements by element bbox coverage and text similarity.
- Pixel-coordinate structure JSON is normalized through its saved model-input canvas before it is compared with the current rendered page. In particular, PaddleOCR-VL's result-level `width` / `height` (including dimensions inherited through a nested `res` wrapper) map model pixels to page points and the current render pixels. The same raw JSON can therefore be replayed at a different `--dpi` without shifting region matches. Non-exact fragments with fewer than two alphanumeric characters are not allowed to match prose by substring, while exact one-character labels or cells remain valid.
- When a parent region and a more specific child region both cover the same text equally well, the smaller child region wins the match. This lets nested card/product/tile structures drive local reading streams instead of being swallowed by the parent grid bbox.
- PP-Structure commonly emits precise unordered OCR lines beside larger ordered `parsing_res_list` parents. When the two come from the same provider, have the same normalized label, pass bbox/text matching, and expose one unambiguous explicit parent order, the precise line remains the root `structure_evidence` and the parent is stored as `ordered_companion`. Its order is marked `external_structure_order_review_only`: it can produce a provenance-rich block transition, but cannot enter runtime partial order, the page-wide external-order candidate, or a derived block stream. Conflicting parent orders are rejected.
- A provider can declare `order_policy`, `relation_policy`, and `semantic_policy` as `review-only` at the root, page, or block/edge level. Review-only regions may match elements for provenance and proposal scoring, but cannot assign runtime roles, primary/secondary streams, semantic-layer ownership, or executable order. Review-only relations are retained in `external_structure_relation_edges` and sidecar diagnostics, but are excluded from runtime path cover and relation-derived streams. Benchmark reports keep them visible through `structure_evidence_review_region_count` and `structure_evidence_review_relation_edge_count` without mixing them into executable evidence counts.
- Matched text elements receive `structure_evidence`, `external_structure_label`, `external_structure_order`, `external_structure_order_source`, and optional `external_structure_order_subindex` metadata. PP-Structure and Docling table cells also expose `external_structure_table_cell_*` metadata and map to the same `table-cell-text` role / `table-island` stream.
- Structure JSON can also provide relation evidence through page-level or stream-level `successor_edges`, `successor_relations`, `ro_linkings`, `reading_order_edges`, `reading_order_relations`, `reading_order_linkings`, `precedence_edges`, `order_edges`, `relations`, `reading_streams`, or `streams`. Endpoints may reference matched structure node ids/refs, zero-based indices from `document`, `elements`, `blocks`, `parsing_res_list`, or `layout_det_res.boxes`, model-specific region ids such as `formula_region_id`, `seal_region_id`, `table_region_id`, or `layout_region_id`, raw OCR anchor ids/refs, or text. If an endpoint id or index does not resolve through matched region/node keys, Scriptorium falls back to the same page's structure-list text alias, so text-only relation payloads can still drive order when matching source text is already present. Resolved edges are stored on source elements as `external_structure_successor_ids` and `external_structure_precedence_target_ids`; alias-resolved edge records also expose `source_alias` / `target_alias` and `resolved_via_alias`.
- A relation endpoint or stream member may now name one matched structure block that covers several native/OCR lines. Scriptorium expands that block only when every candidate shares the same matched structure-region signature, preserves the existing local line order with internal successor edges, and connects external relations at the two block boundaries. Repeated visible text remains ambiguous rather than being expanded, and non-exact one-character text is not allowed to fuzzy-match ordinary prose.
- `reading_streams` / `streams` are also applied as stream metadata, not only relation sources. Stream members can come from text sequences, member lists, supported structure-list indices, or stream-local relation aliases such as `ro_linkings` and `reading_order_linkings`. Members use the same text-alias fallback as relation endpoints. Resolved members receive `external_structure_stream_*` plus `reading_order_stream_id`, `reading_order_stream_type`, and `reading_order_stream_index`, so OCR/image pages can expose translation-local body/sidebar/table/grid/card streams even when the structure JSON has no region bboxes. Member-level diagnostics preserve `external_structure_stream_member_ref`, `external_structure_stream_resolved_via_alias`, and the alias text when fallback was required.
- The IR records `relation_resolution_by_page` and `stream_resolution_by_page`, including resolved element ids, group versus alias resolution, overlapping endpoints, unresolved refs, and duplicate stream members. Aggregate `structure_evidence_*` metrics now expose group relation edges, injected group-internal edges, unresolved relation endpoints/edges, and resolved or unresolved group stream refs for benchmark triage.
- Relation-only sidecars now derive translation-local streams from safe successor chains. If a page provides `successor_edges`, `ro_linkings`, or equivalent reading-order relations but no explicit `reading_streams`, Scriptorium builds degree-constrained relation chains and writes them as `external-relation-*` streams. Explicit external streams win: relation-derived streams skip elements that already have `external_structure_stream_id`.
- When resolved successor/precedence edges form a safe acyclic path-cover order, the text reading order is reassigned with `reading_order_strategy = external-structure-relation-fusion-v1`. This lets OCR/structure JSON drive the semantic layer for image sources and complex pages without requiring one brittle global block permutation.
- When no relation order is available but at least two external block-order tiers are matched on a page, Scriptorium treats them as a partial order rather than a full-page permutation. It adds precedence constraints only between consecutive explicit tiers, uses the native reading order as the stable topological tie-breaker, and leaves unmatched elements in their local native positions. Generic model `text` order never flattens a stronger native table, grid, caption, artifact, footnote, or sidebar stream; a model block with an equally specific table/grid label can still participate. A real PP block may cover several native lines; those lines retain their local native sequence. Reassignments use `reading_order_strategy = external-structure-partial-order-fusion-v2` and append `external-structure-partial-order` to `reading_order_evidence`.
- Benchmark reports expose `structure_evidence_order_source_counts`, so A/B runs can distinguish explicit model order, Docling body-tree order, eligible implicit list order, and unordered visual/furniture regions. Reordered elements preserve model confidence under `reading_order_confidence` when it is stronger than the native heuristic confidence.
- External labels also feed reading-order scope and stream metadata: header/footer/page-number labels become page artifacts, footnotes and sidebars become secondary local streams, caption labels become caption streams, table labels become table-island streams, and explicit card/grid/product/tile labels become `grid-island` streams for translation/editing. Plain `list` labels remain list-role evidence instead of being promoted to grid streams, so ranked/news pages do not get false card-grid structure.
- An explicit ordered body/paragraph block can become an `external-block-body-*` translation subgroup when at least two matched text elements have coverage of at least `0.5`, share one selected native flow segment and column, and are not already owned by an explicit/relation stream. The subgroup is stored in `external_structure_stream_*` with `external_structure_stream_primary = false`; the primary `reading_order_stream_*` remains unchanged, and HTML exposes both layers through `data-scriptorium-structure-stream-*`. This preserves block boundaries for translation/fitting without fragmenting a stable multi-column stream. Generic diagnostic-only blocks and table/grid/caption/artifact/footnote/sidebar members are excluded. `derived_block_stream_count`, `derived_block_stream_member_count`, and `derived_block_streams_by_page` make the conservative derivation inspectable.
- The annotation pass maps external labels into roles, so labels such as `formula`, `header`, `footer`, `table_caption`, and `table` can affect the structured HTML metadata.

This gives the project an A/B path:

```bash
scriptorium convert input.pdf --out-dir outputs/native
scriptorium convert input.pdf --structure-json paddle.json --out-dir outputs/native-plus-structure
scriptorium benchmark input.pdf --structure-json paddle.json --out-dir outputs/benchmark-native-plus-structure
scriptorium benchmark input.pdf --font-profile local-urw --out-dir outputs/benchmark-local-urw
scriptorium benchmark-structure-ab page.png \
  --input-kind image \
  --ocr-json page.ocr.json \
  --structure-json page.structure.json \
  --out-dir outputs/page-image-ab
```

The benchmark command accepts one or more `--ocr-json` and `--structure-json` files, matched by argument order or by names such as `<source-stem>.ocr.json` and `<source-stem>.structure.json`. Reports retain OCR/structure provenance for each case. Real PP-StructureV3 CPU runs now cover Attention page 1, Transformer-XL pages 1-3, JD page 1, a PUMA mixed image/text page, and BYD financial-report page 136 with table recognition. A real PaddleOCR-VL 1.6 replay of PUMA p. 5 also validates the model-canvas mapping at both 96 and 144 DPI: the same raw JSON yields 24 matched elements, no selected reorder, and no candidate-disagreement delta at either DPI. Its four conservative block-derived streams cover 17 native body lines without changing that result. The labelled papers retain `1.0` pair and successor accuracy after fusion; the Transformer p. 1 replay derives six same-column block streams across 85 members while preserving successor accuracy `1.0`. JD derives none, correctly leaving its 35 native grid-island members intact. These are local translation boundaries, not a semantic-order accuracy claim: PUMA and JD still lack human relation sidecars, and PUMA's pseudo-translation conflict total remains unchanged. Across Transformer-XL pages 1-3, stream `needs-structure-evidence` decreases by 1 and consensus successor disagreement by 26. The BYD table run maps 10 cells into one row-major `table-island`, making table replacement conflicts attributable without claiming a total conflict reduction. For images and scanned PDFs, model evidence can become the primary text source; for digital papers, it should first be used as role/order/table/formula evidence while preserving native text and style.

OpenDataLoader PDF 2.4.7 is an Apache-2.0, deterministic CPU/Java provider for
digital PDFs. Install `requirements-opendataloader.txt` with Java 11+ and run
`scriptorium run-opendataloader`. The adapter requests XY-Cut order, saves both
raw and normalized replay JSON, converts bottom-left PDF boxes to Scriptorium's
top-left PDF coordinates, and emits stable `opendataloader-p####-b####` ids.
Malformed blocks break relation chains instead of joining their neighbors.
Pages that are valid in the provider document but absent from a sampled
`DocumentIR` are skipped with diagnostics; page numbers beyond the declared
document size are rejected.

Raw OpenDataLoader JSON is auto-detected by structure fusion. Its block labels,
orders, and adjacent successor edges all carry semantic/order/relation
`review-only` policies, so they can be scored and intersected with another
provider without changing roles, streams, semantic ownership, or runtime order.
The provider remains PDF-only; image sources continue to use OCR/layout JSON as
their primary semantic path.

Surya FastLayout is a separate optional provider because it pins model/image dependencies and its weights have terms beyond the Apache-2.0 code license. Install `requirements-surya.txt` in a dedicated environment and pass `--accept-model-license` only after reviewing the modified AI Pubs OpenRAIL-M weight/output license:

```bash
scriptorium run-surya-layout input.pdf \
  --page-ranges 1-3 \
  --device cpu \
  --accept-model-license \
  --output outputs/input.surya-layout.json
```

`SuryaLayoutAdapter` calls FastLayout detection with encoder features and invokes the learned order head directly. It fails closed when the head or feature map is unavailable, when the installed API does not expose model capacity, when detections exceed the advertised capacity (128 boxes for the tested weights), or when positions are fractional, invalid, duplicated, missing, or otherwise not a complete permutation. This prevents Surya's raster-order fallback from being serialized as learned evidence. Every emitted block carries `order_policy: review-only` and `semantic_policy: review-only`; every successor carries `relation_policy: review-only`. Model labels therefore cannot silently change roles or streams, and neither model order nor relations can reorder the IR.

For the local CPU runtime used in those runs, install `requirements-ocr.txt`, construct PP-StructureV3 with `enable_mkldnn=False`, and, with PaddlePaddle 3.3, set `PADDLE_PDX_ENABLE_MKLDNN_BYDEFAULT=0` and `FLAGS_enable_pir_api=0` before importing Paddle. These version-specific settings avoid the current CPU PIR/oneDNN regression; they are not a core Scriptorium runtime dependency.

The design follows the direction of reading-order research: LayoutReader / ReadingBank treats reading order as a first-class document understanding task, ROOR models reading order as relations over layout elements, and recent graph/path-cover work frames complex pages as multiple successor chains rather than one fragile visual scan. The implementation keeps the model runtime optional but accepts the same evidence shape: local successor edges, precedence edges, and stream memberships.

### Reviewable Reading-Order Sidecar

Every benchmark case now writes `reading-order.sidecar.proposal.json`. It is a `ScriptoriumReadingOrderSidecar` proposal rather than an automatically accepted order repair: `reading_streams` preserve local body, table, grid, sidebar, caption, and artifact membership; only confident local relations are emitted as `successor_edges`; weak local relations remain in `review_successor_edges`; and every cross-stream handoff is kept as a non-executable `review_transitions` record.

Sidecar schema `1.1` adds explicit model-block transition proposals without changing that contract. A relation is emitted only when two structure regions have unique numeric `block_order` values separated by exactly one, both orders are explicit, every matched member has at least `0.5` region coverage, and both blocks are primary text. Header/footer artifacts, sidebars, footnotes, captions, table/grid islands, ambiguous tied orders, implicit list order, and missing numeric tiers are hard boundaries. The relation connects the source block's last selected native member to the target block's first member, records provider/order/label/bbox/member provenance, and always remains `review_required`. `strict_block_transition_count` is therefore expected to stay zero until a separately accepted relation source changes the contract.

```bash
scriptorium propose-reading-sidecar \
  outputs/sample/document.ir.json \
  --sidecar outputs/sample/reading-order.sidecar.proposal.json
```

Independent providers can be intersected over the same stable document nodes:

```bash
scriptorium consensus-reading-sidecars \
  outputs/native/reading-order.sidecar.proposal.json \
  outputs/pp/reading-order.sidecar.proposal.json \
  outputs/surya/reading-order.sidecar.proposal.json \
  --min-providers 2 \
  --output outputs/reading-order.consensus.proposal.json
```

`build_provider_consensus_sidecar()` accepts only unaccepted sidecar proposals, unique provider names, identical page sets, and exact stable-element fingerprints over id, text, and PDF bbox. It intersects explicit block-order transitions by source/target ID, records provider provenance and the minimum confidence, and strips any selected-order marker. The output remains `sidecar_status: proposal`, `policy: review-only`, and `runtime_reorder: false`; consensus reduces review noise but is not an acceptance mechanism.

`sidecar_status: "proposal"` is deliberately ignored by `apply_structure_evidence()` and records a `proposal-skipped` revision. A reviewer or a future relation model must explicitly change it to `accepted` before its strict local edges can affect the IR. The sidecar's `document` nodes also make image/OCR seeding reproducible, but are tagged as references and never overwrite stronger region/table metadata during structure fusion.

When a semantic ground-truth sidecar exists, benchmark writes `semantic/reading_order_sidecar_proposal_quality_report.json` and reports strict-edge precision/coverage separately from review-edge precision/coverage. `reading_order_proposal_semantic_reviewable_successor_coverage` is the combined coverage of strict plus review edges, so an evidence threshold can move a correct edge into review without being mistaken for a semantic regression.

Explicit block transitions have their own strict/review candidate, labelled, correct, precision, and coverage fields. This prevents a model block-order edge from being hidden inside generic cross-stream transitions and keeps the invariant `strict = 0` measurable in normal benchmark and native-only versus native-plus-structure A/B reports.

For pages labelled with `match_mode: "ordered-subsequence"`, the report also distinguishes direct edges from graph paths between consecutive labelled anchors. `strict_anchor_path_coverage` traverses only executable local edges; `local_reviewable_anchor_path_coverage` additionally allows review-only local edges; `reviewable_anchor_path_coverage` also permits review-only cross-stream transitions. `review_block_transition_anchor_path_coverage` isolates the additional anchor paths unlocked specifically by explicit block-order proposals. A path is rejected if it crosses another labelled anchor, so it cannot turn an out-of-order anchor into a match. This is an evaluation-only view: review transitions remain non-executable until separately accepted. Unlabelled pages retain raw stream/edge/transition counts only; those counts are triage signals, not correctness claims.

`local_structure_successor_*`, `local_table_successor_*`, and `local_grid_successor_*` score only strict, non-review proposal edges carrying the native `table-local-order` or `grid-local-order` marker. A stream merely typed as table or grid is not enough: external model membership without that native marker is excluded. This keeps native geometry diagnostics separate from external structure evidence and prevents a provider-created table/grid stream from inflating local-edge precision.

Low `reading_order_confidence` no longer downgrades every local edge as a group. Before a review edge is promoted to strict, `reading_order_sidecar.py` requires it to remain within one provisional stream and pass three independent checks: mutual forward geometry, a selected full-page relation-graph edge with score `>= 0.86`, and direct successor agreement across visual-YX, box-flow, and relation-graph local candidates. The edge records all three evidence labels. The relation-graph API exposes selected path-cover edges separately from its serialized candidate order, which prevents a serialization handoff from being mistaken for a geometry relation. It now also preserves selection-time source/target alternatives, margins, and max-regret for each selected edge. A score tie is never used to promote a review edge; the review edge carries a `relation_graph` payload so a model or reviewer can resolve it with external structure evidence. Cross-stream transitions are intentionally excluded from this promotion path.

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
- `infer_relation_graph_selected_edge_diagnostics()`: exposes the selected local relation edges with their selection-time source/target alternative scores, margins, max-regret, selection step, and exact-tie flag. This is evidence for review and model fusion, not a claim that a serialized candidate order is correct.
- `structure_relation`: a benchmark-only semantic candidate that combines `reading_order_scope` metadata, page artifacts, footnotes, sidebars, caption-target proximity, and relation-graph body ordering into one structure-aware candidate order. It is deliberately diagnostic-only until sidecars or external model evidence prove when it should override the selected order.
- `infer_successor_consensus_order()`: a candidate-arbitration primitive. It takes adjacent successor edges from visual-yx, box-flow, relation-graph, structure-relation, and optional external-structure candidates, votes on those edges under acyclic one-predecessor/one-successor constraints, then serializes a path-cover candidate for benchmark scoring.
- `successor_consensus_diagnostics()`: exposes the same candidate plus support metrics: candidate count, candidate/unique edge counts, selected-edge support ratio, selected-edge coverage ratio, conflicted-edge ratio, and `high` / `medium` / `low` / `unavailable` agreement level.
- `reading_order_caption_type`: shallow caption evidence inferred from native/OCR text labels such as `Figure 1`, `Fig. 2`, `Table 3`, or `Algorithm 1`. Column-local captions stay in their column; captions that cross the column gutter become local flow breaks and carry `caption-label`, `figure-caption`/`table-caption`, and `cross-column-caption` evidence.
- `reading_order_caption_target_*`: local figure/table caption association evidence. The annotation layer links eligible figure/table captions to nearby native image elements, local raster regions, or inferred figure/table layout regions by type-compatible bbox proximity and horizontal alignment. It deliberately excludes near-full-page image backgrounds so scanned/screenshot OCR pages do not treat the page image as a figure target.
- `mixed-table-column-flow-v1`: a local table-island backend for mixed pages. It detects consecutive rows with repeated short-cell column slots, preserves those islands as row-major subregions, and infers surrounding prose columns from non-table text so table cells do not distort body-column detection.
- `mixed-grid-column-flow-v1`: a local grid-island backend for portal, ecommerce, and dense card-grid pages. It detects repeated non-table x slots across adjacent compact rows, preserves each grid island as row-major, marks `root/grid-island-###` / `grid-island-row-major` / `local-structure-grid`, and exposes a `grid-island` reading stream so translation can process cards separately from body prose.
- `table-row-major-v1`: a pure table-grid backend for table-dominated pages. It preserves row-major order with explicit table evidence instead of reporting an unqualified `visual-yx` fallback.
- `reading_order_confidence`: a bounded, conservative heuristic confidence for the assigned ordering path. It is not a semantic accuracy score; it summarizes evidence strength so later editors/translators can route low-confidence nodes to review or model assistance.
- `reading_order_evidence`: a machine-readable evidence list such as `single-column-visual-order`, `recursive-xy-cut`, `horizontal-whitespace-cut`, `column-flow`, `repeated-left-edge`, `spatial-graph`, `horizontal-overlap-chain`, `multi-head-flow`, `table-row-major`, `table-island-row-major`, `grid-island-row-major`, `local-structure-grid`, `page-edge-artifact`, `footnote-secondary-flow`, `bottom-note-zone`, `sidebar-secondary-flow`, `external-structure-relation`, or `external-structure-partial-order`.
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

The relation-graph diagnostic is also separate from strategy selection. It compares the current semantic order against a geometry-only local successor graph and reports both pairwise and adjacent-successor disagreement. The successor metric is the more relevant signal for this candidate because the graph predicts immediate next-node relations before serialization. It additionally reports path-cover edge count, exact-tie count/rate, and mean minimum selection margin. These are ambiguity/triage signals rather than correctness scores: a low margin can be wrong even when it is not an exact tie. Current results show lower local successor disagreement than box-flow on the complex samples, but pairwise disagreement remains high enough that the graph must stay candidate-only until semantic sidecars or external model evidence can arbitrate when it should take over.

The successor-consensus diagnostic is the next arbitration layer. It does not create new geometry rules; instead, it asks whether independent candidates agree on local successor edges. This follows relation/path-cover reading-order work: shared immediate edges are stronger evidence than broad global rank agreement, while disagreement highlights pages that need semantic sidecars, model structure evidence, or manual review. The support/conflict metrics are intentionally separate from correctness: a high-support consensus can still be wrong, but a low-support or high-conflict page should not be automatically reordered without stronger evidence.

The runtime successor-consensus arbitration path is deliberately stricter than the benchmark diagnostic. It excludes visual-yx from the internal vote and requires high agreement between box-flow and relation-graph candidates, a visual-vs-consensus successor disagreement, and large upward x-shift handoff(s) consistent with moving from the bottom of one column to the top of the next. This targets sparse multi-column pages that do not meet the minimum evidence thresholds for repeated-anchor column flow, spatial graph, or selected box-flow fallback.

Benchmark page diagnostics now expose the same idea one level closer to runtime arbitration. For every page with at least two editable text nodes, `reading_order_candidate_page_diagnostics` compares the selected semantic order against the successor-consensus order, records candidate names, support/coverage/conflict metrics, pairwise and successor-edge disagreements, and assigns a review recommendation. This works even when no semantic sidecar exists. The recommendation is a triage label, not an automatic order switch.

Native table/grid-island evidence is deliberately separate from that page-wide vote. A strict proposal edge marked `table-local-order` or `grid-local-order` is exported as `local_structure_*` diagnostics: candidate local streams, potential and strict successor-edge counts, strict coverage, selected-order coverage, reference-page coverage, and strict edges that the generic consensus does not preserve. It never adds a second selected-order vote or creates a cross-stream handoff. A fully covered individual island can be reported as `keep-selected-local-structure`; a page remains `needs-structure-evidence` when its body flow or inter-region transitions are unresolved. `benchmark-structure-ab` carries the same counts and deltas, so a provider that changes generic disagreement but weakens stream triage cannot be mistaken for an unqualified semantic improvement.

`protected_successor_consensus` is the next, still diagnostic-only, relation candidate. It installs valid strict native table/grid edges before the generic weighted path cover, rather than assigning them synthetic votes. Its fields distinguish protected edges from unresolved constraints and record unknown endpoints, self-loops, incoming/outgoing degree conflicts, and cycles separately. `local_structure_constrained_consensus_disagreement_*` measures only strict island edges still missing after this constrained serialization. The candidate never changes `infer_semantic_reading_order()` or runtime arbitration, and it is excluded from automatic semantic-candidate recommendations. If no strict native edge is applicable to a labelled case, its aggregate semantic score is `null`, not a misleading perfect score.

The full ROOR validation run confirms why this remains diagnostic-only. With official text/layout anchors and withheld `ro_linkings`, all 49 pages resolve endpoints through stable element IDs with no unresolved identifiers; this preserves 2,612 official relations despite duplicate segment text. Strict native local edges score `316/617` (`0.51215559`) on directly labelled endpoints; the protected candidate scores `0.41918103` on its eligible relation scope and does not beat the selected native order (`0.48774885`). Constraint preservation therefore proves serializer behavior, not relation correctness. Runtime hard constraints require explicit external successor/stream evidence, a separately validated relation predictor, or accepted review. See [External benchmarks](external-benchmarks.md#roor-relation-benchmark-v1) for the leakage boundary and full results.

Semantic sidecar scoring now includes the diagnostic-only `protected_successor_consensus` candidate next to visual-yx, box-flow, relation-graph, structure-relation, successor-consensus, and external-structure. This lets benchmark reports measure relation-preserving candidates without promoting a single unlabeled sample into runtime behavior.

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
- ROOR models reading order as directed ordering relations instead of one mandatory permutation, matching the local-successor / review-transition contract here: https://aclanthology.org/2024.emnlp-main.540/
- PP-StructureV3 documents multi-column reading-order recovery together with layout, table, formula, and chart structure: https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/PP-StructureV3.html
- BabelDOC's typesetting design first fits a paragraph in its original box, then reduces line spacing, expands only in the writing direction when space is safe, and finally scales text; this informs the planned stream/region reflow pass: https://funstory-ai.github.io/BabelDOC/ImplementationDetails/Typesetting/Typesetting/
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
  "version": 3,
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

The same evaluator also accepts ROOR/structure-style payloads. A sidecar can provide page-level `document`, `elements`, `blocks`, `parsing_res_list`, or `layout_det_res.boxes` entries with text and optional ids, then reference those labels from `ro_linkings`, `reading_order_edges`, `reading_order_relations`, or `reading_order_linkings`. Ordered structure lists without explicit ids can also be referenced by zero-based list index, which matches common relation-model outputs such as `[[0, 2], [2, 1]]`. It can be wrapped in standard `pages`, or reuse model-root wrappers such as `page_results`, `raw_results`, `results`, `res`, `result`, and `data`:

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

`successor_edges` and ROOR-style linkings score immediate adjacency among labelled nodes, ignoring unlabelled actual text between them. `precedence_edges` only require the source label to appear before the target label. A generic `relations` list is also accepted when each item explicitly declares a successor or precedence type/kind. Relation endpoints may be text strings, list indices, arrays, or dictionaries using aliases such as `source` / `target`, `from` / `to`, `head` / `tail`, or `source_id` / `target_id`; ids and supported list indices are resolved through the page label map before scoring. The label map also understands model region ids such as `formula_region_id`, `seal_region_id`, `table_region_id`, and `layout_region_id` from PP/structure payloads, so sidecars can reuse the same ids that runtime structure fusion uses. Semantic page payloads also read same-page `res`, `result`, `data`, `page_results`, `raw_results`, and `results` wrappers before scoring relation and stream labels. If a page has relation edges but no `text_sequence`, `match_mode` defaults to `ordered-subsequence` so the page is not penalized for unlabelled text. Candidate orders receive the same relation metrics as the selected order.

Stream sidecars follow the same shape. `text_sequence`, `sequence`, or `texts` are ordered and generate stream-local successor/precedence checks. `members`, `elements`, `items`, and `children` identify stream labels for missing/coverage diagnostics without implying order by themselves; stream-local `ro_linkings`, `reading_order_*`, or typed `relations` provide the explicit ordering constraints.

Supported page match modes:

- `full-sequence`: the default mode for generated fixtures; expected and actual page text should match exactly except for reported missing/extra nodes.
- `ordered-subsequence`: intended for real PDFs with partial human labels; only the listed text nodes are scored, unlisted actual text is counted as ignored, and pairwise order is still evaluated across the labeled nodes.

Metrics:

- `semantic_order_pair_accuracy`: pairwise order correctness across expected text nodes; this is Kendall-tau-like and catches left/right column swaps.
- `semantic_successor_accuracy`: adjacent successor-edge correctness across expected text nodes. In `ordered-subsequence` mode, unlabelled actual text between two labelled nodes is ignored, but a labelled node inserted between them, a reversed adjacent pair, or a missing adjacent node breaks the edge. Reports also expose `semantic_successor_correct_count` and `semantic_successor_total_count`.
- `semantic_relation_successor_accuracy`: correctness for explicit `successor_edges` or ROOR-style reading-order linkings. This is the relation-style metric to watch when evaluating relation-graph, structure-relation, and successor-consensus candidates on complex layouts.
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

This writes deterministic pseudo-expanded text into `translated_text`, then scores visual similarity plus replacement overflow/conflict/fit-scale metrics. It is useful for JD/PUMA/portal samples where the source page may look perfect with a background layer but translated replacements can still collide locally. Fidelity reports now separate `fidelity_replacement_estimated_overflow_count` from `fidelity_replacement_overflow_count`: the first is the retained static predictor, while the second is actual Chromium clipping whenever `fidelity_replacement_layout_measurement_available` is true. They also expose measured count, browser-fit count, line-height-compaction count, sampled-background-mask count, actual versus static fit scales, and per-stream versions of those diagnostics. Each fidelity case writes `quality/fidelity_replacement_layout_report.json` with DOM dimensions and clipping evidence.

The v3 JD/PUMA/web-HN rerun covers the same 15 pages with no page-count or dimension mismatch. Mean visual similarity is `0.92760169` (`0.81937118` in the v2 padding-only run), with max / mean / p95 diff of `0.10089579` / `0.04620805` / `0.09823334`. It contains 567 replacements, 326 static estimated overflows, and 81 actual Chromium clips; these are different measures and must not be presented as a direct `326 -> 81` reduction. All 567 replacements received browser fitting, 81 used line-height compaction, and 101 used sampled dark masks. JD retains 79 of the actual clips, which makes generic local flow reflow, rather than more mask shrinking, the next fidelity target.

External structure evidence can be evaluated with a paired A/B run:

```bash
scriptorium benchmark-structure-ab input.pdf \
  --structure-json input.structure.json \
  --out-dir outputs/structure-ab \
  --dpi 144
```

This writes `native-only/benchmark_report.json`, `native-plus-structure/benchmark_report.json`, `structure_ab_report.json`, and `structure_ab_summary.csv`. The A/B report records deltas for visual similarity, reading-order risk, `grid_island_element_count`, structure-evidence region/match/reorder counts, block-group relation expansion and unresolved relation/stream-reference counts, page/stream `needs-structure-evidence` recommendations, review recommendations, successor-disagreement counts, semantic successor metrics, semantic relation/stream/assignment missing-label counts, and semantic stream-assignment id/type accuracy when sidecars exist.

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
- `fidelity_replacement_element_count`, `fidelity_replacement_overflow_count`, `fidelity_replacement_conflict_count`, `fidelity_replacement_conflict_target_count`, `fidelity_replacement_same_stream_conflict_target_count`, `fidelity_replacement_cross_stream_conflict_target_count`, `fidelity_replacement_padding_constrained_count`, `fidelity_replacement_padding_constraint_side_count`, `fidelity_replacement_min_fit_scale`, `fidelity_replacement_mean_fit_scale`, and `fidelity_replacement_policy_counts`: replacement-risk diagnostics for edited/translated nodes in fidelity mode. The two padding fields show how many masks and directional sides were constrained to avoid adjacent visible elements. These fields stay zero/`null` for source-only runs and become the primary non-visual quality signal for translated HTML-to-PDF round trips.
- `fidelity_replacement_conflict_target_stream_type_counts`, `fidelity_replacement_conflict_target_stream_id_counts`, `fidelity_replacement_conflict_stream_type_pair_counts`, and `fidelity_replacement_conflict_stream_id_pair_counts`: conflict-target attribution for translated replacement masks. Same-stream target conflicts usually mean local text fitting is too tight; cross-stream target conflicts usually point to stream-boundary errors or over-expanded masks.
- `fidelity_replacement_stream_diagnostics`, `fidelity_replacement_stream_type_counts`, `fidelity_replacement_stream_type_overflow_counts`, `fidelity_replacement_stream_type_conflict_counts`, `fidelity_replacement_stream_id_counts`, `fidelity_replacement_stream_id_overflow_counts`, and `fidelity_replacement_stream_id_conflict_counts`: stream-local replacement diagnostics. They split the same replacement risk by `reading_order_stream_id` / `reading_order_stream_type`, so JD/PUMA/portal runs can show whether translated collisions live in the body flow, card grids, sidebars, footnotes, table islands, or page artifacts. Each stream diagnostic also includes same-stream/cross-stream target counts and stream pair-count maps for local triage.
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
- `reading_order_relation_graph_path_cover_page_count`: pages where the geometry relation graph selected at least one executable path-cover edge.
- `reading_order_relation_graph_path_cover_edge_count`: selected local path-cover edges, excluding serialization-only handoffs.
- `reading_order_relation_graph_tied_edge_count` / `reading_order_relation_graph_tied_edge_ratio`: selected edges with an exactly equal feasible source or target alternative, and their share of selected path-cover edges.
- `reading_order_relation_graph_margined_edge_count` / `reading_order_relation_graph_mean_minimum_margin`: selected edges that had at least one alternative, plus the mean of their weaker source/target selection margin. These are not calibrated probabilities.
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
- `structure_evidence_region_count` and `structure_evidence_review_region_count`: normalized external regions loaded from Paddle/PP-Structure/Docling/Surya structure JSON, and the subset whose semantic policy is review-only.
- `structure_evidence_relation_edge_count`, `structure_evidence_review_relation_edge_count`, and `structure_evidence_resolved_relation_edge_count`: external successor/precedence edges loaded from structure JSON, the subset excluded from runtime by review-only policy, and edges successfully resolved to current page elements.
- `structure_evidence_resolved_relation_alias_edge_count`: resolved relation edges that required the text-only structure-list alias fallback rather than direct region/node-key matching. The per-edge metadata keeps the raw refs plus the alias text that made the fallback resolvable.
- `structure_evidence_stream_count`, `structure_evidence_resolved_stream_member_count`, and `structure_evidence_stream_conflict_count`: external reading streams loaded from structure JSON, members resolved to current elements, and overlapping stream assignments that were skipped.
- `structure_evidence_resolved_stream_alias_member_count`: stream members that required the same text-only alias fallback. Applied elements keep the source member ref and whether the member was resolved through an alias.
- `structure_evidence_relation_stream_count`, `structure_evidence_resolved_relation_stream_member_count`, and `structure_evidence_relation_stream_conflict_count`: streams derived from relation-only successor chains, resolved member count, and reserved conflict diagnostics. Explicit external streams are skipped before relation-derived stream assignment and are not counted as conflicts.
- `structure_evidence_matched_element_count`: native elements matched to those regions by bbox/text evidence.
- `structure_evidence_reordered_page_count`: pages whose text order was reassigned from external structure evidence.
- `structure_evidence_relation_reordered_page_count` and `structure_evidence_order_reordered_page_count`: pages driven by relation path-cover evidence versus block-order evidence.
- `semantic_order_pair_accuracy`: pairwise semantic order score when ground truth is available.
- `semantic_successor_accuracy`: labelled adjacent successor-edge score when ground truth is available.
- `semantic_successor_correct_count`, `semantic_successor_total_count`: raw successor-edge counts used for case and summary aggregation.
- `semantic_relation_successor_accuracy`, `semantic_relation_successor_correct_count`, and `semantic_relation_successor_total_count`: explicit relation-edge adjacency score from sidecar `successor_edges`.
- `semantic_relation_precedence_accuracy`, `semantic_relation_precedence_correct_count`, and `semantic_relation_precedence_total_count`: explicit before/after relation score from sidecar `precedence_edges`.
- `semantic_relation_missing_text_delta`, `semantic_stream_missing_text_delta`, and `semantic_stream_assignment_missing_delta` in `benchmark-structure-ab`: native-plus-structure minus native-only missing-label counts. Negative deltas show that structure JSON made sidecar labels resolvable in extracted text or stream metadata.
- `semantic_stream_successor_accuracy` and `semantic_stream_precedence_accuracy`: stream-local order scores from sidecar `reading_streams` / `streams`. These measure whether labelled text appears in the expected local successor or before/after relation inside body, sidebar, table, caption, footnote, or grid streams.
- `semantic_stream_assignment_id_accuracy`, `semantic_stream_assignment_type_accuracy`, and their label/found/missing/correct count fields: assignment scores for the selected IR. These compare sidecar stream membership against each element's `reading_order_stream_id` and normalized `reading_order_stream_type`, so translation re-rendering can verify that OCR/structure JSON produced the expected local streams, not only a plausible global order.
- `semantic_stream_assignment_id_mismatch_count`, `semantic_stream_assignment_type_mismatch_count`, and `semantic_stream_assignment_type_confusion_counts`: stream-assignment triage fields. Confusion keys use `expected=>actual`, for example `grid-island=>body`, so complex-page regressions show whether structure evidence is failing on card grids, sidebars, table islands, captions, footnotes, or page artifacts.
- `semantic_stream_assignment_id_accuracy_delta` and `semantic_stream_assignment_type_accuracy_delta` in `benchmark-structure-ab`: native-plus-structure minus native-only stream-assignment accuracy. Positive deltas show that structure JSON improved local translation-stream membership, even when visual similarity is mostly unchanged.
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

Selection-time ambiguity validation:

| Sample | Command Output | Visual Similarity | Path-Cover Edges | Exact Ties | Mean Minimum Margin | Interpretation |
|---|---|---:|---:|---:|---:|---|
| Transformer-XL first 3 pages | `outputs/external/transformer-xl-relation-ambiguity-v1` | 0.98160664 | 288 | 3 (1.041667%) | 0.00123018 | The labelled paper remains visually and semantically stable; ties remain review evidence rather than strict promotion. |
| PUMA 2024 Annual Report first 12 pages | `outputs/external/puma-2024-annual-report-relation-ambiguity-v1` | 0.9795117 | 329 | 0 | 0.03710031 | The annual-report bottleneck is mostly weak geometry/model evidence rather than exact path-cover ties. |
| JD homepage screenshot PDF | `outputs/external/jd-home-relation-ambiguity-v1` | 0.99576887 | 93 | 2 (2.150538%) | 0.03896739 | Dense OCR/card layout still needs explicit streams or successor relations for local semantics. |
| BYD annual report p. 136 | `outputs/external/byd-2024-annual-report-relation-ambiguity-v1` | 1.0 | 30 | 0 | 0.09570952 | This table-heavy financial page has no exact tie, but table/stream evidence remains necessary for translation-local handling. |

The new fields do not alter selected runtime order. They expose where a geometry-only graph has no preference, keep ties out of automatic sidecar promotion, and give Paddle/Docling or a future semantic scorer an explicit local target to resolve. Margin thresholds are intentionally not calibrated from these benchmark labels; any future automatic use must be validated on held-out relation-style sidecars.

Latest semantic candidate scoring validation:

- `semantic_quality.py` now scores named candidate element-id orders against sidecars and reports `semantic_candidate_order_metrics`.
- Sidecars can include `successor_edges` and `precedence_edges`, so complex layouts can be evaluated as relation constraints instead of only as one serialized `text_sequence`.
- `scriptorium benchmark` automatically supplies `visual_yx`, `box_flow`, `relation_graph`, `structure_relation`, and `successor_consensus` candidates for each page.
- `external_structure` is supplied when Paddle/PP-Structure/Docling evidence has either resolved successor/precedence relations or at least two distinct `external_structure_order` values on a page. Relation edges are converted into a path-cover order first; otherwise block orders become adjacent-tier partial precedence constraints, with native order retained as the stable tie-breaker.
- Case reports and CSV include flattened candidate accuracies such as `semantic_relation_graph_successor_accuracy`, `semantic_structure_relation_successor_accuracy`, `semantic_successor_consensus_successor_accuracy`, and relation-edge variants such as `semantic_structure_relation_relation_successor_accuracy`.
- Summary reports aggregate candidate successor accuracy and `semantic_best_candidate_by_successor_counts`.
- Case and summary reports also include arbitration diagnostics, including candidate-vs-selected successor/pairwise deltas and recommendation counts.
- Case reports also include page-level candidate diagnostics that do not require sidecar labels, so external complex PDFs can be queued for review before ground truth exists.
- Reading stream labels are scored in two separate ways. Successor/precedence metrics evaluate local ordering, while stream-assignment metrics evaluate whether selected IR elements carry the expected `reading_order_stream_id` and `reading_order_stream_type`. Candidate orders intentionally skip assignment scoring because membership/type is a property of the extracted semantic layer, not a sorted candidate list.
- Unit coverage lives in `tests/test_semantic_quality.py::test_candidate_orders_are_scored_against_semantic_ground_truth`, `tests/test_semantic_quality.py::test_reading_stream_assignments_score_ir_stream_metadata`, `tests/test_reading_order.py::test_successor_consensus_diagnostics_report_support_and_conflict`, `tests/test_reading_order.py::test_successor_consensus_diagnostics_downgrades_cycle_conflict`, `tests/test_reading_order.py::test_successor_consensus_arbitration_orders_sparse_two_column_page`, `tests/test_reading_order.py::test_successor_consensus_arbitration_preserves_sparse_three_column_metadata`, `tests/test_benchmark.py::test_candidate_page_diagnostics_recommend_review_for_supported_disagreement`, and benchmark field assertions in `tests/test_benchmark.py`.

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

## Docling Review Provider

`scriptorium run-docling` accepts PDF and image sources and writes raw Docling
JSON plus a normalized structure payload. The optional environment pins
`docling==2.111.0`, `docling-core==2.86.0`, and
`docling-ibm-models==3.13.3`. Docling code and IBM models are MIT licensed; the
Heron checkpoint is Apache-2.0. Heron is a learned RT-DETR layout detector, but
Docling's downstream reading-order predictor is rule-based.

The normalized payload marks semantics, order, and relations `review-only`,
disables provider streams, isolates the external candidate from successor
consensus, and sets `runtime_reorder: false`. Existing accepted OCR evidence is
preserved separately, and review-only identifiers cannot change the semantic
label denominator. Use normalized `--output`, not `--raw-output`, in A/B runs.

`candidate_consensus_policy: isolated` is a provider-neutral structure contract,
not a Docling special case. Generic page/block/relation JSON propagates the
isolation marker to every matched element and resolved relation endpoint. The
external candidate remains sidecar-scored, while successor consensus and
page/stream recommendations are computed from the same native candidates as
the control branch.

## Trainable Relation Ranker

The optional `requirements-relation-ranker.txt` path trains a
`HistGradientBoostingClassifier` over normalized source/target geometry,
overlap, direction, size, and small text-shape features. Training reads only
the official ROOR `data.train.txt`. A SHA-256 UID partition reserves an internal
calibration subset; threshold selection maximizes top-successor relation F1 on
that subset. Validation files and benchmark sidecars are never opened by the
trainer.

`train-relation-ranker` writes a local joblib model and adjacent manifest with
the feature schema, train-index digest, fit/calibration counts, dataset license,
threshold, calibration metrics, sklearn version, and model SHA-256.
`run-relation-ranker` verifies the digest before loading, rejects any input that
already contains successor/`ro_linkings` answers, and emits confidence-bearing
review-only edges with `candidate_consensus_policy: isolated` and
`runtime_reorder: false`. Joblib can execute code while loading; only locally
generated bundles are trusted.

ROOR relations are not always a single path: some source nodes have multiple
valid immediate successors. The v2 bundle therefore trains a second binary
branch gate from the fit partition. It receives source geometry, the top two
pair scores and margins, and selected pair geometry features. A separate
calibration sweep decides whether to emit the rank-2 edge. Inference emits at
most two successors, with independent `confidence` and `branch_confidence`;
it never turns the pair scorer into an unrestricted global edge threshold.

`run-relation-ranker` also accepts a multi-page `DocumentIR`. Every text-bearing
page is projected into normalized PDF-space segments, scored by the same model,
and emitted as generic `pages/elements/successor_edges` structure JSON. This
makes native PDF text, image OCR anchors, annual reports, and portal screenshots
use one inference path rather than a ROOR-specific runtime.

The model bundle stores per-feature 1%/99% envelopes computed only from the fit
partition. Each output page reports mean pair confidence, edge-level envelope
outlier ratio, and feature-value outlier ratio. These are domain-shift
diagnostics, not correctness estimates: a model can remain highly confident on
out-of-domain pages. They are intended as a future runtime rejection signal and
as a way to prioritize independent labels.

`requirements-semantic-order.txt` adds an isolated semantic-research path. The
current preset pins the Apache-2.0 Google BERT-Tiny checkpoint and revision,
uses the pretrained NSP head to compute `log p(IsNext)`, and stores scores in a
content-addressed SQLite cache. Model identity, revision, license, truncation,
and score formula are part of the feature contract; a local snapshot may be
used for acquisition, but cannot change that identity. Semantic bundles require
the same scorer at inference, while v2 geometry bundles reject the extra feature
instead of silently accepting a different input shape.

The first direct-feature experiment is deliberately not promoted. With the same
122/27 ROOR train fit/calibration documents, 138,513 examples, and seed as v2,
adding Tiny NSP as feature 26 reduced top-edge F1 from `0.65488640` to
`0.64327062` and branch F1 from `0.66737288` to `0.65594855`. The optional path
exists to make semantic A/B reproducible, not to claim a new default; all output
remains review-only and runtime reorder stays disabled.

The v4 semantic path uses a two-stage contract instead. The unchanged v2 pair
estimator first selects at most five targets per source; only those candidates
receive NSP scores. A 31-feature reranker combines base score/rank/margins, raw
and relative NSP scores, and the original 25 pair features. Its threshold comes
from five document-hash OOF folds over fit documents, not the 27-document
calibration partition. The final fit candidate recall is `0.94110838`; only
35,751 unique ROOR pairs are cached instead of the direct path's 402,395. The
frozen calibration top/branch F1 values are `0.67855816/0.69343066`.

```bash
pip install -r requirements-semantic-order.txt
scriptorium train-relation-ranker path/to/ROOR-Datasets/data \
  --semantic-scorer bert-tiny-uncased-nsp \
  --semantic-fusion top-k-rerank \
  --semantic-top-k 5 \
  --semantic-cache outputs/cache/semantic-successor.sqlite3 \
  -o outputs/models/semantic-relation-ranker.joblib
```

The Comp-HRDoc relation evaluator also runs in two strict phases: it finishes
every mode prediction before resolving or opening any semantic sidecar. Reports
declare `labels_opened_after_all_predictions: true`; optional semantic rankers
must receive the same pinned scorer/cache through the benchmark CLI.

`benchmark-relation-rankers-roor` applies the same two-phase contract to a
fetched ROOR corpus and compares top, branch, and degree-one path-cover edges.
On all 49 official validation pages, v4 improves branch F1 from `0.69167292` to
`0.73061145` and path-cover F1 from `0.68729852` to `0.71334792`.

The hierarchy benchmark can receive v4 through `--relation-model` plus the same
semantic scorer options. Native geometry remains authoritative for membership,
within-region streams, and existing region transitions. A semantic edge cannot
fill an empty region slot: it may replace exactly one conflicting native region
edge only when both are boundary-aligned, the semantic confidence is at least
`0.10` higher, and replacement stays acyclic. This preserves transition count
and local metrics. The 64-page development corpus performs four replacements;
its calibration line/region F1 reaches `0.93209877/0.90690691`. A separate
32-page official-test window performs two replacements and improves line/region
F1 to `0.94255569/0.91990847`, with membership and within-region F1 unchanged.

Hierarchy policy `local-streams-with-relation-graph-transitions-v4` adds an
object-branch endpoint contract before region degree/cycle selection. A
boundary edge whose source region role is `table` is retained as
`table-region-terminal-branch` evidence only; an edge whose target region role
is `figure` is retained as `figure-region-root-branch` evidence only. This
preserves the useful caption directions (figure to caption, caption to table)
without serializing a graphical object as a bridge between body streams.
Diagnostics expose aggregate, table-source, and figure-target suppression
counts. The rule is case-insensitive and applies equally to native and semantic
inferred edges.

On the 64-page development corpus it suppresses 38 candidates and raises
aggregate line/region F1 to `0.94870087/0.92708333`. Calibration reaches
`0.94968553/0.92638037`. Frozen replay on the separate 32-page official-test
window suppresses 24 candidates and reaches `0.94811321/0.93055556`, above the
flat controls `0.94712644/0.90064795`. Correct-edge recall, membership, and
within-region F1 are unchanged. The benchmark still emits a review-only partial
DAG with `runtime_reorder: false`; oracle-region success is not end-to-end
provider-region promotion evidence.

Policy `local-streams-with-relation-graph-transitions-v5` preserves abstained
fine elements without flattening assigned provider regions. Consecutive
unassigned ids in selected-native order form `unassigned-fallback` streams.
Their internal adjacency and stream-boundary transitions remain review-only at
confidence `0.5`. A boundary candidate is eligible only when at least one
endpoint is unassigned, the source has no successor, the target has no
predecessor, and adding it does not close a cycle. Diagnostics report stream,
member, internal-edge, candidate, emitted, degree-suppressed, and
cycle-suppressed counts. The candidate element permutation is computed before
this layer and remains unchanged.

### Provider-Derived Hierarchy Benchmark

`materialize-provider-hierarchy` joins answer-free fine hierarchy inputs to one
normalized provider block file per sample. It deliberately completes and
writes all adapted inputs before resolving any source label path. Provider
sequence and relation fields are discarded by the normalizer; input invariance
tests mutate those fields and hierarchy labels independently. When a Paddle
corpus-run manifest is present, its upstream corpus SHA and complete generated
plus skipped sample-id set must match the hierarchy source manifest.

`benchmark-provider-hierarchy` likewise predicts every selected input before
opening labels. `--partition` enforces fit/calibration isolation, and every
input, label, proposal, provider output, corpus manifest, optional provider-run
manifest, and optional relation model is hash-bound. The frozen PP-DocLayout
membership settings are coverage `0.10` and margin `0.10`; the oracle default
`0.80` is intentionally not reused for padded detector boxes.

Provider and oracle segmentations do not share region ids. The primary metric
therefore unions local-stream and transition edges and scores line successor
relations. Precision excludes predictions whose endpoints have no published
relation label but reports them as `unscored`; assignment coverage, pairwise
co-membership F1, and recovery of oracle-within versus oracle-cross relations
remain separate diagnostics. This is a correspondence-aware evaluation rather
than an exact-region replay, following the mismatch treatment in the PRImA
reading-order metric:
https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf

On 50 fit pages, provider hierarchy at the frozen coverage threshold scores
`0.94842599`; v5 fallback raises it to `0.97433893`, versus flat
`0.94768195`. On 14 calibration pages, the same values are
`0.93254330 -> 0.96754386`, versus flat `0.97694650`. The independent 32-page
provider test reaches precision/recall/F1
`0.96979086/0.97054264/0.97016660`, above flat `0.96606248`, with assignment
coverage `0.97687225` and segmentation pair F1 `0.80643143`. Semantic and
native v5 are identical on that test. Because calibration still trails flat and
segmentation remains imperfect, the report decision is
`provider-derived-development-benchmark-review-only` and runtime reorder stays
disabled.

Policy v6 makes provider origin explicit instead of inferring it from a schema
name. Both the real `DocumentIR` adapter and corpus materializer write
`input_adapter.coarse_region_source = normalized-provider-structure`; older or
oracle hierarchy inputs do not activate the new guards. For each provider
region, consecutive members in selected order remain one stream. A skipped
member pair is also retained when its normalized vertical gap is within
`[-0.25, 1.25]` mean line heights. Otherwise the region is represented by
multiple streams with stable `-segment-NNN` ids, and no successor edge is
invented across the discontinuity.

Native boundary transitions are separately gated by absolute selected-base
rank displacement `<= 4`. A rejected edge remains in
`cross_region_relation_evidence` with
`provider-nonlocal-selected-rank-gap`; external semantic edges are not filtered
by this native guard. Stream segmentation can free element degree slots for v5
unassigned fallback, but that fallback still requires an unassigned endpoint
and preserves degree-one acyclicity. Candidate orders are computed before the
fallback layer and remain unchanged.

Diagnostics include `provider_local_stream_split_count`, backward/gap
discontinuity counts, and
`fine_relation_provider_nonlocal_suppressed_count`. Fit/calibration/test
provider relation F1 changes from
`0.97433893/0.96754386/0.97016660` to
`0.97688390/0.97363796/0.97527740`. Test precision/recall is
`0.97966367/0.97093023`; the flat test F1 is `0.96606248`. The oracle test
control remains exactly `0.95571096/0.93055556` line/region F1 with no
provider-only action. The 14-page calibration result still trails flat
`0.97694650`, so the gate remains review-only.

A same-input v5/v6 replay on real complex pages finds no action on Attention
PP or PUMA PP. BYD PP suppresses one nonlocal relation transition. JD/Docling
splits 8 provider chains and suppresses 6 nonlocal native candidates; local
edges change `89 -> 81`, relation transitions `11 -> 5`, and guarded
unassigned fallback transitions `10 -> 18`. These are unlabelled diagnostics,
not accuracy claims. All four base/candidate orders are identical and runtime
reorder remains false.

## Comp-HRDoc Relation Benchmark

`fetch-comphrdoc` pins the MIT Comp-HRDoc repository revision and verifies the
129,857,097-byte Git LFS annotation object by SHA-256. It reads only the unified
test annotation member, downloads the selected arXiv source document, and
renders each page directly to the official annotation dimensions. HRDoc image
assets are not redistributed.

Each annotated text block is expanded into textline nodes. Graphical annotations
are retained as typed figure/table nodes with stable pseudo text. Consecutive
textlines are linked locally; `reading_order_label = 1` links the current block
tail to the next official reading-order block, while `0` ends that local chain.
Floating label `2` stays outside body flow: figures precede their captions and
table captions precede tables, matching the official evaluator. The answer-free
structure file contains only typed layout/bbox anchors. Stable ids and
`ro_linkings` are written to the adjacent semantic sidecar, so model inference
cannot read the answers. Selection is a fixed document/page prefix and the
manifest records repository revision, archive/PDF hashes, URLs, and relation
counts. This is an oracle-layout order benchmark, not OCR detection scoring.

During relation inference, explicit figure/table roles can contribute a local,
geometry-gated caption edge. These edges remain review-only and carry
`relation_origin = structure-role-geometry`; they replace a learned outgoing
edge from the same source rather than creating an ambiguous branch. Stable
layout `block_id` groups caption lines so figures target the first line while
the last caption line precedes a table. The answer-free payload never exposes
Comp-HRDoc's official `reading_order_id` or relation labels.

Floating oracle construction groups annotations by official relation id only
inside the withheld semantic sidecar. It does not depend on whether a caption
appears before or after its graphical annotation; this matters because both
orders occur in the official test data.

`fetch-comphrdoc-relations` creates an annotation-only, cross-document floating
prefix. Typed layout anchors and semantic answers go to separate directories;
the manifest records that labels select targeted pages but are absent from
inference input. It neither fetches nor redistributes source images. Main body
successors skip floating groups, matching the official evaluator's separate
body and floating chains. `benchmark-comphrdoc-relations` loads one model and
runs the same pages with structure-role fusion disabled and enabled.

`train-floating-ranker` reads only the pinned Comp-HRDoc official train member.
Pages are split by document-id hash, preventing pages from one paper from
crossing fit/calibration. Each graphical block is paired with its official
caption as a positive and nearby same-page text blocks as hard negatives. The
27 shallow features cover graphical kind, normalized pair geometry, overlap,
relative direction, block line count, text length, and caption prefixes. The
model is a balanced histogram gradient booster. Its threshold is selected only
on the train-derived calibration partition. Every threshold candidate is decoded
with the same cardinality-first maximum-weight one-to-one assignment used at
inference. Edge margin is the smaller score gap against another caption for the
same graphical source and another graphical source for the same caption. This
captures both row and column competition instead of accepting a source-local
top score that loses the global assignment. Prediction emits isolated review-only
edges; answer-bearing inputs are rejected and runtime reorder remains disabled.
Older manifests without assignment-policy fields retain the original greedy
source-best-margin decoder so historical models remain reproducible.

The generated manifest records archive/member hashes, split policy, 4,102 fit
pages, 1,073 calibration pages, 49,763 examples, 5,638 positives,
`assignment_policy = global-cardinality-weight-v1`, and
`selection_margin_policy = min-row-column-score-gap-v1`. Threshold `0.36`
produces 1,373/1,489 correct/predicted against 1,446 calibration labels:
precision/recall/F1 `0.92209537/0.94951591/0.93560477`, up from the greedy
decoder's calibration F1 `0.91295681`. Training twice produces byte-identical
model files; manifests retain identical metrics and policy fields while
recording their respective output filenames.

Reliability is calibrated separately from the F1 operating point. A review gate
requires at least 25 calibration predictions and precision `>= 0.95`; confidence
`>= 0.85` and assignment margin `>= 0.72` give 995/1,047 correct, precision
`0.95033429`, recall `0.68810512`. The strict `0.97` target is now available at
confidence `>= 0.05` and margin `>= 0.84`, giving 871/897 correct, precision
`0.97101449`, recall `0.60235131`. The model also stores fit-only 1%/99% feature
envelopes. Every selected edge reports feature outlier count and ratio,
reliability tier, and strict-gate status. Clean calibration availability alone
does not enable runtime reorder; robustness profiles and real providers remain
separate promotion gates.

The relation corpus now passes body and floating candidates through a shared
degree-one acyclic selector. Candidate edges are confidence-sorted; only the
train-calibrated high-precision, zero-OOD floating subset is inserted first as
protected diagnostic evidence. The selector reports selected/protected edges,
outgoing and incoming conflicts, self-loops, and cycle rejections. This is a
generic path-cover primitive over stable hashable ids, but corpus use remains
benchmark-only.

The corpus scorer supports deterministic `clean`, `mild`, and `stress` source
perturbations. Mild uses 0.5% page-relative bbox jitter, 10% text-block
fragmentation, 3% graphical-type dropout, 1% element dropout, and 5% caption
prefix corruption. Stress uses 1.5%, 25%, 10%, 3%, and 15%, respectively.
Selection is SHA-256 keyed by profile, page uid, element/block id, and action;
the same corpus always receives identical noise. Reports separate retained
elements and resolvable labels from relation accuracy. These are controlled
sensitivity tests, not measured OCR error distributions.

`benchmark-provider-anchors` normalizes Docling documents, PaddleOCR-VL 1.6
`raw_results`, ROOR-style pages, and generic `pages/elements` providers into one
top-left anchor contract. Text/caption matching is many oracle lines to one
provider paragraph. Figure/table matching is globally one-to-one: a dependency-
free Hungarian solver first maximizes eligible match cardinality, then total
geometry score, with dummy columns for unmatched anchors. The same primitive
replaces input-order caption claiming in structure-role relation fusion. Within
a matched provider paragraph, oracle lines are ordered by geometry rather than
JSON list position, preventing answer-order leakage.

Reports include total and type-specific anchor recall, provider match rate,
serialized relation edges, explicit figure/table-caption edges, and optional
trained floating edges. `graphical_relation_audit` retains raw official scores
while comparing official graphical labels with an answer-free local-geometry
proposal. It reports exact agreement, conflicts, unresolved labels, and provider
agreement against that diagnostic proposal. The proposal is explicitly not
ground truth and never rewrites labels or `DocumentIR`. The suite command
aggregates the same counters over files selected by a rendered Comp-HRDoc
manifest.

Provider reports now include a relation-answer-free `provider_degradation`
section. The normalizer preserves a provider `group_id` independently from each
anchor id, so exact text lines can be regrouped into the same semantic block
while a real paragraph provider can remain one anchor. Geometry correspondence
ignores type only for diagnosis; runtime matching and relation scoring retain
their existing kind constraints. The diagnostic then separates the eight
LED-style failure families: missing and hallucination at anchor level; size,
split, merge, overlap, and duplicate at grouped-unit level; and type confusion
from geometry-matched anchors. Split/merge multiplicity, unmatched counts by
kind, normalized center/edge error, IoU/coverage/area ratio, NFC character
similarity, bag-of-token precision/recall/F1, and caption-prefix preservation
remain independently inspectable.

Two document-specific guards prevent misleading error counts. Provider overlap
is measured only when it is additional to oracle overlap, and high-IoU duplicate
boxes are kept in the duplicate category. A provider text/caption anchor that is
at least 90% contained in an oracle figure/table and at most 25% of the parent
area is reported as `nested_graphical_content`; full-size figure-to-text type
loss still remains a misclassification. This distinguishes useful chart/diagram
OCR from a genuine hallucinated page region.

Each real report is also compared with deterministic clean/mild/stress replays
of the same oracle. The comparison is an unweighted RMSE over 12 normalized
diagnostic rates and is explicitly descriptive: it is not fitted, does not read
relation labels, and cannot promote an edge or change `runtime_reorder`. The
suite command micro-aggregates raw counts and geometry/text records before
recomputing the signature. This decomposition follows the split/merge-oriented
document-structure evaluation tradition and the newer LED/COTe observation that
plain IoU or mAP hides structurally different layout failures:
https://www.haralick.org/journals/Liang_2001_Computer-Vision-and-Image-Understanding.pdf,
https://arxiv.org/abs/2603.17265, and https://arxiv.org/abs/2603.12718.

On the fixed 250-page graphical test corpus, global structure-role assignment
first raised graphical correct/predicted from `295/342` to `301/346`. A separate
train-only locality calibration then removes mandatory horizontal overlap while
tightening maximum horizontal center distance from `0.50` to `0.35` page width;
the vertical gap remains `0.12` page height. On 4,102 fit pages, correct/predicted
changes `5284/5658 -> 5291/5665` with no regressed page. On 1,073 held-out
calibration pages it changes `1348/1473 -> 1349/1474`, again with no regression.

With both changes frozen, the untouched 250-page test reaches graphical
`306/350/347`, precision/recall/F1 `0.87428571/0.88184438/0.87804878`. Overall
structure-role F1 is `0.85803621`, and diagnostic joint path-cover F1 is
`0.88575528`. Relative to global assignment alone, three pages change and all
three improve; relative to the original greedy baseline, graphical correct
increases by 11 with eight additional predictions. Real Docling/Paddle anchor
recall and raw relation metrics remain unchanged.

The learned floating decoder is also global. With its train-only operating point
frozen, raw graphical test F1 improves from `0.91322902` to `0.91761364`
(`323/357/347`), and joint path-cover F1 improves from `0.88774602` to
`0.88839440` (`8983/9758/10465`). The only raw page change is
`1507.01067_7`, improving from `2/3` to `4/4`. The clean strict subset is
`196/201` (precision `0.97512438`), or `169/173` with zero feature outliers
(precision `0.97687861`). All five strict raw errors touch graphical objects
where the independent local-geometry audit conflicts with the official label;
the report records this fact without relabeling the corpus.

Noise-aware selective calibration is now a second, conjunctive abstention layer.
Official train text blocks retain their exact text-line polygons, then reuse the
same deterministic clean/mild/stress perturbation code as the corpus benchmark.
Four document-hash folds train temporary pair estimators and score only held-out
fit documents, producing 15,413 correctness records without in-sample pair
predictions. The final forecaster is a standardized L2 logistic regression over
12 domain-general features: pair score, source/target competitor scores and
margins, exclusion-based global assignment cardinality/score gap, feature OOD
ratio, and page/assignment size. It deliberately excludes raw coordinates,
caption text features, profile identity, and answer relations. An earlier
selector-only nonlinear prototype generalized poorly under noise and was not
retained.

The forecaster cannot bypass the original gates. Noise-aware review requires the
base `confidence >= 0.85` and `margin >= 0.72` gate plus correctness score
`>= 0.29`; noise-aware strict requires the base `confidence >= 0.05` and
`margin >= 0.84` gate plus score `>= 0.44`. Thresholds maximize minimum-profile
risk coverage on the train-derived calibration views while requiring every
profile to meet its precision floor. Strict calibration precision/recall is
`0.97272727/0.59197787` clean, `0.97522816/0.51728907` mild, and
`0.97164461/0.35546335` stress. Four-fold split counts, feature names, all
per-profile metrics, and gate provenance are serialized in the manifest.

On the untouched 250-page test, noise-aware strict changes clean from `196/201`
to `192/195` (precision `0.98461538`), mild from `169/175` to `163/167`
(`0.97604790`), and stress from `115/123` to `109/116` (`0.93965517`). Clean
and mild errors all touch audited conflict graphicals; stress still has six
errors outside that set. Noise-aware review keeps 235 clean and 198 mild correct
edges while removing one error from each, and keeps all 133 stress correct edges
while removing two errors. Its protected path cover preserves clean/stress F1
and raises mild F1 `0.85764341 -> 0.85784363`. The stress precision gap remains
real, so provider outputs stay review-only, never write order into `DocumentIR`,
and `runtime_reorder` remains false. The next step needs real provider-noise
labels or a stronger domain-shift model, not another synthetic/test threshold.

Assignment-confidence analysis motivates score-gap and assignment-stability
features beyond pair probability: https://doi.org/10.1016/j.patrec.2015.07.010

Calibrated structured prediction motivates a separate correctness forecaster
using margin and pseudo-margin features: https://proceedings.neurips.cc/paper/2015/file/52d2752b150f9c35ccb6869cbf074e48-Paper.pdf

Noise-aware selective calibration motivates this train-only rejection layer but
does not provide distribution-free test robustness:
https://arxiv.org/abs/2208.12084

Sparse graph segmentation models bidimensional text-line and region relations,
then applies cluster-and-sort post-processing. It is a candidate architecture
for a train-only floating-pair gate: https://arxiv.org/abs/2305.02577

Subpixel positive OCR boxes now use floor/ceil crop boundaries rather than
rounding both sides to the same coordinate. This keeps a one-pixel crop instead
of aborting image-source benchmarks with `cannot write empty image`.

## Train-Only Provider Calibration and Fast Paddle Layout

`fetch_comphrdoc_provider_calibration_corpus()` reconstructs a small real-image
provider corpus from the pinned Comp-HRDoc train annotation archive and original
arXiv PDFs. It uses annotation geometry and categories to stratify pages as
`multicolumn` or `graphical-multicolumn`, but the selector is not allowed to read
relation labels. Document ids are SHA-256 partitioned before pages are chosen;
the current fixed split has three fit documents and one calibration document.
The manifest records the annotation revision/hash, source PDF URL/hash,
partition, layout stratum, selection fields, and source-license policy.

The generated directories enforce the data boundary:

- `images/` is the only provider input.
- `structure/` contains answer-free anchors with relations removed and is used
  only for provider-to-oracle matching.
- `semantic/` contains evaluation relations and is opened only by the scorer.
- `sources/` is a local reconstruction cache; Scriptorium does not redistribute
  the arXiv PDFs.

`benchmark_provider_anchor_suite()` now preserves `sample_id`, `partition`, and
`layout_stratum` per case and emits micro summaries for each partition, including
missing provider cases. Nested graphical diagnostics use oracle group ids rather
than assuming a line id is also a grouped block id. This prevents Docling's
nested chart OCR from colliding with block/line namespaces during aggregate
overlap analysis.

`PaddleLayoutAdapter` runs Paddle's `LayoutDetection` with
`PP-DocLayoutV3`, without OCR or VLM recognition. Every valid provider box is
normalized to a stable page-local id, bbox, label, confidence, raw index, and
optional numeric order. Consecutive numeric-order boxes become serialized
successor edges. The payload explicitly declares:

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

The `run-paddle-layout` command accepts PDF and first-class image sources,
preserves sampled source page indices, and records model options, package
versions, input sizes, and input SHA-256 values. A local model directory can be
used without changing the output contract. The capability declaration is also
consumed by degradation scoring: layout-only output keeps text records available
for inspection, but marks text fidelity not applicable and excludes character,
token, and caption-loss features from profile distance. The remaining nine
layout diagnostics continue to be scored.

PaddleOCR-VL and PP-Structure payloads now carry the same class of reproducibility
provenance. Pipeline, adapter, and prediction options are serialized; installed
`paddleocr`, `paddlex`, and `paddlepaddle` versions and per-input hashes are
recorded; keys containing credential markers are redacted. PaddleOCR-VL defaults
to synchronous local prediction and exposes `--queued` and `--max-new-tokens`
explicitly, so queue scheduling and generation limits are no longer hidden run
conditions.

For rendered upright PDF pages, PP-Structure now disables document orientation,
document unwarping, and text-line orientation by default, alongside the existing
optional table/formula/region stages. Rotated, curved, photographed, table-heavy,
or formula-heavy inputs can re-enable only the required stages with explicit CLI
flags. CPU compatibility environment and options remain part of provenance.

On the answer-separated eight-page corpus, PP-DocLayoutV3 relation F1 is
`0.89882353` on fit, `0.87248322` on calibration, and `0.89198606` overall.
Docling on the same pages reaches `0.88119954`, `0.84415584`, and `0.87148936`.
PaddleOCR-VL and lightweight PP-Structure were run only on one graphical page
per partition and reach overall F1 `0.89510490` and `0.88732394`; those two-page
results are not full-corpus comparisons. All providers remain review-only.

The architecture matches the evidence from RT-DocLayout/PP-DocLayoutV3: layout
and reading-order prediction can be useful without paying for full recognition
(https://arxiv.org/html/2606.23344). The CLM+NSP path-cover approach in
https://arxiv.org/html/2607.01018 also supports local successor scoring followed
by max-regret path cover. Scriptorium already implements degree-one, acyclic,
max-regret path-cover selection. A semantic next-sentence scorer is deferred
until provider text is cleaner and a larger held-out calibration set can justify
its cost; the current eight train-only pages cannot justify runtime promotion.

`run_paddle_layout_corpus()` loads one `PP-DocLayoutV3` predictor and walks the
manifest rather than paying model startup for every page. Each output records
the corpus/sample/input SHA-256 provenance. Existing outputs are skipped only
when their corpus manifest hash matches; `--refresh` is explicit, and a stale
output from another corpus fails instead of being silently reused.

Provider benchmark schema v6 separates serialized edges into
`within_anchor`, `between_anchors`, and `direct_between_anchors`. A Provider
paragraph may own many oracle lines, so within-anchor edges are geometry-local
segmentation evidence, not evidence that the model ordered two blocks. Provider
confidence is retained during normalization and copied into anchor assignment
provenance. For each direct block transition, the evaluator records exact
direct-successor provenance from visual Y/X, box-flow, non-trivial recursive
XY-cut tree edges, and the actual edges selected by relation-graph max-regret
path cover. It emits the full support/confidence curve with a 95% Wilson
precision lower bound. Semantic labels score each already-selected point only;
a label-invariance test verifies that changing `ro_linkings` cannot change
eligible edges.

Comp-HRDoc `ro_linkings` are partial labels. Transition review v3 therefore
separates threshold-selected edges into `eligible`, `scorable`, and `unscored`.
Precision includes an edge only when both endpoints occur in the relation
endpoint universe; an unlabelled edge is no longer assumed false. Gates also
require a minimum `scorable_fraction`, preventing apparently high precision from
hiding mostly unscored output. Suite v8 aggregates the same counters by case,
partition, layout, and position.

`freeze_stratified_provider_transition_gate()` now writes gate v4. The artifact
separates `candidate_orders` from `support_candidate_names`; by default only
visual Y/X, box-flow, and selected relation-graph edges contribute support.
Recursive XY-cut stays available for explicit A/B and audit, but does not count
as an independent default vote. The default still predeclares
`minimum_native_support = 2`, so single-candidate edges abstain.
Layout-by-position-by-confidence rules are selected on fit labels only. A
SHA-256 document ordering then creates five out-of-fold splits: each validation
fold is scored only with rules selected from the other documents. Aggregate,
every fold, and every active bucket must separately satisfy precision, Wilson,
minimum scorable count, and `scorable_fraction`. Calibration cannot modify any
rule; it can only accept or reject. `cross_validation_folds = 0` exists only for
legacy diagnostics.

The artifact pins the source report and corpus SHA-256 values, observable and
support candidate sets, document folds, criteria, and criterion results, and
always keeps `runtime_reorder: false`. Train and independent evaluation both
recompute support from `native_supporting_candidates`. Gate v4 fails closed when
filtering is requested without that provenance; legacy v2/v3 artifacts retain
their original counts. `benchmark_provider_anchor_suite()` can load the gate on
another corpus only after both document CV and calibration pass; no test-time
threshold search occurs.

`fetch_comphrdoc_provider_test_corpus()` applies a separate deterministic
document/page hash namespace to official test annotations. Calibration and test
reconstruction share one materializer, while their manifests keep the official
split, selection fields, source revision, and answer boundary explicit. Both
fetchers accept `--annotation-archive`; the local 130 MB archive still has to
match `530f482b75523a80fe1b0a7480fd8273c44f9239e0189650a4841c0aae61d03d`.

On the same already opened 32-page test window, the partial-label-aware v1 audit
is `256/268`, with 16 `unscored` transitions. The old `209/219` value was
partial-label-unaware and is withdrawn as a current metric. Start is `72/78`
with Wilson `0.84216770`, middle is `91/92` with Wilson `0.94097214`, and end is
`93/98` with Wilson `0.88607548`, so the audit can only reject promotion.

On the enlarged 64-page train-only suite, gate v4's default three-channel set
reproduces `192/195` across 25
fit-document OOF predictions, but folds 2 and 3 each have Wilson `0.83805895`.
The `graphical-multicolumn/middle` bucket has only 18 scorable predictions;
`multicolumn/start` is only `8/9` with `0.75` scorable fraction. Seven
calibration documents retain only `21/21`, with Wilson `0.84536098` and fewer
than 30 predictions. Explicitly adding recursive XY-cut changes OOF to
`173/176` and calibration to `23/24`, Wilson `0.79758194`; the added error is a
visual-Y/X plus XY-cut edge. A visual-Y/X plus box-flow support-2 control has no
qualified fit bucket. The gate is therefore
`document-cross-validation-rejected-review-only`, and no new test window was
opened.

`chunkr_benchmark.py` adds a second, cross-domain development surface for
block-level order. Acquisition pins the upstream Hugging Face revision and the
1.9 MB COCO annotation SHA-256; source images are not required for geometry
candidate scoring. Validation requires globally unique annotations, one or more
valid in-page boxes per image, known categories, and contiguous ascending ids
inside each image. Those ids define the published order but are removed from
candidate input ordering: category/bbox fingerprints provide deterministic
answer-free input permutations instead.

The report separates complete serialized orders from actual evidence edges.
Order metrics include exact page match, position accuracy, pairwise accuracy /
Kendall tau, and adjacent successor accuracy for all, non-trivial, and 10+
element pages. Edge metrics score visual and box-flow adjacency, only
non-trivial recursive XY-cut tree edges, and only selected relation-graph
path-cover edges. Stable-three-channel and all-four-channel support curves are
reported independently, with per-domain and per-page diagnostics. The artifact
is always `development-benchmark-only` and `runtime_reorder: false`.

The first full run found a duplicate-assignment defect before producing scores:
some right-side grid cells were owned by both a grid-island token and the
sidebar secondary flow. Mixed island ordering now excludes island-owned items
from artifact/sidebar/footnote classification, so every input element receives
exactly one assignment. A fixed 16-box regression covers this conflict. The next
learned candidate can use Chunkr only for development/cross-validation; runtime
or promotion evidence still requires answer-separated external corpora.

`chunkr_order_ranker.py` implements that development candidate without entering
the converter. Training creates 223,634 directed pairs from 9,267 blocks. Its 68
features contain normalized source/target geometry, overlap and direction,
page element count, rank/direction/adjacency from five answer-free geometry
candidates, and separate source/target role one-hot vectors. A
`HistGradientBoostingClassifier` scores both directions of every unordered pair;
the two probabilities are antisymmetrized, then a Borda score produces one
permutation with visual Y/X only as a deterministic tie-break. Uniform pair
weighting is retained: the tested focal variant cost roughly `333 s` instead of
`43 s`, did not recover selected-order successor accuracy, and is not the
default.

Cross-validation is category/complexity-stratified and SHA-256 assigned at page
scope. Feature construction and fold assignment do not read annotation ids;
tests rebuild the same anchors with reversed answer ids and require identical
features/folds but changed labels. Because Chunkr has no document identifiers,
the report explicitly says page OOF and `test_split_claimed: false`. Every fold
contains learned and baseline metrics rather than reporting only the aggregate.

Training writes a model, adjacent manifest, and adjacent OOF report. Loading is
fail-closed: schema, review-only status, isolation policy, model filename/hash,
OOF filename/hash, feature/role contracts, and report-to-model hashes must all
match before joblib deserialization. Joblib artifacts remain local-trust only.
Prediction recursively rejects order-bearing fields including successor,
precedence, stream, explicit order, and semantic-order values; ids and
role/bbox fingerprints must be unique, and one page is capped at 256 elements.
Output is always review-only successor evidence with `runtime_reorder: false`.

The feature-level 1st/99th percentile envelope did not detect the ROOR transfer
failure, despite confident wrong predictions. A separate page profile therefore
records element count, bbox width/height/area quantiles, aspect, role entropy and
ratios, and disagreement between selected/XY/relation candidates. Diagnostics
include every profile value and each violated lower/upper bound. The ROOR replay
also confines manifest paths to the corpus and executes in two phases: it
predicts every structure page first, then opens semantic sidecars for scoring.
Tests assert the event order and reject path traversal.

Chunkr OOF improves exact/pairwise from selected `0.61255116/0.87452713` to
`0.70259209/0.93686112`, but successor falls `0.75041012 -> 0.74349660`. On 49
ROOR line-level pages, direct recall is only `0.19142420`, below selected
`0.46592649`, and precedence is `0.77067381` versus `0.83192956`; all pages are
outside the coarse-block page profile. The OOD rule was added after observing
this window and is therefore diagnostic, not an independently calibrated gate.
The architectural consequence is to keep coarse-block and text-line ordering as
separate levels: infer/accept block membership first, order blocks second, and
preserve or independently predict line successors only within each block.

## Hierarchical Block/Line Proposals

`hierarchical_order_adapter.py` connects the hierarchy prototype to real
`DocumentIR` and normalized PP-Structure, PaddleOCR-VL, OpenDataLoader, Surya,
or Docling structure JSON:

```bash
scriptorium build-hierarchical-order path/to/document.ir.json \
  --structure-json path/to/provider.json \
  --page-index 0 \
  --output outputs/hierarchical-order.proposal.json
```

The adapter declares `element_granularity: fine` and
`region_granularity: coarse`. Its fine layer contains visible, non-empty text
elements from either PDF or image-source IR; source visual layers and empty
shape/image anchors are excluded. The coarse filter accepts provider block
lists, layout-detector blocks, and Docling body/furniture leaves. Paddle OCR
lines, table cells, generic `document` references, and reading-order sidecars
are rejected with reason counts. Provider sequence values are stripped and
provider relation arrays are ignored before the hierarchy proposal is built.
Changing `block_order` or review successor edges therefore cannot change the
adapted element/region geometry.

Structure deduplication is granularity-aware. A coarse `parsing_res_list` block
and a fine OCR line with the same text and geometry remain separate evidence;
only duplicates inside the same granularity class compete. This preserves the
precise OCR anchor for structure application and the parent block for hierarchy
membership.

Membership is fail-closed and ordered by evidence strength:

1. Compatible explicit provider parent references require at least `0.5`
   element coverage.
2. Exact or contained alphanumeric text may repair local coordinate drift when
   axis alignment, bounded line-relative gap, score `>= 0.74`, and competitor
   margin `>= 0.08` all pass. It also overrides a geometry parent whose text
   conflicts with the element.
3. Remaining elements use geometry coverage `>= 0.8` and runner-up margin
   `>= 0.1`; ties stay unassigned.

Within-region order always preserves the answer-free selected local line order.
The default cross-region layer no longer forces selected coarse geometry into
one adjacency chain. One fine relation-graph pass returns both its serialized
diagnostic order and its actually selected edge diagnostics. Coarse regions are
listed by the completion rank of their members only for inspection. Every
selected edge crossing two assigned regions is retained as evidence; only a
source-stream-tail to target-stream-head edge can become a review transition.
Region-level degree-one checks and cycle suppression keep those transitions a
partial DAG. Non-boundary and suppressed edges remain in
`cross_region_relation_evidence` with an explicit reason.

The optional Chunkr block model remains an explicit same-granularity A/B path.
OOD model pages still suppress all model transitions. Candidate expansion is
allowed only when the selected relation edges happen to form the exact complete
coarse chain and every fine element is assigned. All local edges and
transitions remain review-only in an unaccepted
`ScriptoriumReadingOrderSidecar` proposal; `total_order_asserted` and
`runtime_reorder` stay `false`.

The first real, unlabelled geometry-only versus text-plus-geometry audit is:

| Page/provider | Fine elements | Coarse regions | Assigned | Unassigned | Non-empty regions | Eligible cross-region transitions |
|---|---:|---:|---:|---:|---:|---:|
| Attention p. 1 / PP-Structure | 56 | 9 | 47 -> 52 | 9 -> 4 | 6 -> 9 | 1 -> 6 |
| BYD annual report p. 136 / PP-Structure | 34 | 17 | 29 -> 33 | 5 -> 1 | 11 -> 15 | 7 -> 13 |
| JD image source / Docling | 64 | 93 | 49 -> 53 | 15 -> 11 | 31 -> 37 | 16 -> 20 |

These numbers measure membership coverage, not semantic accuracy. The JD
Paddle replay resolves `64/64` text anchors through explicit ids, but 54 empty
region boundaries still suppress a page permutation. Attention with
OpenDataLoader resolves `56/56` and forms a complete 21-transition review chain;
that is also not promotion evidence without independent labels. The next gate
must score within-region successors and cross-region transitions separately on
an unopened annual-report, portal, or line-level family.

The architecture follows PAGE's region/line nesting and ordered/unordered group
model, and the Detect-Order-Construct coarse-to-fine separation. It also follows
relation-based reading-order work that represents complex layouts as local DAG
relations rather than forcing one global permutation:

- PAGE `TextRegion` / `TextLine`: https://ocr-d.de/en/gt-guidelines/pagexml/pagecontent_xsd_Complex_Type_pc_TextRegionType.html
- OCR-D PAGE reading order: https://ocr-d.de/en/gt-guidelines/trans/lyLeserichtung.html
- Detect-Order-Construct: https://arxiv.org/abs/2401.11874
- DLAFormer coarse-to-fine layout analysis: https://arxiv.org/abs/2405.11757
- Ordering relations for visually rich documents: https://aclanthology.org/2024.emnlp-main.540/
- PRImA correspondence-aware reading-order evaluation: https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf
- Sparse graph segmentation and cluster-and-sort reading order: https://arxiv.org/abs/2305.02577
- XY-Cut++ multi-granularity/cross-modal ordering: https://arxiv.org/abs/2504.10258
- GraphDoc relation graph (MIT; release TODOs remain): https://github.com/yufanchen96/GraphDoc

### Hierarchy Relation-DAG Contract

`infer_relation_graph_order_evidence()` exposes one immutable
`RelationGraphOrderEvidence` containing the serialized candidate indices and
selection-time diagnostics for every edge that entered the max-regret path
cover. This avoids rebuilding the quadratic candidate graph when hierarchy
needs both views. Existing order-only and selected-edge APIs delegate to the
same primitive.

`hierarchical_order.py` maps each selected fine edge through predicted
membership. Cross-region candidates carry score, alternatives, margins,
selection regret, selection step, and tie status. Boundary candidates are
ranked by untied status, regret, score, and stable ids. The selector enforces a
single outgoing/incoming edge per region and rejects any edge that would close
a region cycle. Selected records enter `review_transitions`; every rejected or
non-boundary record remains in `cross_region_relation_evidence` with
`not-local-stream-boundary`, degree-conflict, or cycle provenance. This makes
abstention inspectable instead of silently serializing path heads.

The answer-separated Comp-HRDoc hierarchy corpus has independent hashed
`inputs/` and `labels/`. Materialization reads every structure page before any
semantic sidecar. Evaluation predicts every input before resolving any label
path. Tests mutate relation labels without changing inputs, reverse source
input order, track the two read phases, reject path traversal and hash
tampering, and force a three-region cycle to verify suppression.

One non-iterative membership refinement handles `ambiguous-region-overlap`
only. The untied relation predecessor/successor and the selected-order
predecessor/successor must all agree on one region, and that region must remain
inside the original geometry tie. Repaired members never propagate further
repairs. This `relation-base-continuity-parent` rule resolves 8 memberships (5
fit and 3 calibration), all correctly, and triggers zero times on the four real
provider replays.

The same pass has a separate boundary branch. Relation and selected-order
neighbors must agree positionally on two distinct regions, forming the same
`A -> element -> B` pattern. The compact element text must contain at least
`MIN_EXACT_TEXT_PARENT_CHARACTERS` characters, exactly one geometry-tied region
must contain it, and that region must be `A` or `B`. The method is
`relation-base-boundary-text-parent`; its evidence records the relation/base
split and unique tied-region text containment. It resolves 13 more memberships
(6 fit and 7 calibration), all correctly. The branch reads the original
membership map, so neither boundary nor interior repairs can propagate.

Before semantic arbitration and the v4 object-branch contract, the 64-page
train-only v3 control reached membership `5244/5257 = 0.99752711` with zero
wrong assignments and 13 unassigned elements. Within-region F1 was
`0.99297033`; fit/calibration values were `0.99191794/0.99642675`. Line
cross-region F1 was `0.93473962`, and region-transition F1 was `0.90607029`.
Calibration line/region F1 was `0.92260062/0.89759036`, with line still below
the flat `0.92879257`. The v3 report aggregated 972 cross-region evidence edges,
905 boundary candidates, 67 non-boundary records, 9 tied edges, 3 cycle
suppressions, and 902 emitted transitions. Both refinement methods triggered
zero times on the four current real-provider replays. These values remain the
explicit pre-semantic/pre-object-branch control; the current v4 results are
recorded in the semantic hierarchy section above.

Two rejected controls define the boundary of this design. Cutting every local
stream around non-boundary relation edges improves fit line F1 to `0.94176373`
but lowers within F1 to `0.98873592`, removes 25 correct local edges, and creates
one cycle. Adding non-boundary relation edges only when flat order agrees raises
partial line F1 to `0.93891213`, but region F1 falls to `0.89402390`; partial
line labels leave 22 of 29 additions unscored. Non-boundary records therefore
remain evidence, not translation handoffs.

For translation, each accepted coarse membership still defines a bounded local
stream. The partial DAG can later order handoffs between those streams without
requiring one page permutation. Non-boundary relation evidence cannot drive
replacement or reflow; it remains review material until a structure provider,
semantic model, or human confirms the boundary. This preserves stable
translation fitting units while avoiding false global order.
