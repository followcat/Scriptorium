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
  --out-dir outputs/external/translation-stress-padding-v1 \
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

The new `run-pp-structure` command was independently replayed on Attention p. 1 at 144 DPI in `outputs/research/attention-pp-structure-runner-ab-v1`. Its layout-only configuration produced 58 normalized regions and 55 matched native elements. Visual delta stayed `0.0`; labelled pair/successor accuracy stayed `1.0`; direct strict sidecar successor coverage rose from `3/9` to `5/9` (`0.33333333 -> 0.55555556`). The lower raw region count than the older control is expected because this command deliberately disables table, formula, and region modules unless explicitly requested.

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

A separate real PaddleOCR-VL 1.6 replay on Attention p. 1 is stored under `outputs/research/attention-paddle-ab-v1`. It matched 56 native elements through 16 model regions. Visual delta and selected semantic metrics remained unchanged, while direct strict sidecar successor coverage increased from `3/9` to `6/9` (`0.33333333 -> 0.66666667`). This supports using the VLM's block order as reviewable local-stream evidence, not as a substitute for explicit relation prediction.

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
| PUMA annual report p. 5 | 4 / 17 | No reorder; visual `0.95767110` at the coordinate A/B baseline | `7 / 12 / 6 / 7` streams / strict / review / transitions | 23 replacements, 6 overflows, 18 conflicts/targets; v2 constrains 17 masks across 26 sides without changing visual similarity. |
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

### Explicit block-order review transitions v2

Paddle layout outputs commonly expose ordered blocks without successor relations. Sidecar schema `1.1` converts only unique, explicitly numbered, consecutive primary text blocks into provenance-rich review transitions. Secondary content and nonlinear islands are boundaries, missing numeric orders cannot be skipped, and the strict transition count is fixed at zero. The new benchmark fields score these model proposals independently from native local edges and generic selected-order handoffs.

Image A/B runs now have two explicit inputs: `--ocr-json` creates the same text/bbox anchors in both branches, while `--structure-json` is exclusive to the structure branch. A precise unordered PP-Structure OCR line may be linked to one unambiguous explicitly ordered parent; the precise anchor continues to own label/bbox/confidence, while parent order is stored only as `ordered_companion` review evidence. It cannot drive runtime partial order or a derived block stream, and conflicting parent orders are rejected. ROOR `ro_linkings` exist only in the adjacent scoring sidecar; all answer relations are removed from input anchors.

| Provider / sample | Review candidates | Labelled / correct | Precision | Label coverage | Strict | Visual delta |
|---|---:|---:|---:|---:|---:|---:|
| PaddleOCR-VL 1.6, Attention p. 1 | 2 | 1 / 1 | `1.0` | `1/9` (`0.11111111`) | 0 | `0.0` |
| PP-StructureV3 runner, Attention p. 1 | 1 | 0 / 0 | unavailable | `0/9` | 0 | `0.0` |
| PP-StructureV3, Transformer-XL pp. 1-3 | 12 | 5 / 5 | `1.0` | `5/41` (`0.12195122`) | 0 | `0.0` |
| PP-StructureV3, fixed five-page ROOR prefix | 4 | 4 / 4 | `1.0` | `4/205` (`0.01951220`) | 0 | `0.0` |

All ten labelled transitions are correct, which is encouraging, but coverage remains too sparse to promote block order into runtime constraints. Ordered-parent fusion raises Transformer correct coverage from `3/41` to `5/41`; after isolating it as review-only evidence, selected-successor delta, order-driven reorder, and visual delta all remain zero. Making model blocks secondary subgroups then raises Transformer strict anchor-path coverage from native `0.78048780` to structure `0.80487805`, keeps reviewable path coverage at `1.0` in both branches, and reduces stream `needs-structure-evidence` from `3` to `2`.

The fixed ROOR pages are `82251504`, `82837252`, `85201976`, `86263525`, and `93106788`; they were not selected by result. All four proposals come from `86263525`, score `4/24`, and reduce its stream `needs-structure-evidence` count from `4` to `2`; the other four pages do not satisfy the proposal guards. Strict transitions, order-driven reorders, selected-successor deltas, and visual deltas are zero across all five. Outputs are under `outputs/research/*-block-transitions-v3` and `outputs/research/roor-pp-structure-block-transitions-v4`.

### Surya FastLayout learned-order review v1

`scriptorium run-surya-layout` runs Surya 0.21.1 FastLayout with its learned order head and saves replayable structure JSON. It requires explicit model-license acceptance and fails instead of accepting raster fallback when the order head, detector features, advertised capacity, or a complete integer permutation is unavailable. The tested weights advertise a 128-box order capacity. All labels, orders, and successor edges carry review-only semantic/order/relation policies, so they cannot change roles, streams, semantic-layer ownership, or runtime order.

The fixed five ROOR pages were evaluated first, then held-out Attention, Transformer-XL, JD, and PUMA cases were run without changing provider thresholds or fusion rules:

| Sample | Review candidates | Labelled / correct | Precision | Correct label coverage | Full external candidate | Runtime / visual delta |
|---|---:|---:|---:|---:|---:|---:|
| Fixed five-page ROOR prefix | 42 | 41 / 30 | `0.73170732` | `30/205` (`0.14634146`) | relation successor `99/205` (`0.48292683`) | 0 / `0.0` |
| Transformer-XL pp. 1-3 | 23 | 9 / 2 | `0.22222222` | `2/41` (`0.04878049`) | successor `21/41` (`0.51219512`) | 0 / `0.0` |
| Attention p. 1 | 3 | 1 / 1 | `1.0` | `1/9` (`0.11111111`) | successor `9/9` (`1.0`) | 0 / `0.0` |
| JD homepage | 5 | unlabelled | unavailable | unavailable | unlabelled | 0 / `0.0` |
| PUMA p. 5 | 4 | unlabelled | unavailable | unavailable | unlabelled | 0 / `0.0` |

The ROOR run reduces stream `needs-structure-evidence` by four, but the held-out Transformer precision collapse disproves any general runtime-promotion rule. Before semantic isolation, Surya labels and relations could indirectly change sidecar role/stream construction: Transformer strict anchor-path coverage regressed from native `32/41` to `20/41`, while Attention moved from `3/9` to `6/9`. After `semantic_policy: review-only` is enforced, both retain their native strict paths (`32/41` and `3/9`) while the model proposals remain separately scoreable. Strict block transitions, relation/order-driven reorders, and visual deltas stay zero.

Artifacts are under `outputs/research/surya-fast-layout-roor-v1/fixed-five-semantic-isolated-ab` and `outputs/research/surya-fast-layout-heldout-v1/*-semantic-isolated-ab`. The result is a review provider, not a runtime reading-order driver.

### OpenDataLoader XY-Cut++ review v1

OpenDataLoader PDF 2.4.7 provides an Apache-2.0 deterministic XY-Cut++ path on
CPU/Java. `scriptorium run-opendataloader` preserves raw JSON and emits a
normalized replay with stable ids, top-left PDF coordinates, and review-only
block order and successor edges. The CLI output was regenerated from the source
PDFs before this A/B run; no saved hand-written sidecar was used.

| Sample | Blocks / provider edges | Block review candidates | Labelled / correct | Full external successor | Stream needs delta | Runtime / visual delta |
|---|---:|---:|---:|---:|---:|---:|
| Attention p. 1 | 22 / 21 | 4 | 1 / 1 | `9/9` (`1.0`) | `+1` | 0 / `0.0` |
| Transformer-XL pp. 1-3 | 57 / 54 | 29 | 11 / 11 | `34/41` (`0.82926829`) | `-1` | 0 / `0.0` |

Transformer's external candidate materially exceeds Surya's held-out `21/41`,
but still loses to selected native order at `41/41`. Its labelled block-review
edges are precise on this sample, but are not independent enough or broad enough
to authorize runtime promotion. Intersecting OpenDataLoader with the existing
PP-StructureV3 proposal produces 9 consensus edges from 32 unique provider
candidates; 4 are labelled and all 4 are correct (`4/41` coverage). This remains
review-noise reduction, not an accepted relation model. Outputs are under
`outputs/research/opendataloader-xycut-v1`; reproducibility reruns are under its
ignored `replay-cli` directory.

### Independent provider consensus v1

`scriptorium consensus-reading-sidecars` intersects explicit block-order review transitions from at least two independent providers. It rejects mismatched page sets or stable document fingerprints (element id, text, and PDF bbox), preserves provider/confidence provenance, and always writes an unaccepted review-only proposal with `runtime_reorder: false`.

| Sample | Providers | Provider candidate edges | Consensus edges | Labelled / correct | Correct label coverage |
|---|---:|---:|---:|---:|---:|
| Attention p. 1 | 3 | 4 | 2 | 1 / 1 | `1/9` (`0.11111111`) |
| Transformer-XL pp. 1-3 | 2 | 33 | 2 | 1 / 1 | `1/41` (`0.02439024`) |
| Fixed five-page ROOR prefix | 2 | 42 | 4 | 4 / 4 | `4/205` (`0.01951220`) |
| PUMA p. 5 | 2 | 4 | 3 | unlabelled | unavailable |

Across the labelled sets, consensus emits eight candidates, six of which are labelled and all six correct. Precision on labelled candidates is `6/6`, but correct coverage is only `6/255` (`0.02352941`). This is useful review-noise reduction, not enough evidence to accept edges or broaden runtime arbitration. Artifacts are under `outputs/research/provider-consensus-v1`.

### Secondary block subgroups v1

An ordered model block is derived only when all members share one native flow segment and column. It is now stored in `external_structure_stream_*` with `primary = false` instead of replacing the primary `reading_order_stream_*`. HTML exposes the second layer through `data-scriptorium-structure-stream-*`, allowing a translator to batch paragraphs/blocks inside a stable primary stream. Review provenance for block transitions remains intact.

| Provider / sample | Derived blocks / members | Stream needs: native -> structure | Strict anchor path | Other result |
|---|---:|---:|---:|---|
| PaddleOCR-VL 1.6, Attention p. 1 | 2 / 17 | `0 -> 0` | `0.33333333 -> 0.33333333` | Two block-review candidates remain; successor and visual deltas are zero. |
| PP-StructureV3 runner, Attention p. 1 | 2 / 17 | `0 -> 0` | `0.33333333 -> 0.33333333` | One unlabelled block-review candidate remains; successor and visual deltas are zero. |
| PP-StructureV3, Transformer-XL pp. 1-3 | 21 / 158 | `3 -> 2` | `0.78048780 -> 0.80487805` | All 12 candidates remain with `5/5` labelled correctness; successor-consensus disagreement is `-26`, while selected-successor and visual deltas are zero. |
| PP-StructureV3, JD homepage | 5 / 29 | `1 -> 1` | unlabelled | Repairs the previous `1 -> 2` stream regression while retaining successor-consensus disagreement `-62`; visual delta is zero. |
| PP-StructureV3, PUMA p. 5 | 4 / 15 | `0 -> 0` | unlabelled | Primary-stream diagnostics and visual delta remain unchanged. |

JD and PUMA have no human relation labels, so those rows demonstrate that block grouping no longer creates primary-stream fragmentation; they are not semantic-accuracy claims. Outputs are under `outputs/research/*-secondary-block-streams-v1`.

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

## Native Local Structure Evidence v1

`outputs/external/local-structure-evidence-v2` reruns the same 15-page PUMA, JD, and Hacker News translation-stress set at 144 DPI with raster fidelity. It does not change selected page order or visual rendering; it separates native table/grid island successors from the generic page-level candidate vote. `local_structure_successor_coverage` is strict over potential island edges, while the consensus-conflict column counts strict local edges that the generic successor-consensus candidate does not preserve.

| Sample | Native local streams | Strict local edges | Strict coverage | Local edges conflicting with generic consensus | Local reference-page coverage | Stream recommendation |
|---|---:|---:|---:|---:|---:|---|
| PUMA annual report, pp. 1-12 | 6 | 71 | 1.0 | 50 / 71 | 0.13948919 | 6 `keep-selected-local-structure` |
| JD homepage screenshot PDF | 3 | 32 | 1.0 | 30 / 32 | 0.24060150 | 3 `keep-selected-local-structure` |
| Hacker News print PDF | 0 | 0 | 0.0 | 0 / 0 | 0.0 | none |

The aggregate is 9 native local streams, 103 strict edges, full strict coverage, and 80 / 103 (`0.77669903`) strict edges that generic consensus would otherwise break. This is not a semantic accuracy gain: the protected local edges were already selected by the native table/grid detector. It makes the disagreement honest and prevents a generic page candidate from falsely classifying a fully evidenced island as `needs-structure-evidence`.

Two current PP-StructureV3 A/B controls show why those dimensions must be read together. For JD (`outputs/external/jd-local-structure-ppstructure-ab-v1`), 128 matched structure elements leave the three native local streams and 32 strict edges unchanged, reduce local-vs-consensus conflicts from 30 to 13, but increase stream `needs-structure-evidence` from 1 to 2; visual and reading-risk deltas are both zero. For PUMA p. 5 (`outputs/external/puma-local-structure-ppstructure-ab-v1`), 24 matched elements derive four bounded body streams but the page has no native local table/grid island, so all local-structure deltas remain zero. Neither provider emits explicit relation or stream edges in these runs, so neither result promotes a runtime order change.

## Protected Local Constraints v1

`outputs/external/protected-local-structure-v1` reruns the same 15 pages with `protected_successor_consensus`. This is a diagnostic candidate only: valid strict native table/grid edges enter the degree-constrained DAG before generic candidate edges, so they do not count as synthetic votes and cannot create a cross-stream relation. It records protected edges, unresolved constraints, individual rejection reasons, and strict edges still absent after constrained serialization.

| Sample | Strict local edges | Broken by generic consensus | Protected | Unresolved | Missing after constrained consensus |
|---|---:|---:|---:|---:|---:|
| PUMA annual report, pp. 1-12 | 71 | 50 | 71 | 0 | 0 |
| JD homepage screenshot PDF | 32 | 30 | 32 | 0 | 0 |
| Hacker News print PDF | 0 | 0 | 0 | 0 | 0 |

Across the set, all 103 strict local edges are accepted as constraints; all 80 local edges that generic consensus would break are retained by the constrained candidate; no unknown endpoint, degree, or cycle rejection occurs. Visual similarity remains `0.92760169`, identical to the browser-fit baseline, because selected IR order and visual rendering are unchanged. This verifies local constraint plumbing, not semantic accuracy: PUMA and JD still have no tracked relation-style ground truth, while labelled Hacker News has no applicable native table/grid constraint. Accordingly, the protected candidate's semantic aggregate is unavailable (`null`), and it remains excluded from runtime and automatic candidate recommendations.

## ROOR Relation Benchmark v1

[ROOR](https://github.com/chongzhangFDU/ROOR-Datasets) labels layout reading order as directed immediate-successor relations (`ro_linkings`) between document segments. `scriptorium fetch-roor` downloads a fixed published split prefix rather than selecting examples by outcome, pinned to upstream revision `6b5ca2b2cc6ad02ab1dd8ec1c17551ab614f0aa0` and recorded in its manifest. For each page it writes the source image, the original annotation, an adjacent `.semantic-order.json` used only for evaluation, and a derived `layout-anchor-only` structure JSON. The latter contains the image metadata and `document` text/bbox anchors but removes `ro_linkings`, `label_entities`, and all task labels, so the answer relations cannot enter `--structure-json` fusion.

```bash
scriptorium fetch-roor \
  --out-dir data/external/roor-validation-full-v1 \
  --split val \
  --sample-count 49

structure_args=()
for path in data/external/roor-validation-full-v1/structure/*.structure.json; do
  structure_args+=(--structure-json "$path")
done

scriptorium benchmark \
  data/external/roor-validation-full-v1/images/*.png \
  --input-kind image \
  "${structure_args[@]}" \
  --out-dir outputs/external/roor-validation-full-v1-native-layout \
  --dpi 96 \
  --image-dpi 96 \
  --ocr-fallback off \
  --html-mode fidelity \
  --fidelity-background raster
```

The complete published `val` split has 49 pages. Its 2,602 supplied layout anchors all match the generated IR; no official relation is supplied to the converter. All 49 reports resolve relation endpoints through stable element IDs with zero unresolved identifiers. There are 2,612 labelled immediate-successor relations. This matters because 34 pages contain duplicate segment text: text-only normalization would collapse distinct relations. The source-image fidelity score is mechanically `1.0` in this setup because raster fidelity preserves the input image, so it is not evidence of reading-order quality.

| Evidence / candidate | Correct / labelled relations | Relation successor accuracy | Scope |
|---|---:|---:|---|
| Selected native order | 1,274 / 2,612 | `0.48774885` | all labelled relations |
| Generic `successor_consensus` | 1,043 / 2,612 | `0.39931087` | all labelled relations |
| Diagnostic `protected_successor_consensus` | 778 / 1,856 | `0.41918103` | only pages where that candidate exists |
| Strict native local table/grid proposal edges | 316 / 617 | `0.51215559` | directly labelled proposal endpoints |
| Strict native table-island proposal edges | 227 / 406 | `0.55911330` | directly labelled table endpoints |
| Strict native grid-island proposal edges | 89 / 211 | `0.42180095` | directly labelled grid endpoints |

This is an oracle-layout/order evaluation, not an end-to-end OCR score: ROOR provides the text and boxes so it isolates the reading-order problem. It also invalidates the earlier intuition that native geometric table/grid chains can automatically become hard runtime constraints. Constraint serialization can preserve those chains, but the chains themselves are only about 51% precise on this independent relation set, and the protected candidate does not beat the selected native order. Native local edges therefore remain review and translation-stream evidence. A hard runtime constraint must come from explicit external successor/stream relations, a validated relation predictor, or an accepted human review.

## Translation Stress Results

`outputs/external/translation-stress-padding-v1` writes deterministic pseudo-expanded replacements to `translated_text`, prints fidelity HTML back to PDF, and measures both visual similarity and replacement risk. It covers 15 pages across PUMA, JD, and web-HN with `mismatched_case_count = 0`, `dimension_match_rate = 1.0`, and `page_count_match_rate = 1.0`. The run uses `fidelity-replacement-fit-v2`, which constrains local mask padding against adjacent visible boxes without changing text coordinates or fitting policy.

| Sample | Pages | Selected Path | Visual Similarity | Max Diff | Mean Diff | Translation Elements | Expansion | Overflows | Conflicts | Conflict Targets | Masks / Sides Constrained | Min Fit | Mean Fit | Grid Islands | Page/Size Match | Semantic Successor |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| PUMA 2024 Annual Report | 12 | `fidelity/svg` | 0.67731004 | 0.32268996 | 0.12552682 | 398 | 1.99511484 | 187 | 306 | 320 | 314 / 529 | 0.62 | 0.68186231 | 31 | yes / yes | n/a |
| JD homepage screenshot PDF | 1 | `fidelity/raster` | 0.87466976 | 0.12533024 | 0.12533024 | 104 | 6.15817223 | 97 | 104 | 189 | 50 / 110 | 0.62 | 0.63202981 | 35 | yes / yes | n/a |
| Hacker News print PDF | 2 | `fidelity/raster` | 0.90613373 | 0.09386627 | 0.04964834 | 65 | 2.23526357 | 42 | 65 | 69 | 32 / 33 | 0.62 | 0.63153077 | 0 | yes / yes | 1.0 |

Combined summary: mean visual similarity is `0.81937118`, max diff is `0.32268996`, mean diff is `0.10016847`, p95 diff is `0.30295399`, total translation elements are `567`, total overflows are `326`, total conflicts are `475`, and total conflict targets are `578`. The run constrains `396` replacement masks across `672` directional sides. Against the v1 baseline on the same inputs, that is `90` fewer conflicts and `321` fewer conflict targets with unchanged overflow, so the result is a mask-safety improvement rather than a long-text fitting improvement. `grid_island_element_count` totals `66`: 31 from PUMA and 35 from JD.

### Browser-Layout Fit v3

`outputs/external/translation-stress-browser-fit-v2` reruns the same 15 pages after the v3 browser-layout replacement pass. It uses raster fidelity for all three selected cases, measures the generated HTML in Chromium after print media and fonts are ready, and writes a `quality/fidelity_replacement_layout_report.json` sidecar for each case. The resulting PDF is produced only after the same fitting pass has run.

| Sample | Pages | Selected Path | Visual Similarity | Static Estimated Overflow | Actual Chromium Clips | Actual Mean Fit | Browser Fitted / Line-Height Compacted | Dark Masks | Page/Size Match |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 Annual Report | 12 | `fidelity/raster` | 0.89910421 | 187 | 0 | 0.95120879 | 398 / 13 | 101 | yes / yes |
| JD homepage screenshot PDF | 1 | `fidelity/raster` | 0.95797219 | 97 | 79 | 0.67350577 | 104 / 8 | 0 | yes / yes |
| Hacker News print PDF | 2 | `fidelity/raster` | 0.92572866 | 42 | 2 | 0.92540769 | 65 / 60 | 0 | yes / yes |

The combined visual similarity is `0.92760169`; max / mean / p95 diff are `0.10089579` / `0.04620805` / `0.09823334`. All 567 replacements were browser-fitted; the mean actual fit scale is `0.89731428` versus static `0.66695203`, 81 selected compact line height, and 101 used a sampled dark mask. The run records 326 static estimated overflows and 81 actual browser clips. Those are intentionally different measures, so they are not a direct `326 -> 81` before/after reduction. It records 400 conflicts and 578 conflict targets under the measured clipping policy.

This is an end-to-end score, not an isolated causal attribution: PUMA selects raster in v3 whereas the historical padding-only table selected SVG, and the run also contains the print-DPI coordinate correction and dark-mask behavior. The remaining 79 actual clips on JD are the primary generic reflow target; they should be addressed through local stream/region layout rather than sample-specific rules.

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

## Docling ROOR Full Validation

The isolated Docling experiment uses all 49 pages of the official ROOR
validation split, with official `ro_linkings` withheld from extraction and used
only for scoring. Both A/B branches share the same layout-anchor OCR JSON.

| Candidate | Correct | Labelled | Accuracy |
|---|---:|---:|---:|
| Selected native, both branches | 1274 | 2612 | 0.48774885 |
| Isolated Docling external candidate | 785 | 1888 | 0.41578390 |
| Native successor consensus | 1043 | 2612 | 0.39931087 |

Docling produced a fully comparable candidate on 38 pages: 3 better, 3 equal,
and 32 worse than selected native order. Safety invariants stayed unchanged:
241 grid-island elements, 92 stream-level and 49 page-level
`needs-structure-evidence` cases, visual similarity 1.0, zero provider streams,
and zero reordered pages. The run yielded 1,916 review regions and 1,522 review
relations. This is negative generalization evidence: Docling remains an
isolated review candidate and is not a runtime orderer.

## Learned Successor Availability Audit

The official Apache-2.0 ROOR repository now contains relation-prediction code,
but its authors explicitly state that the fine-tuned ROP weights cannot be
released under their organization policy. FocalOrder publishes results but no
reproducible code or checkpoint. Neither can currently supply an executable
provider benchmark.

As an external research control, the OpenRAIL HURIDOCS LightGBM weights were run
on held-out Transformer-XL page 2. Its two-stage predictor selects 18 candidate
successors and then performs pairwise next-token ranking. After bbox/text
matching, 107 elements and 86 relations resolved; its isolated external
candidate scored `13/16` (`0.8125`) labelled successors. Selected native order
remained `16/16`; box-flow and relation-graph were both `8/16`. The provider is
not integrated because the required GitHub inference repository declares no
code license. The run also exposed and verified the generic isolation-contract
fix: native and structure successor consensus both remain `8/16`, and both page
recommendations remain `needs-structure-evidence`.

## ROOR-Trained Relation Ranker

The reproducible ranker uses 122 official train documents for fitting and 27
UID-hash train documents for calibration. Calibration fixes threshold `0.16`
before validation and reports precision `0.66132556`, recall `0.64857143`, and
F1 `0.65488640`. The tracked 49-page validation set has no sample overlap with
the train index.

The branch gate uses the same train/calibration boundary. Its fixed calibration
threshold is `0.75`, where relation F1 is `0.66737288`; no validation result is
used to select that threshold.

| Metric | Native selected | Top-1 ranker | Branching ranker |
|---|---:|---:|---:|
| Official relation correct / total after candidate decoding | 1274/2612 | 1513/2612 | 1529/2612 |
| Official relation accuracy | 0.48774885 | 0.57924962 | 0.58537519 |
| Direct predicted-edge precision | n/a | 0.68715305 | 0.67794118 |
| Direct predicted-edge recall | n/a | 0.66347626 | 0.70597243 |
| Direct predicted-edge F1 | n/a | 0.67510713 | 0.69167292 |

The branching external candidate gains `0.09762634` absolute relation accuracy
over native and is the best scored candidate on 35 of 49 pages. It adds 198
calibrated rank-2 edges, predicting 2,720 review edges in total; 2,686 resolve
to IR elements. Visual similarity, grid-island counts, selected order,
and reordered-page counts remain unchanged. After filtering isolated relation
provenance from explicit-successor diagnostics, both branches also retain the
same stream recommendations, including 92 `needs-structure-evidence` streams.
The result is sufficient to keep developing the model as an independent
candidate, but not to promote it to runtime order: decoded accuracy remains
0.585 and several multi-page/form-like samples have low direct precision.

### Cross-Domain DocumentIR Replay

Without retraining, the same ROOR model was applied to native PDF `DocumentIR`
anchors from two paper families:

| Sample | Selected | External ranker | Relation graph | Box flow |
|---|---:|---:|---:|---:|
| Transformer-XL pp. 1-3 | 41/41 | 29/41 | 22/41 | 14/41 |
| Attention pp. 1-3, 12-13 | 33/33 | 29/33 | 21/33 | 18/33 |

The external candidate generalizes above the geometry baselines but does not
beat the mature native paper order. All 502 predicted relations resolve, while
visual, selected-order, consensus, stream-diagnostic, and runtime-reorder deltas
remain zero.

Fit-only feature envelopes expose the domain shift that confidence misses.
ROOR validation feature-value OOD has median `0.02000` and maximum `0.04875`.
Transformer pages range `0.03028-0.03797`; Attention pages range
`0.05309-0.10667`. PUMA p. 5 is `0.07304` and the JD homepage is `0.08274`, even
though their mean pair confidences remain `0.86286` and `0.90136`. OOD is
therefore a rejection/triage diagnostic, not a score correction or correctness
claim. PUMA resolves 23/23 and JD 146/146 review edges with zero reorder and
zero visual/stream-diagnostic delta.

### Comp-HRDoc Fixed Test Prefix

Two official test documents were selected by published filename order, not by
benchmark outcome: `1401.3699` and `1402.2741`, first five pages each. Their
arXiv PDFs are rendered to official Comp-HRDoc dimensions, while line anchors
and relation-only sidecars come from the pinned unified test annotation.

| Scope | Native selected | External decoded | Relation graph | Visual-YX | Box flow |
|---|---:|---:|---:|---:|---:|
| `1401.3699`, pp. 1-5 | 191/205 | 205/205 | 205/205 | 205/205 | 100/205 |
| `1402.2741`, pp. 1-5 | 155/155 | 155/155 | 155/155 | 155/155 | 122/155 |
| Combined | 346/360 | 360/360 | 360/360 | 360/360 | 222/360 |

Across the 10 pages, raw learned edges score precision `0.93472585`, recall
`0.99444444`, and F1 `0.96366083` (358 correct / 383 predicted / 360 labels).
All 383 predicted relations resolve. Visual, selected-order, consensus,
stream-diagnostic, and reorder deltas remain zero. The result independently
confirms strong line/paragraph continuity, but visual-yx and relation graph also
score 360/360 because these pages are dominated by local textline chains. It
must not be presented as evidence that floating figures, tables, annual reports,
or portal grids are solved.
