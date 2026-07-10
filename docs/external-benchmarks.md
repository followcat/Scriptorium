<p align="center">
  <a href="../README.md"><img alt="Home" src="https://img.shields.io/badge/Home-README-2b6cb0"></a>
  <a href="external-benchmarks.zh-CN.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E9%98%85%E8%AF%BB-blue"></a>
  <img alt="English" src="https://img.shields.io/badge/English-current-2f855a">
</p>

# External Benchmark Samples

These samples are intentionally kept out of git because `data/` and `outputs/` are ignored. The table records how to recreate the local sources and which benchmark reports currently hold the measured results.

## Current Samples

| Sample | Local source | Source | Purpose |
|---|---|---|---|
| PUMA 2024 Annual Report | `data/external/puma-2024-annual-report.pdf` | `https://annualreports.com/Click/27465` | Public listed-company annual report with dense image, text, table, and shape layout. |
| BYD 2024 Annual Report | `data/external/byd-2024-annual-report.pdf` | `https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF` | China A-share annual report, 290 Chinese pages, many financial tables and dense vector rules. |
| JD homepage screenshot PNG | `outputs/external/jd-home/full-page.png` | `https://www.jd.com/` redirects to `https://hk.jd.com/` in this environment | First-class image source path for the same ecommerce homepage screenshot. |
| JD homepage screenshot PDF | `outputs/external/jd-home/input.pdf` | `https://www.jd.com/` redirects to `https://hk.jd.com/` in this environment | Full-page ecommerce homepage screenshot converted into an image-only PDF. |
| Hacker News print PDF | `outputs/external/web-hn/input.pdf` | `https://news.ycombinator.com/` | Real web-to-PDF portal/list layout with tracked semantic sidecar labels. |

## Recreate Inputs

Download the PUMA annual report:

```bash
curl --fail --location https://annualreports.com/Click/27465 \
  --output data/external/puma-2024-annual-report.pdf
```

Download the BYD A-share annual report from CNInfo:

```bash
curl --fail --location https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF \
  --output data/external/byd-2024-annual-report.pdf
```

Current local checksum:

```text
SHA256 e9c2d7fdd088e151ccb6c8ad3d95587b2b014b10f2c9731508d23ce07fde4de3
```

Capture the JD homepage as a full-page screenshot and wrap it as a PDF:

```bash
./.venv/bin/python -u - <<'PY'
from pathlib import Path
from PIL import Image
import fitz
import shutil
from playwright.sync_api import sync_playwright

out = Path("outputs/external/jd-home")
out.mkdir(parents=True, exist_ok=True)
screenshot = out / "full-page.png"
pdf = out / "input.pdf"
executable = shutil.which("google-chrome") or shutil.which("chromium")
launch_kwargs = {"headless": True, "args": ["--no-proxy-server", "--disable-dev-shm-usage", "--disable-gpu"]}
if executable:
    launch_kwargs["executable_path"] = executable

with sync_playwright() as p:
    browser = p.chromium.launch(**launch_kwargs)
    try:
        page = browser.new_page(viewport={"width": 1440, "height": 1200}, device_scale_factor=1)
        page.goto("https://www.jd.com/", wait_until="commit", timeout=30000)
        page.wait_for_timeout(6000)
        page.evaluate("window.stop()")
        page.screenshot(path=str(screenshot), full_page=True, animations="disabled", timeout=30000)
    finally:
        browser.close()

with Image.open(screenshot) as im:
    width, height = im.size
    page_width_pt = width * 72 / 96
    page_height_pt = height * 72 / 96

doc = fitz.open()
try:
    page = doc.new_page(width=page_width_pt, height=page_height_pt)
    page.insert_image(page.rect, filename=str(screenshot))
    doc.save(pdf)
finally:
    doc.close()
PY
```

Chrome may need to run outside the restricted sandbox in local automation environments.

Capture the Hacker News portal/list page through Playwright's print path:

```bash
./.venv/bin/scriptorium capture-pdf \
  https://news.ycombinator.com/ \
  --pdf outputs/external/web-hn/input.pdf \
  --mode print
```

## Benchmark Commands

PUMA annual report, first 12 pages:

```bash
./.venv/bin/scriptorium benchmark data/external/puma-2024-annual-report.pdf \
  --out-dir outputs/external/puma-2024-annual-report-relation-graph-diagnostics-v1 \
  --dpi 144 \
  --max-pages 12 \
  --html-mode auto \
  --fidelity-background auto
```

JD screenshot PDF:

```bash
./.venv/bin/scriptorium benchmark outputs/external/jd-home/input.pdf \
  --out-dir outputs/external/jd-home-relation-graph-diagnostics-v1 \
  --dpi 144 \
  --html-mode auto \
  --fidelity-background auto
```

JD screenshot PNG as a first-class image source:

```bash
./.venv/bin/scriptorium benchmark outputs/external/jd-home/full-page.png \
  --out-dir outputs/external/jd-home-image-source-benchmark-v1 \
  --dpi 144 \
  --input-kind image \
  --image-dpi 96 \
  --html-mode auto \
  --fidelity-background auto
```

BYD A-share annual report, first 40 pages:

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-source-smoke-v1 \
  --dpi 144 \
  --max-pages 40 \
  --html-mode auto \
  --fidelity-background auto
```

BYD financial statement section, explicit source pages 136-160:

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-financial-pages-v1 \
  --dpi 144 \
  --page-ranges 136-160 \
  --html-mode auto \
  --fidelity-background auto
```

BYD translated re-rendering stress, first 40 pages:

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-translation-stress-v1 \
  --dpi 144 \
  --max-pages 40 \
  --html-mode fidelity \
  --fidelity-background auto \
  --translation-stress pseudo-expand
```

Use `--page-ranges` when the important section is not at the beginning of a long source. Page ranges are 1-based source page numbers, cannot be combined with `--max-pages`, and preserve original `page_index` values for semantic sidecars and structure JSON alignment.

Translation re-rendering stress run for annual-report, ecommerce screenshot, and web portal samples:

```bash
./.venv/bin/scriptorium benchmark \
  data/external/puma-2024-annual-report.pdf \
  outputs/external/jd-home/input.pdf \
  outputs/external/web-hn/input.pdf \
  --out-dir outputs/external/translation-stress-v3 \
  --dpi 144 \
  --max-pages 12 \
  --html-mode fidelity \
  --fidelity-background auto \
  --translation-stress pseudo-expand
```

## Current Results

| Sample | Pages Scored | Selected Path | Visual Similarity | Max Diff | Mean Diff | Elements | Editable | OCR Pages | OCR Text | Mixed Table Flow | Table Row-Major | Spatial Graph | Box-Flow Elements | Captions | Box-Flow Pairwise | Box-Flow Successor | Relation Pairwise | Relation Successor | Page Artifacts | Footnotes | Sidebars | RO Confidence | Low-Conf RO | Reading Risk |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 Annual Report | 12 | `fidelity/raster` | 0.9795117 | 0.0204883 | 0.01089482 | 815 | 521 | 0 | 0 | 238 | 0 | 0 | 0 | 0 | 0.17460108 | 199/509 | 0.16306211 | 166/509 | 20 | 2 | 36 right | 0.82476488 | 0 | `0.35 / high` |
| BYD 2024 Annual Report | 40 | `fidelity/raster` | 0.89780001 | 0.10219999 | 0.05377595 | 9531 | 3015 | 0 | 0 | 1052 | 0 | 0 | 0 | 0 | 0.32890849 | 2496/2975 | 0.09694495 | 981/2975 | 0 | 33 | 97 right | 0.89081217 | 0 | `0.35 / high` |
| JD homepage screenshot PDF | 1 | `fidelity/raster` | 0.99576887 | 0.00423113 | 0.00423113 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 127/133 | 0.21624958 | 117/133 | 0 | 0 | 0 | 0.83 | 0 | `0.35 / high` |
| JD homepage screenshot PNG | 1 | `structured/image-source` | 0.99236799 | 0.00763201 | 0.00763201 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0 | 0.43833464 | 128/133 | 0.21894288 | 120/133 | 8 | 0 | 0 | 0.77151567 | 0 | `0.35 / high` |

The direct JD PNG run validates the first-class image-source path. It produces the same semantic inventory as the image-only PDF compatibility path: 135 total elements, 134 editable OCR text anchors, 35 grid-island elements, and the same high reading-risk diagnosis caused by missing semantic sidecar evidence. The visual score is slightly lower because the comparison path renders the source image layer directly instead of comparing against image-only PDF rasterization, but page count and page dimensions still match. Current benchmark reports also expose `semantic_layer_driver`, so image cases can distinguish structure JSON, OCR JSON, OCR fallback, and visual-only runs.

BYD is the current complex Chinese annual-report stressor. It is 290 pages and 10,092,140 bytes locally. A quick PyMuPDF profile shows that its first 20 pages expose 497 text blocks and 1088 drawing objects, compared with PUMA's 257 text blocks and 375 drawing objects over the same page count. Across the full PDF, BYD has 50,724 drawing objects and 101 pages with at least 30 text blocks, compared with PUMA's 37,081 drawing objects and 65 such pages. It therefore adds a harder Chinese table/vector/form-report dimension that PUMA does not cover well.

## PP-StructureV3 A/B

Real PP-StructureV3 `save_to_json` output is now exercised through `benchmark-structure-ab`. The CPU runs use layout and text recognition only; formula, table, chart, and seal modules are disabled for these reading-order samples. Visual output is unchanged because structure evidence changes the semantic layer, not the source visual layer.

| Sample page | Structure regions / matched elements | Visual similarity | Labelled semantic result | Structure-order result |
|---|---:|---:|---|---|
| Attention p. 1 | 78 / 56 | `0.95231377` | Pair `1.0`, successor `1.0` | No selected reorder; the prior sparse-order regression is gone. |
| Transformer-XL pp. 1-3 | 458 / 321 | `0.93853503` | Pair `1.0`, successor `1.0` | Native column flow is retained; stream needs decrease by 1 and consensus successor disagreement by 26. |
| JD homepage p. 1 | 160 / 128 | `0.99536129` | No semantic sidecar | Successor-consensus disagreement improves by 62; native grid streams remain protected and stream `needs-structure-evidence` stays at baseline. |
| PUMA p. 5 | 42 / 25 | `0.95767110` | No semantic sidecar | The implicit-image guard leaves selected order and review diagnostics at the native baseline. |
| BYD financial report p. 136 | 66 / 34 | `0.96518593` | No semantic sidecar | PP table recognition maps 10 cells into one row-major `table-island`; page needs-structure-evidence decreases by 1. |

This is evidence that block order is useful but incomplete: model block order can refine candidate successors, while translation needs explicit local `successor_edges` and `reading_streams` to resolve portal cards, image captions, sidebars, and repeated text safely.

## PaddleOCR-VL 1.6 A/B

The first real PaddleOCR-VL replay uses PUMA annual-report p. 5. The model ran
on a 96-DPI `794 x 1123` page image, then the exact raw JSON was replayed in
`benchmark-structure-ab` at both 96 and 144 DPI. This is a coordinate-contract
test: the model result records the input canvas, while the benchmark is free to
render the PDF at another DPI.

| Sample | Model regions / matched elements | Benchmark DPI | Visual similarity | Reading-risk delta | Selected-order / candidate delta | Model relation evidence |
|---|---:|---:|---:|---:|---|---|
| PUMA p. 5 | 11 / 24 | 96 and 144 | `0.95767110` | `0.0` | No selected reorder; stream needs and all successor-disagreement deltas are `0` | None: no relation or stream edges |

The corrected structure branch produces three local sidecar streams with 13
strict edges, 9 review edges, and 2 review transitions. These are proposal
evidence, not an accuracy claim: PUMA still has no semantic order sidecar. The
important result is DPI-invariant region alignment; without it, a 96-DPI model
bbox replayed against a 144-DPI page can falsely attach a paragraph to another
text region and manufacture an order regression.

### Explicit model-block translation streams

Some layout providers expose ordered paragraph blocks but no relation edges or
`reading_streams`. The block-stream bridge uses those boundaries only after
native matching: every member must be body text in one selected flow segment
and column, with coverage at least `0.5`. It then keeps native local order and
creates a primary `external-block-body-*` stream; it never turns block order
into a page-wide permutation or crosses a stronger table/grid/caption/artifact
stream.

| Sample | Derived block streams / members | Selected-order result | Sidecar proposal result | Translation-stress result |
|---|---:|---|---|---|
| PUMA annual report p. 5 | 4 / 17 | No reorder; visual `0.95767110` at the coordinate A/B baseline | `7 / 12 / 6 / 7` streams / strict / review / transitions | 23 replacements, 22 conflicts, 6 overflows; grouping improves attribution but not totals yet. |
| Transformer-XL p. 1 | 6 / 85 | Labelled successor accuracy remains `1.0` | `13 / 81 / 5 / 13` | Not used as a claim about translated visual fidelity. |
| JD homepage p. 1 | 0 / 0 | All 35 native grid-island members remain protected | No generic block stream is created | The dense card/page structure still needs explicit model relations or streams. |

This is intentionally a local semantic improvement rather than a score
shortcut: PUMA and JD do not yet have human relation labels, so the stream and
proposal counts are diagnostic evidence only. The PUMA stress result also
shows why the next fidelity work must constrain masks and fitting inside these
streams instead of treating a new stream id as a conflict reduction by itself.

## Docling Body-Tree A/B

Docling now contributes only bounded same-page sibling runs rather than a serialized whole-page body order. Root-body runs stop at geometry boundaries and split again at stronger native local streams. On concrete native columns, their membership and edges remain reviewable secondary evidence rather than primary translation streams or executable page-level reorder constraints. The latest reruns use the blank-PDF-checked Chromium path; native-only and structure branches therefore print the same nonblank visual layer before their semantic metrics are compared.

| Sample | Docling regions / matched elements | Visual similarity | Semantic / local-stream result |
|---|---:|---:|---|
| Transformer-XL pp. 1-3 | 72 / 321 | `0.93853503` | Pair and successor remain `1.0`. Strict successor coverage stays `17/41`, strict anchor-path coverage stays `32/41`, and reviewable path coverage stays `41/41`; stream `needs-structure-evidence` improves `3 -> 2`, while candidate successor disagreement drops `108 -> 82`. |
| JD homepage p. 1 | 93 / 127 | `0.99536129` | All 35 native grid-island elements remain protected. Five Docling streams resolve 26 members and 3 relation edges; stream `needs-structure-evidence` improves `2 -> 1`, and candidate successor disagreement drops `114 -> 47`. |

The same checked print path reruns PUMA p. 5 at `0.95767110` and BYD p. 136 at `0.96518593`, both with zero A/B visual delta. BYD still gains its one `table-island` stream and a one-page `needs-structure-evidence` improvement; these runs verify rendering stability, not new semantic labels.

### Reading-order sidecar proposal v3

Each benchmark branch now emits a reviewable `reading-order.sidecar.proposal.json`. The counts below are intentionally split into strict executable local edges, review-only local edges, and review-only cross-stream transitions. They are not semantic accuracy scores on samples without human labels.

| Sample | Native proposal: streams / strict / review / transitions | Structure proposal: streams / strict / review / transitions | Interpretation |
|---|---:|---:|---|
| Transformer-XL pp. 1-3 | 18 / 299 / 4 / 15 | 18 / 287 / 16 / 25 | Structure evidence moves a small number of local edges from automatic execution to review instead of inventing a cross-column global order. |
| JD homepage p. 1 | 10 / 39 / 85 / 11 | 16 / 39 / 79 / 24 | Model blocks add local stream boundaries and grid/body partitions while preserving the same strict edge count. |
| PUMA p. 5 | 2 / 1 / 22 / 1 | 2 / 1 / 22 / 1 | The local-stream guard correctly prevents generic model text blocks from fragmenting a stable native flow. |
| BYD financial report p. 136 | 17 / 17 / 0 / 16 | 17 / 17 / 0 / 16 | Table evidence changes one stream's type to `table-island`; the confident local chains remain stable. |

Transformer-XL is the labelled check for this proposal layer. In `outputs/external/transformer-xl-ppstructurev3-ab-pages-1-3-sidecar-proposal-v3`, native-only direct strict edges have `17/17` labelled precision and cover `17/41` anchor transitions (`0.41463415`). Native-plus-structure has `15/15` direct strict precision and covers `15/41` (`0.36585366`); its two moved review edges are both correct, so direct strict-plus-review coverage remains `17/41` (`0.41463415`).

Those direct counts are intentionally retained, but they are not sufficient for an `ordered-subsequence` semantic sidecar: labels can skip unlabelled IR nodes. The path-aware scorer now checks whether consecutive labelled anchors are connected without crossing another labelled anchor. Native-only has strict local path coverage `32/41` (`0.78048780`) and reviewable graph path coverage `41/41` (`1.0`); native-plus-structure has strict local `30/41`, strict-plus-local-review `32/41`, and reviewable graph `41/41`. The final nine native anchor transitions are supported only through review-only cross-stream handoffs, so they remain non-executable. This corrects the partial-label measurement gap without claiming that review transitions are safe automatic layout constraints.

### Evidence-gated local promotion v1

`reading_order_confidence` describes a page strategy, not a particular edge. The sidecar now promotes a review-only edge only when it remains inside one provisional stream and all three independent signals agree: mutual forward geometry, a selected full-page relation-graph edge with score at least `0.86`, and direct successor agreement from visual-YX, box-flow, and relation-graph stream candidates. An exactly tied feasible relation-graph alternative blocks promotion even when those three checks agree. The proposal records `geometry-mutual-neighbor`, `relation-graph-selected`, and `stream-consensus-3-of-3`; tied review edges also carry selection-time `relation_graph` margin diagnostics. Cross-stream transitions remain review-only.

The following reruns are stored under `outputs/external/*-edge-evidence-v1`. Counts are streams / strict / review / transitions. They are proposal counts, not accuracy claims on unlabelled samples.

| Sample | Native proposal | Native-plus-structure proposal | Result |
|---|---:|---:|---|
| Transformer-XL pp. 1-3 | 18 / 299 / 4 / 15 | 18 / 299 / 4 / 21 | No review edge met all gates. Strict anchor-path coverage remains `32/41` native and `30/41` structure; reviewable path coverage remains `41/41` for both. |
| PUMA annual report p. 5 | 2 / 13 / 10 / 1 | 2 / 13 / 10 / 1 | Twelve stable body edges move from review to strict, compared with the prior `1 / 22` local split. PUMA has no semantic sidecar, so this is evidence coverage only, not a correctness score. |
| JD homepage p. 1 | 10 / 39 / 85 / 11 | 15 / 39 / 80 / 45 | No low-consensus OCR/card edge is promoted. The structure branch retains the `-60` successor-consensus disagreement delta, while raw block boundaries increase review transitions. |
| BYD annual report p. 136 | 17 / 17 / 0 / 16 | 17 / 17 / 0 / 19 | Table-like content receives no accidental promotion. The raw PP-Structure page JSON does not lower the stream `needs-structure-evidence` count in this rerun. |

The new gate is intentionally precision-first. It demonstrates a general way to recover stable local chains without converting every low-confidence page strategy into an executable relation. The next promotion criterion is labelled relation or stream coverage on additional complex documents, not a larger raw strict-edge count.

The BYD page-136 pseudo-translation A/B leaves the total at 17 overflows and 17 conflicts, so table structure alone is not a fidelity fix. It does, however, move 10 replacements into `table-island` and attributes 9 conflicts to that one local stream instead of the body streams. That is the measurable target for table-aware mask padding and text fitting.

### Relation-Graph Selection Ambiguity

The relation graph now reports selection-time alternatives rather than only a serialized candidate order. `path_cover_edge_count` excludes serialization handoffs; `tied_edge_count` is limited to exactly equal feasible alternatives; `mean_minimum_margin` summarizes only edges that had an alternative. None of these are correctness scores or runtime switching thresholds.

| Sample | Output | Path-Cover Edges | Exact Ties | Mean Minimum Margin | Result |
|---|---|---:|---:|---:|---|
| Transformer-XL pp. 1-3 | `outputs/external/transformer-xl-relation-ambiguity-v1` | 288 | 3 (1.041667%) | 0.00123018 | Visual `0.98160664`, semantic pair/successor accuracy remains `1.0`; ties remain review-only. |
| PUMA pp. 1-12 | `outputs/external/puma-2024-annual-report-relation-ambiguity-v1` | 329 | 0 | 0.03710031 | Visual `0.9795117`; weak/missing structure evidence, not exact ties, is the main unresolved source. |
| JD screenshot PDF | `outputs/external/jd-home-relation-ambiguity-v1` | 93 | 2 (2.150538%) | 0.03896739 | Visual `0.99576887`; explicit local streams or successor relations remain required. |
| BYD p. 136 | `outputs/external/byd-2024-annual-report-relation-ambiguity-v1` | 30 | 0 | 0.09570952 | Visual `1.0`; table/translation streams still need structural evidence despite no exact tie. |

This confirms that a margin gate is not a substitute for semantic structure: it prevents arbitrary promotion, while PaddleOCR-VL/PP-Structure/Docling relation or stream output must resolve the remaining low-evidence local flows.

## Translation Stress Results

`outputs/external/translation-stress-v3` writes deterministic pseudo-expanded replacements to `translated_text`, prints fidelity HTML back to PDF, and measures both visual similarity and replacement risk. It covers 15 pages across PUMA, JD, and web-HN with `mismatched_case_count = 0`, `dimension_match_rate = 1.0`, and `page_count_match_rate = 1.0`.

| Sample | Pages | Selected Path | Visual Similarity | Max Diff | Mean Diff | Translation Elements | Expansion | Overflows | Conflicts | Conflict Targets | Min Fit | Mean Fit | Grid Islands | Page/Size Match | Semantic Successor |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| PUMA 2024 Annual Report | 12 | `fidelity/svg` | 0.67616927 | 0.32383073 | 0.12495262 | 398 | 1.99511484 | 187 | 396 | 637 | 0.62 | 0.68186231 | 31 | yes / yes | n/a |
| JD homepage screenshot PDF | 1 | `fidelity/raster` | 0.87463572 | 0.12536428 | 0.12536428 | 104 | 6.15817223 | 97 | 104 | 192 | 0.62 | 0.63202981 | 35 | yes / yes | n/a |
| Hacker News print PDF | 2 | `fidelity/raster` | 0.90618105 | 0.09381895 | 0.04962468 | 65 | 2.23526357 | 42 | 65 | 70 | 0.62 | 0.63153077 | 0 | yes / yes | 1.0 |

Combined summary: mean visual similarity is `0.81899535`, max diff is `0.32383073`, mean diff is `0.09998053`, p95 diff is `0.30398408`, total translation elements are `567`, total overflows are `326`, total conflicts are `565`, and total conflict targets are `899`. `grid_island_element_count` totals `66`: 31 from PUMA and 35 from JD.

The same run reports page-level candidate recommendations as `keep-selected-low-consensus: 1`, `keep-selected-supported: 5`, `needs-structure-evidence: 7`, and `review-disagreement: 2`. Stream-level diagnostics are stricter on local flows: `keep-selected-low-consensus: 5`, `keep-selected-supported: 39`, `needs-structure-evidence: 17`, `review-consensus: 1`, and `review-disagreement: 1`.

A previous JD-only stress run exposed a Chromium extra blank tail page: the real page diff was `0.12536428`, but the report was dominated by an extra blank exported page and scored the case as `visual_similarity = 0.0`. `print_html_to_pdf()` now removes only trailing blank artifact pages beyond the expected source page count, so the JD stress score is `0.87463572` and real nonblank overflow pages remain measurable.

BYD standalone translation stress is tracked separately in `outputs/external/byd-2024-annual-report-translation-stress-v1` because it is much heavier than the three-sample PUMA/JD/web-HN sweep. The first 40 pages have `visual_similarity = 0.90512026`, `max_diff_ratio = 0.09487974`, `mean_diff_ratio = 0.05402886`, `p95_diff_ratio = 0.07766083`, and no page-count or dimension mismatch. It creates 2274 pseudo-translated replacements with expansion ratio `3.31871867`, 1182 overflows, 1257 conflicts, 764 conflict targets, min fit scale `0.62`, and mean fit scale `0.66336636`.

BYD also exercises the stream-local replacement diagnostics. Replacement conflicts by stream type are `body: 648`, `grid-island: 453`, `table-island: 124`, `sidebar-right: 19`, and `footnote: 13`. The largest stream-id conflict buckets are `body-main: 378`, `grid-island-001: 199`, `grid-island-002: 141`, `body-segment-002: 109`, and `table-island-001: 103`, making it a useful target for future mask/fitting work.

PUMA has no semantic sidecar yet, so its high reading-order risk is a useful signal for the next labeling pass. Its OCR fallback counts are 0 because the sampled pages already expose native PDF text. The current diagnostics report 5 repeated-anchor pages, max 3 anchors, 4 table-like pages, and 0 table-like visual-yx pages. The current mixed-table/artifact/sidebar/footnote pass reports 99 direct column-flow elements, 238 mixed-table-flow elements, 20 header artifacts, 36 right-side sidebar/marginalia elements, and 2 footnote elements, keeping detected local table islands row-major while surrounding body text can still use column flow.

Semantic successor-edge metrics are intentionally unavailable for PUMA and JD until tracked `.semantic-order.json` sidecars are added. The current successor validation is covered by the built-in fixtures at 47/47 labelled edges, arXiv Attention at 33/33, Transformer-XL first 3 pages at 41/41, and Hacker News at 24/24; these metrics will become the main local-continuity score once external complex-page sidecars are expanded.

For PUMA and JD, the next sidecar pass should prefer relation-style labels for ambiguous regions: `successor_edges` for local body/sidebar/OCR list chains and `precedence_edges` for looser section, caption, table, or marginalia constraints. This avoids overfitting one arbitrary serialized order on pages where several human-readable global orders are acceptable.

The relation-graph diagnostics keep the same PUMA/JD external visual scores while adding a geometry-only successor-graph candidate next to the existing box-flow candidate. Structure-relation candidate metrics are now available in code as a sidecar-scored order that combines page artifacts, footnotes, sidebars, caption-target proximity, and relation-graph body order; they will be added after the next external benchmark refresh with semantic sidecars or model evidence. Successor-consensus diagnostics are also available in code, including selected-edge support, edge coverage, conflicted-edge ratio, agreement-level page counts, page-level selected-vs-consensus recommendation counts, and conservative runtime arbitration element counts; they will be added to this table on the next external benchmark refresh. Caption-target proximity diagnostics are available in code: future refreshes will report targeted/orphan caption counts and target coverage when sampled text exposes figure/table labels. Both current external cases still report 0 caption nodes because the sampled native/OCR text does not expose leading caption labels. They also report 0 spatial-graph and 0 box-flow nodes because stronger existing paths win first. PUMA has no pure `table-row-major-v1` nodes in the sampled pages because table-like regions are handled as mixed table islands; it still reports 36 `sidebar-secondary-flow` / `right-sidebar` evidence hits, 2 `footnote-secondary-flow` / `bottom-note-zone` evidence hits, 46 `table-island-row-major` hits, 20 `page-edge-artifact` hits, 163 `column-flow` hits, and 271 `single-column-visual-order` hits. JD reports 134 `recursive-xy-cut` OCR anchors with horizontal/vertical whitespace-cut evidence.

New benchmark reports also include `reading_order_stream_count`, `reading_order_stream_type_counts`, and `reading_order_stream_id_counts`. The latest grid-island rerun shows that PUMA exposes local body/sidebar/footnote/artifact/table-island streams plus 2 `grid-island` streams covering 31 elements, while JD exposes 3 `grid-island` streams covering 35 OCR elements. External Paddle/PP-Structure/Docling labels can now reinforce explicit card/grid/product/tile regions as `grid-island` translation streams; plain list labels remain list evidence rather than card-grid evidence.

The box-flow, relation-graph, and successor-consensus disagreement ratios are not correctness scores. Pairwise disagreement flags broad candidate-order differences: PUMA's box-flow ratio is `0.17460108`, while relation graph is `0.16306211`; JD's box-flow ratio is `0.42778588`, while relation graph is `0.21624958`. Successor disagreement is stricter about immediate next-node edges: PUMA improves from 199/509 box-flow disagreements to 166/509 relation-graph disagreements, and JD improves from 127/133 to 117/133. These values support keeping relation graph and successor consensus as candidate signals, but PUMA and JD still need semantic sidecars or external Paddle/PP-Structure/Docling evidence before changing selected ordering rules.

JD is image-only by design. The latest run keeps the same source-preservation score while adding 134 transparent `native-ocr` editable anchors. Its OCR text now stays out of the mixed-table strategy after the duplicate-slot formula/table guard and is handled by recursive XY-Cut. Its reading risk is high because text is available but no semantic sidecar exists yet; that is a better diagnostic than the previous 0-text low-risk result.
