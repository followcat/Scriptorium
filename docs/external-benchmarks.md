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

### Comp-HRDoc Floating-Role Replay

The first five pages of `1401.3699` were replayed after preserving graphical
nodes. The label set grows from 205 to 207 because page 2 contains two official
`figure -> caption` edges. Raw predictions improve from 204/217 correct
(precision `0.94009217`, recall `0.98550725`, F1 `0.96226415`) to 206/219
(precision `0.94063927`, recall `0.99516908`, F1 `0.96713615`). Both new
structure-role edges are correct. Downstream external/relation-graph accuracy
remains 207/207 and visual similarity remains `0.97221549`; all runtime and
stream diagnostic deltas remain zero. This two-edge replay validates the
mechanism, not broad floating-layout coverage.

### Expanded Floating Figure/Table Prefix

A broader fixed replay uses all 27 pages of the first published test document
`1401.3699`, plus the first six pages of `1411.3334`, the first published
document prefix that reaches a table floating group. Selection was fixed before
inference. The 33 pages contain 1,225 official relations and 18 graphical
relations: 15 figure-caption and three table-caption links.

| Relation source | Correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Same model, role fusion disabled | 1186 / 1277 / 1225 | 0.92873923 | 0.96816327 | 0.94804157 |
| Structure-role geometry enabled | 1203 / 1293 / 1225 | 0.93039443 | 0.98204082 | 0.95552025 |
| Structure-role edges only | 18 / 18 / 18 | 1.00000000 | 1.00000000 | 1.00000000 |

The adapter also exposed and fixed an oracle bug: official table groups may put
caption annotations before or after table annotations. Multi-line caption tails
now link to tables in either representation. Layout block ids are available to
inference, but official reading-order ids remain sidecar-only. Eighteen edges
are still too few for runtime promotion; the result supports the architecture
and motivates a larger document-diverse floating split.

### 250-Page Cross-Document Floating Corpus

The annotation-only corpus selects the first 250 graphical floating pages by
published image-name order before inference. It spans 53 test documents,
10,465 official successor labels, and 347 floating graphical labels. No page
images are downloaded or redistributed. The same model is scored in three modes:

| Mode | Correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Native ranker | 8895 / 10681 / 10465 | 0.83278719 | 0.84997611 | 0.84129386 |
| Native + global/calibrated structure role | 9193 / 10963 / 10465 | 0.83854784 | 0.87845198 | 0.85803621 |
| Native + trained floating | 9210 / 10964 / 10465 | 0.84002189 | 0.88007645 | 0.85958281 |

The calibrated global structure-role F1 delta over native is `+0.01674235`.
Its graphical edges alone score 306/350 against 347 labels: precision
`0.87428571`, recall `0.88184438`, F1 `0.87804878`, up from the original greedy
295/342 and `0.85631350`.

Locality parameters were selected without this test corpus. A constrained
official-train search kept vertical gap at `0.12` page height, removed mandatory
horizontal bbox overlap, and tightened horizontal center distance from `0.50`
to `0.35` page width. Fit changes `5284/5658 -> 5291/5665` correct/predicted;
the document-hash calibration partition changes `1348/1473 -> 1349/1474`.
Every changed fit/calibration page improves. After freezing the gate, three
fixed-test pages change and all three improve, adding five correct edges with
four predictions over global assignment alone. Role fusion remains review-only.

The learned decoder now runs the same cardinality-first maximum-weight global
assignment during threshold calibration and inference. Its margin is the
minimum score gap against both a competing caption in the source row and a
competing graphical block in the target column. On the official-train
document-hash calibration partition, threshold `0.36` scores 1,373/1,489
against 1,446 labels: precision `0.92209537`, recall `0.94951591`, F1
`0.93560477`, up from the greedy decoder's `0.91295681`. The 250-page test was
not used for threshold or reliability selection.

With that decoder frozen, graphical test edges improve from 321/356 and F1
`0.91322902` to 323/357 and F1 `0.91761364`; the only changed page is
`1507.01067_7`, improving from 2/3 to 4/4. Overall F1 gains `+0.01828895` over
native and `+0.00154660` over calibrated global heuristic fusion.

| Train-calibrated test subset | Correct / predicted / labels | Precision | Recall |
|---|---:|---:|---:|
| High-precision review | 235 / 242 / 347 | 0.97107438 | 0.67723343 |
| Review + zero OOD | 204 / 209 / 347 | 0.97607656 | 0.58789625 |
| Strict | 196 / 201 / 347 | 0.97512438 | 0.56484150 |
| Strict + zero OOD | 169 / 173 / 347 | 0.97687861 | 0.48703170 |

The answer-free label audit finds 306 exact local-geometry agreements, 32
conflicting official graphical labels across 24 pages, nine labels without a
geometry proposal, and 12 proposals without an official label. All five clean
strict errors touch a graphical object in the conflict set; all four strict
zero-OOD errors do as well. This is diagnostic context, not permission to change
the official score or labels.

### Joint Body/Floating Path Cover

The same 250 pages were decoded through a shared degree-one acyclic path cover.
This filters contradictory raw edges and scores only selected graph relations:

| Mode | Correct / selected / labels | Precision | Recall | F1 | Cycles rejected |
|---|---:|---:|---:|---:|---:|
| Native ranker | 8667 / 9481 / 10465 | 0.91414408 | 0.82818920 | 0.86904643 | 18 |
| Native + calibrated global role | 8951 / 9746 / 10465 | 0.91842807 | 0.85532728 | 0.88575528 | 3 |
| Native + trained floating | 8983 / 9758 / 10465 | 0.92057799 | 0.85838509 | 0.88839440 | 2 |

The trained mode protects and retains all 209 high-precision zero-OOD floating
edges. It rejects 14 outgoing conflicts and 1,190 incoming conflicts across the
corpus. The constrained F1 gain is real diagnostic evidence that body and float
relations can share one graph, but it is not a runtime promotion: the corpus has
oracle layout anchors, and the strict gate still has to survive noisy and real
provider inputs.

### Deterministic Layout/OCR Noise Sensitivity

The fixed 250 pages were replayed under the predefined synthetic profiles. Mild
retains 11,267/11,369 elements and 10,276/10,465 resolvable labels; stress
retains 11,028/11,369 elements and 9,829/10,465 labels.

| Profile and mode | Raw F1 | Joint F1 | Joint P/R | Protected | Strict P | Strict zero-OOD P |
|---|---:|---:|---:|---:|---:|---:|
| Mild native | 0.81361888 | 0.84100588 | 0.89577708 / 0.79254658 | 0 | n/a | n/a |
| Mild trained floating | 0.82955665 | 0.85764341 | 0.90055713 / 0.81863354 | 170 | 0.96571429 | 0.96551724 |
| Stress native | 0.59203145 | 0.59914677 | 0.71294831 / 0.51667463 | 0 | n/a | n/a |
| Stress trained floating | 0.60318967 | 0.61366500 | 0.71923966 / 0.53511706 | 101 | 0.93495935 | 0.96551724 |

Trained floating evidence retains a positive joint-order delta under both
controlled profiles, but neither mild nor stress strict precision reaches the
`0.97` promotion target. All six mild strict errors touch audit-conflict
graphicals; only two of eight stress errors do, so high-noise failures cannot be
explained away as label ambiguity. Absolute stress performance also collapses,
with 2,698 fragmented elements and 341 dropped elements. The next reliability
layer was therefore fitted only on train-derived perturbations.

#### Noise-Aware Abstention A/B

Official train text blocks are reconstructed from their exact line polygons.
Four document-hash cross-fit pair models produce 15,413 held-out correctness
records across clean/mild/stress views. A standardized L2 logistic forecaster
uses 12 domain-general score, assignment-stability, OOD, and page-size features;
it excludes raw coordinates, caption text, profile identity, and labels. The
noise-aware gates are conjunctive, so the forecaster can reject an old-gate edge
but cannot admit one. The final model is byte-deterministic with SHA-256
`8fbd68a177b978f23759290a4cc6eaa24586c7a2e3316407377a3135f3f719b1`.

| Train calibration profile | Review correct/predicted | Review P/R | Strict correct/predicted | Strict P/R |
|---|---:|---:|---:|---:|
| Clean | 996 / 1048 | 0.95038168 / 0.68879668 | 856 / 880 | 0.97272727 / 0.59197787 |
| Mild | 879 / 925 | 0.95027027 / 0.60788382 | 748 / 767 | 0.97522816 / 0.51728907 |
| Stress | 621 / 653 | 0.95099541 / 0.42946058 | 514 / 529 | 0.97164461 / 0.35546335 |

The correctness thresholds are `0.29` for review and `0.44` for strict, after
the original confidence/margin gates. They were frozen before the following
test replay:

| Test profile | Old strict | Noise-aware strict | Precision delta | Strict errors on audit conflicts | Noise-aware joint F1 |
|---|---:|---:|---:|---:|---:|
| Clean | 196 / 201 | 192 / 195 | 0.97512438 -> 0.98461538 | 3 / 3 | 0.88839440 |
| Mild | 169 / 175 | 163 / 167 | 0.96571429 -> 0.97604790 | 4 / 4 | 0.85784363 |
| Stress | 115 / 123 | 109 / 116 | 0.93495935 -> 0.93965517 | 1 / 7 | 0.61366500 |

Review filtering keeps the same 235 clean, 198 mild, and 133 stress correct
edges while removing one, one, and two errors respectively. The noise-aware
protected path cover is unchanged on clean/stress and recovers two mild
relations. Stress remains far below the promotion target, with six strict
errors outside the audit-conflict set. These synthetic results do not establish
real OCR robustness and `runtime_reorder` remains false.

### Real PaddleOCR-VL and Docling Anchors

PaddleOCR-VL 1.6 and Docling 2.111.0 with Tesseract 4.1.1 were run on the fixed
first five rendered pages of `1401.3699`. This prefix is mostly single-column:

| Provider | Oracle anchors | Provider anchors | Relation correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Docling | 220/224 | 62/66 | 201 / 215 / 207 | 0.93488372 | 0.97101449 | 0.95260663 |
| PaddleOCR-VL 1.6 | 224/224 | 63/66 | 207 / 219 / 207 | 0.94520548 | 1.00000000 | 0.97183099 |

Both find 2/2 oracle figures. Docling emits two correct explicit float edges;
Paddle emits no explicit relations. The noise-aware layer keeps both correct
Paddle trained review edges, while Docling has no edge in that tier; neither
provider has a strict edge on this prefix. Combined F1 does not change. The
graphical-label audit finds 2/2 exact geometry agreements and no official-label
conflict.

The v2 provider report decomposes recognition/layout degradation without using
relation labels. Counts use their natural denominators: anchors for
missing/hallucination, semantic units for split/merge, and matched provider
groups for size error.

| Provider | Missing | Hallucination | Nested graphical OCR | Split | Merge | Size error | Character similarity | Token F1 | Caption prefix | Nearest synthetic / distance |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 4/224 | 2/66 | 2/66 | 4/56 | 0/66 | 7/62 | 0.74785572 | 0.71956298 | 0/2 | mild / 0.30899512 |
| PaddleOCR-VL 1.6 | 0/224 | 1/66 | 2/66 | 4/56 | 0/66 | 8/63 | 0.85877792 | 0.88688310 | 2/2 | mild / 0.05474538 |

Both providers have low page-normalized localization error (p90 center/edge
error `0.00393755/0.00394033` for Docling and
`0.00415761/0.00564990` for Paddle). Paddle's much smaller profile distance and
stronger text fidelity show that the synthetic mild family is a useful local
description for this prefix. Docling's nominally nearest profile is still far
away, driven mainly by OCR text loss and both caption prefixes being corrupted;
the profile name alone must not be read as a calibrated domain label.

A fixed complex page, `1412.1395` p. 4, contains two columns, two interleaved
figures/captions, code, a full-width top diagram, and body text:

| Provider | Anchor recall | Figure recall | Relation precision | Relation recall | Relation F1 |
|---|---:|---:|---:|---:|---:|
| Docling | 43/49 | 2/2 | 0.69047619 | 0.74358974 | 0.71604938 |
| PaddleOCR-VL 1.6 | 44/49 | 1/2 | 0.72093023 | 0.79487179 | 0.75609756 |

The degradation report changes the interpretation of Docling's 70 text/caption
anchors. Fifty-three of its 74 total provider anchors are small text boxes
nested inside the two oracle figures, mostly chart/diagram OCR, rather than
hallucinated body regions. After separating that useful nested layer, genuine
hallucination is 9/74 and missing is 6/49. Paddle has no nested graphical OCR on
this page; it has 2/14 hallucinations, 4/49 missing anchors, and stronger text
fidelity, but merges two of 14 provider units and misses one figure. The raw
scores above remain unchanged. However,
both pinned Comp-HRDoc unified annotations and `test_eval/1412.1395.json` bind
the top Figure 1 box to the lower “Fig. 2” caption and the lower Figure 2 box to
the upper “Fig. 1” caption. The answer-free local-geometry audit therefore marks
2/2 official graphical labels as conflicts; it does not replace them.

Docling's explicit edge and both trained-provider edges agree with the local
geometry proposal on 1/1 predicted edge (precision `1.0`, recall `0.5` over two
geometry proposals). Docling's trained edge does not pass the new strict gate;
Paddle's does pass both the old and noise-aware strict gates, but remains wrong
under the crossed official raw label. This corrects the earlier “provider
mispair” diagnosis:
provider recognition and text ordering remain weak on the page, but the observed
float penalty comes from an audited oracle conflict rather than duplicate
graphical-anchor assignment. The edge remains review-only and does not reorder
runtime output.

| Provider | Missing | Hallucination | Nested graphical OCR | Split | Merge | Type error | Character similarity | Token F1 | Caption prefix | Nearest synthetic / distance |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 6/49 | 9/74 | 53/74 | 3/17 | 2/74 | 0/43 | 0.70025503 | 0.64571963 | 1/2 | mild / 0.20701574 |
| PaddleOCR-VL 1.6 | 4/49 | 2/14 | 0/14 | 3/17 | 2/14 | 1/45 | 0.75789504 | 0.76982884 | 2/2 | mild / 0.12464003 |

Docling is almost equally distant from clean, mild, and stress
(`0.21559071/0.20701574/0.21405694`), which is direct evidence that the current
synthetic perturbations do not model its figure-internal OCR family. These
diagnostics therefore remain benchmark evidence only; no threshold, relation
label, or runtime gate was changed after observing this page.

## Train-Only Multi-Column Provider Calibration

The earlier five-page prefix is mostly single-column and belongs to the public
test split. A separate command now reconstructs a deterministic provider corpus
from the official Comp-HRDoc **train** annotations and the original arXiv PDFs:

```bash
scriptorium fetch-comphrdoc-provider-calibration \
  --sample-count 8 --document-count 4 --calibration-fraction 0.2 \
  --out-dir data/external/comphrdoc-provider-calibration
```

The command verifies the pinned annotation archive SHA-256, downloads source
PDFs only for local reconstruction, and records each source URL and PDF hash.
Scriptorium does not redistribute those PDFs; each paper keeps the license from
its arXiv record. Selection reads only `bbox`, `category_id`, and
`textline_polys`. It does not read `reading_order_id`, `reading_order_label`, or
`ro_linkings`. Documents are assigned by SHA-256 to disjoint fit/calibration
partitions, so pages from one paper cannot cross the split.

The fixed corpus contains six fit pages from `1710.06349`, `1609.04214`, and
`1709.05631`, plus two calibration pages from `1702.07651`. Each partition has
both plain multi-column and graphical multi-column pages. Providers receive
only rendered images; layout anchors are used only for matching, and semantic
sidecars are opened only by the scorer.

Run each provider once per manifest image, keeping the sample id in the output
file name, then score the directory. For the fast layout-only path:

```bash
scriptorium run-paddle-layout \
  data/external/comphrdoc-provider-calibration/images/1710.06349_4.png \
  --input-kind image --device cpu \
  --output outputs/pp-doclayoutv3/1710.06349_4.structure.json

scriptorium benchmark-provider-anchor-suite \
  data/external/comphrdoc-provider-calibration \
  outputs/pp-doclayoutv3
```

The table reports micro relation F1 after provider anchors and serialized order
edges are mapped to the answer-separated oracle. PaddleOCR-VL and PP-Structure
were run on one graphical page per partition; their two-page numbers are not
direct full-corpus comparisons with the eight-page providers.

| Provider | Pages | Fit F1 | Calibration F1 | Overall F1 | Capability |
|---|---:|---:|---:|---:|---|
| PaddleOCR-VL 1.6 | 2 | 0.94193548 | 0.83969466 | 0.89510490 | OCR + layout/order |
| PP-StructureV3 lightweight | 2 | 0.96774194 | 0.79069767 | 0.88732394 | OCR + layout/order |
| PP-DocLayoutV3 | 8 | 0.89882353 | 0.87248322 | 0.89198606 | layout/order, no text recognition |
| Docling 2.111.0 | 8 | 0.88119954 | 0.84415584 | 0.87148936 | OCR + layout/order |

On the same eight pages, PP-DocLayoutV3 is non-negative against Docling on all
pages, positive on seven, and tied on one. Mean per-page F1 delta is
`+0.01846737`, micro F1 delta is `+0.02049670`, and a paired page bootstrap gives
95% interval `[+0.00832645, +0.02927668]`; all four document-level mean deltas
are positive. Graphical multi-column micro F1 remains the harder stratum
(`0.82627119` for PP-DocLayoutV3 and `0.80081301` for Docling) than multi-column
without graphicals (`0.93786982` and `0.92240117`).

Observed CPU costs are operational measurements, not a controlled speed
benchmark: PP-DocLayoutV3 took about `8.48 s/page` in a controlled single-page
probe at roughly `1.28 GB`; Docling took about `23 s/page`; lightweight
PP-Structure took `148-316 s/page`; PaddleOCR-VL took roughly
`682-2193 s/page`, including model and cold-start variation. Disabling redundant
orientation, unwarping, and text-line-orientation preprocessing reduced the
PP-Structure fit-page run from `334 s` to `148 s` while relation F1 changed from
`0.9333` to `0.9677`. Those stages remain available through explicit flags for
rotated or photographed pages.

PP-DocLayoutV3 declares `text_recognition: false`. Its zero text fields are
therefore marked not applicable, and character/token/caption metrics are
excluded from synthetic-profile distance; the remaining nine layout features
still apply. All outputs retain `review-only`, `runtime_reorder: false`, package
versions, model options, input hashes, and capability provenance. Eight
train-only pages are useful evidence, not enough domain coverage to promote a
provider into runtime ordering. No runtime threshold was changed from these
results.

### 32-Page Granularity Audit and Independent Test Gate

The eight-page result combined oracle-line edges inside one Provider paragraph
with actual cross-block ordering. The expanded corpus fixes 16 train documents,
24 fit pages, and eight calibration pages; both partitions contain
`multicolumn` and `graphical-multicolumn` pages. arXiv sources use `v1` so later
revisions cannot silently move or remove annotated pages. A local annotation
archive can be reused, but its pinned SHA-256 is still verified:

```bash
scriptorium fetch-comphrdoc-provider-calibration \
  --sample-count 32 --document-count 16 --calibration-fraction 0.2 \
  --arxiv-version v1 --annotation-archive /path/to/CompHRDoc.zip \
  --out-dir data/external/comphrdoc-provider-calibration-32

scriptorium run-paddle-layout-corpus \
  data/external/comphrdoc-provider-calibration-32 \
  --out-dir outputs/pp-doclayoutv3-calibration-32 --partition all --device cpu

scriptorium benchmark-provider-anchor-suite \
  data/external/comphrdoc-provider-calibration-32 \
  outputs/pp-doclayoutv3-calibration-32 \
  --output outputs/pp-doclayoutv3-calibration-32/suite.json
```

Suite schema v7 separates three granularities. `within-anchor` is an oracle-line
edge ordered geometrically inside one model block; `direct inter-anchor` is the
actual transition between adjacent model blocks. The old relation summary treats
every prediction as scorable and is therefore only a raw exact-match view:

| Metric | 24 fit pages | 8 calibration pages | 32 overall pages |
|---|---:|---:|---:|
| Serialized aggregate F1 | 0.90329611 | 0.84264832 | 0.88870500 |
| Within-anchor precision | 1389/1396 = 0.99498567 | 440/443 = 0.99322799 | 1829/1839 = 0.99456226 |
| Raw direct inter-anchor precision | 237/306 = 0.77450980 | 50/102 = 0.49019608 | 287/408 = 0.70343137 |
| Partial-label-aware direct precision | 237/278 = 0.85251799 | 50/84 = 0.59523810 | 287/362 = 0.79281768 |
| Unscored direct transitions | 28 | 18 | 46 |

Each direct transition records the minimum detection confidence of its two
Provider endpoints and the exact answer-free native candidates that emit the
same direct successor. Suite v8 exposes four observable channels: `visual-yx`,
`box-flow`, non-trivial `recursive-xy-cut` tree edges, and selected
`relation-graph` max-regret path-cover edges. Candidate provenance is retained
separately from the subset approved for a gate. Eligibility is computed from
those fields before the scorer opens the semantic sidecar; changing relation
labels cannot change eligible edges. The curve also reports a 95% Wilson
precision lower bound. Comp-HRDoc `ro_linkings` are partial labels, so review v3
scores an edge only when both endpoints occur in the relation endpoint universe;
other selected edges are `unscored`. Gates also require a minimum
`scorable_fraction`.

On the same 32-page fit/calibration suite, the endpoint-aware legacy v1 gate is
`native support >= 1 && confidence >= 0.5`: fit is
`230/237 = 0.97046414`, Wilson `0.94029969`, with 26 unscored transitions;
calibration is `46/48 = 0.95833333`, Wilson `0.86024344`, but scorable fraction
is only `0.76190476`. This gate is retained only to rescore the already opened
historical test window and cannot authorize runtime behavior:

```bash
scriptorium freeze-provider-transition-gate \
  outputs/pp-doclayoutv3-calibration-32/suite.json \
  --partition fit --minimum-precision 0.95 \
  --minimum-wilson-lower-95 0.90 --minimum-predicted 50 \
  --output outputs/pp-doclayoutv3-transition-gate.json
```

Independent validation uses a separate document-hash selection from official
Comp-HRDoc test annotations: 16 documents and 32 pages, with 17 graphical
multi-column and 15 multi-column pages. Selection still does not read relation
labels. With latest arXiv sources, annotation/PDF token-alignment F1 is at least
`0.69785276` and averages `0.90739489`; the Provider still sees only rendered
images. The test run only loads the frozen gate and never searches thresholds:

```bash
scriptorium fetch-comphrdoc-provider-test \
  --sample-count 32 --document-count 16 \
  --annotation-archive /path/to/CompHRDoc.zip \
  --out-dir data/external/comphrdoc-provider-test-32
scriptorium run-paddle-layout-corpus \
  data/external/comphrdoc-provider-test-32 \
  --out-dir outputs/pp-doclayoutv3-test-32 --partition all --device cpu
scriptorium benchmark-provider-anchor-suite \
  data/external/comphrdoc-provider-test-32 outputs/pp-doclayoutv3-test-32 \
  --transition-gate outputs/pp-doclayoutv3-transition-gate.json
```

Unfiltered raw direct test transitions are `269/384 = 0.70052083`. The old
`209/219 = 0.95433790` report treated unlabelled edges as errors and is withdrawn
as a current metric. With identical candidates on the same already opened test
window, endpoint-aware results are:

| Independent test stratum | Eligible | Scorable / unscored | Correct / scorable | Precision | Wilson lower 95% |
|---|---:|---:|---:|---:|---:|
| Aggregate | 284 | 268 / 16 | 256/268 | 0.95522388 | 0.92337855 |
| Graphical multi-column | 91 | 84 / 7 | 81/84 | 0.96428571 | 0.90018306 |
| Multi-column | 193 | 184 / 9 | 175/184 | 0.95108696 | 0.90966772 |

FocalOrder's positional-disparity result also motivates a veto-only post-hoc
audit. Test start, middle, and end bands reach `72/78`, `91/92`, and `93/98`,
with Wilson bounds `0.84216770`, `0.94097214`, and `0.88607548`. Start and end
still fail, so this window remains veto-only.

The next train-only suite expands to 64 pages and 32 documents: 25 fit documents
and 7 calibration documents. Gate v4 separates all observable candidates from
the candidates allowed to contribute support. Its default support set is the
previously calibrated `visual-yx`, `box-flow`, and selected `relation-graph`
edges; `recursive-xy-cut` remains observable but does not count as an independent
vote. Every rule still requires at least two support votes and five-fold
document-grouped OOF selection:

```bash
scriptorium freeze-stratified-provider-transition-gate \
  outputs/pp-doclayoutv3-calibration-64/suite-v8-structural-edges.json \
  --minimum-native-support 2 --cross-validation-folds 5 \
  --output outputs/pp-doclayoutv3-transition-gate-v4.json
```

Full-fit rules reach `234/237 = 0.98734177`. Aggregate OOF is
`192/195 = 0.98461538`, Wilson `0.95575171`, but folds 2 and 3 are each `30/31`
with Wilson `0.83805895`. `graphical-multicolumn/middle` has only 18 scorable
predictions; `multicolumn/start` is only `8/9`, Wilson `0.56500029`, with `0.75`
scorable fraction. Calibration is `21/21`, but Wilson `0.84536098 < 0.85` and
`21 < 30`. Requiring unanimous support 3 leaves no fit bucket with 20 examples.
Gate v4 reproduces these three-channel results exactly. Enabling all four
channels explicitly keeps full-fit at `234/237`, but OOF becomes
`173/176 = 0.98295455` and calibration becomes `23/24 = 0.95833333`, Wilson
`0.79758194`. The added calibration error is supported only by `visual-yx` and
`recursive-xy-cut`, confirming that the latter is correlated geometry evidence,
not an independently calibrated vote. A strict `visual-yx + box-flow` control
with support 2 produces no qualified fit bucket. Gate v4 is therefore still
`document-cross-validation-rejected-review-only`; the fourth test window was
not opened. Legacy v2/v3 gates retain their original support counts, while v4
fails closed if candidate-level provenance is missing during filtering.

### Chunkr Cross-Domain Reading-Order Development Benchmark

Chunkr Reading Order Bench OSS provides a permissively licensed, cross-domain
COCO corpus covering financial, legal, government, research, magazine, and
other document pages. Scriptorium pins revision
`d6b5ddf06a6479a42bb0b33c243801171e042fc7` and annotation SHA-256
`93974a16cb43a44656f293b933abd1a713d2bff2bfa71cd7b74987edb26bdbfa`.
The pinned file contains 733 pages and 9,267 layout elements. These source-file
counts are authoritative for this run; they are not copied from the evolving
dataset-card summary.

The standard COCO records do not contain a separate `reading_order` property.
The published sequence is encoded by contiguous ascending annotation ids within
each image. The loader rejects missing, duplicate, non-contiguous, out-of-order,
unknown-category, and out-of-page records. Before candidate inference, anchors
are reordered by a SHA-256 fingerprint of category and bbox; annotation ids are
excluded. This prevents the answer sequence from becoming a stable-sort
tie-break. The corpus is development-only and cannot authorize runtime reorder.

```bash
scriptorium fetch-chunkr-reading-order \
  --out-dir data/external/chunkr-reading-order

scriptorium benchmark-chunkr-reading-order \
  data/external/chunkr-reading-order/_annotations.coco.json \
  --output outputs/chunkr-reading-order/report.json
```

| Order candidate | Exact pages | Pairwise accuracy | Successor accuracy | Complex-page exact / pairwise |
|---|---:|---:|---:|---:|
| Selected `auto` | 449/733 = 0.61255116 | 0.87452713 | 0.75041012 | 136/331 = 0.41087613 / 0.86935761 |
| Visual Y/X | 484/733 = 0.66030014 | 0.84918215 | 0.72099836 | 166/331 = 0.50151057 / 0.84227794 |
| Box-flow | 163/733 = 0.22237381 | 0.85557652 | 0.53808296 | 28/331 = 0.08459215 / 0.85872998 |
| Recursive XY-cut | 484/733 = 0.66030014 | 0.85826842 | 0.72931802 | 167/331 = 0.50453172 / 0.85193170 |
| Relation graph | 342/733 = 0.46657572 | 0.81817613 | 0.70635107 | 94/331 = 0.28398792 / 0.81279666 |

The selected algorithm improves long-range pairwise and local-successor quality
over visual Y/X, but lowers whole-page exact match and position accuracy. This
is a concrete optimization target: future arbitration must retain the pairwise
gain without changing already-correct simple pages.

| Direct edge evidence | Precision | Recall | F1 |
|---|---:|---:|---:|
| Visual Y/X | 0.72099836 | 0.72099836 | 0.72099836 |
| Box-flow | 0.53808296 | 0.53808296 | 0.53808296 |
| Non-trivial recursive XY-cut edges | 0.78614579 | 0.45746426 | 0.57837037 |
| Selected relation-graph edges | 0.80938502 | 0.65080853 | 0.72148610 |
| Stable three-channel support >= 2 | 0.91591928 | 0.67014296 | 0.77398836 |
| All four channels support >= 2 | 0.82393096 | 0.74958988 | 0.78500430 |

The independent domain mix confirms the Provider result: adding XY-cut raises
coverage and slightly raises F1, but drops support-2 precision by more than nine
points. It remains audit-only. The first run also exposed and fixed a generic
ownership bug where grid-island elements classified as sidebars were emitted
twice; the corrected order is now always a complete permutation on all 733
pages.

The isolated learned candidate uses 68 role, normalized-geometry, and candidate-
rank features with a bidirectional pairwise classifier and Borda decoding. Five
category/complexity-stratified SHA-256 folds keep whole pages together. The
upstream corpus does not publish document ids, so this is page-level OOF
development evidence, not document-level cross-validation and not a held-out
test claim.

```bash
python -m pip install -r requirements-relation-ranker.txt

scriptorium train-chunkr-order-ranker \
  data/external/chunkr-reading-order/_annotations.coco.json \
  --cross-validation-folds 5 \
  --output outputs/models/chunkr-order-ranker.joblib

scriptorium benchmark-chunkr-order-ranker-roor \
  data/external/roor-validation-full-v1 \
  --model outputs/models/chunkr-order-ranker.joblib \
  --output outputs/chunkr-order-ranker-roor.json
```

| Chunkr OOF candidate | Exact match | Position accuracy | Pairwise accuracy | Successor accuracy |
|---|---:|---:|---:|---:|
| Learned pairwise ranker | 515/733 = 0.70259209 | 0.72170066 | 0.93686112 | 0.74349660 |
| Selected `auto` | 449/733 = 0.61255116 | 0.67109097 | 0.87452713 | 0.75041012 |
| Visual Y/X | 484/733 = 0.66030014 | 0.67400453 | 0.84918215 | 0.72099836 |

The learned ranker improves whole-page exact, position, and long-range pairwise
metrics in-domain, but loses 59 correct adjacent successors relative to selected
`auto`. Against selected order it gains 87 exact pages and loses 21, while
successor correctness is better on 142 pages and worse on 105. This is evidence
for a global-ranking research candidate, not for replacing local reading
streams.

Unchanged replay on all 49 answer-separated ROOR validation pages reverses the
result. The benchmark completes every prediction before opening any semantic
sidecar; structure and label files are path-confined and independently hashed.

| ROOR 49-page candidate | Direct relation recall | Precedence accuracy |
|---|---:|---:|
| Learned Chunkr ranker | 500/2612 = 0.19142420 | 2013/2612 = 0.77067381 |
| Selected `auto` | 1217/2612 = 0.46592649 | 2173/2612 = 0.83192956 |
| Visual Y/X | 1390/2612 = 0.53215926 | 2317/2612 = 0.88705972 |

The learned candidate is worse than selected on 47/49 pages for direct
relations and 35/49 for precedence. Chunkr contains coarse mixed-role blocks;
ROOR exposes fine all-text lines. A page-profile envelope over element count,
box-size quantiles, role mix, and candidate disagreement marks all 49 ROOR pages
outside the Chunkr training envelope. That `49/49` rejection was added after
this granularity failure was observed, so it is a diagnostic for the opened
window, not independently validated OOD calibration. The model remains
`runtime_reorder: false`, `candidate_consensus_policy: isolated`, with promotion
rejected. The next experiment must freeze a hierarchical coarse-block-then-line
contract and validate it on an unopened document family rather than tuning this
flat ranker against ROOR.

### Hierarchical Proposal Coverage Audit

The frozen hierarchy contract is now implemented as an isolated proposal path.
It can be replayed directly from one `DocumentIR` page and provider structure
JSON without copying provider sequence or relation answers into the ordering
candidate:

```bash
scriptorium build-hierarchical-order \
  outputs/research/attention-pp-structure-block-transitions-v3/native-only/cases/attention-is-all-you-need/document.ir.json \
  --structure-json outputs/research/pp-structure-attention-page-1/page_0001_res.json \
  --page-index 0 \
  --output /tmp/attention-hierarchy.proposal.json
```

PP parent blocks and OCR lines are normalized as separate granularity classes.
The adapter keeps only true coarse provider blocks, then compares the original
geometry-only membership with exact/contained-text plus local-spatial evidence:

| Page/provider | Fine elements | Normalized / selected regions | Assigned | Unassigned | Non-empty regions | Eligible cross transitions |
|---|---:|---:|---:|---:|---:|---:|
| Attention p. 1 / PP-Structure | 56 | 61 / 9 | 47 -> 52 | 9 -> 4 | 6 -> 9 | 1 -> 6 |
| BYD annual report p. 136 / PP-Structure | 34 | 61 / 17 | 29 -> 33 | 5 -> 1 | 11 -> 15 | 7 -> 13 |
| JD image source / Docling | 64 | 93 / 93 | 49 -> 53 | 15 -> 11 | 31 -> 37 | 16 -> 20 |

This is a **coverage audit**, not a labelled reading-order benchmark. It proves
that the adapter can recover more plausible region membership across a paper,
a Chinese financial report, and an image-source portal without lowering the
global geometry threshold. It does not prove that the additional within-region
or cross-region edges are correct. All emitted edges remain review-only, and
incomplete region chains suppress candidate expansion. Independent labels must
score within-region successors and cross-region transitions separately before
any promotion decision.

### Answer-Separated Hierarchical Relation Benchmark

The labelled hierarchy benchmark now materializes 64 official Comp-HRDoc
**train** pages from 32 documents: 50 fit pages, 14 calibration pages, 32
graphical multi-column pages, 30 multi-column pages, and 2 graphical pages.
Document-hash partitioning prevents pages from one paper crossing fit and
calibration. `block_id` is used only while materializing oracle coarse-region
geometry and membership labels; member ids, `ro_linkings`, provider sequence
values, and relation values never enter inference input. The evaluator predicts
every page before resolving or opening any label path, verifies both input and
label SHA-256 values, and keeps proposals review-only:

```bash
scriptorium materialize-comphrdoc-hierarchy \
  /tmp/scriptorium-comphrdoc-provider-calibration-64-v1 \
  --output /tmp/scriptorium-hierarchy-train64-v1
scriptorium benchmark-hierarchical-order-corpus \
  /tmp/scriptorium-hierarchy-train64-v1 \
  --output /tmp/scriptorium-hierarchy-train64-boundary-text-report-v1.json
```

The previous control forced one adjacency chain over coarse regions. The new
default keeps selected fine relation-graph edges as a partial DAG. It retains
all cross-region edges as evidence, emits a transition only when the edge joins
the source local-stream tail to the target local-stream head, enforces one
region predecessor/successor, and removes the lowest-regret edge that would
close a region cycle. A member-completion region sequence remains diagnostic;
`total_order_asserted` is false and `runtime_reorder` remains false.

One non-iterative membership refinement handles exact geometry ties only when
both untied relation-graph neighbors and both selected-order neighbors already
belong to the same tied candidate region. It repairs 5 fit and 3 calibration
memberships without using the newly assigned members for further propagation.

A second non-iterative rule handles the complementary boundary split. Untied
relation and selected-order neighbors must both form the same `A -> element ->
B` region pattern. The element must contain at least four normalized
alphanumeric characters, and exactly one region inside the original geometry
tie must contain that text. The unique text parent must be either `A` or `B`.
This resolves 6 fit and 7 calibration memberships, all correctly; neither these
members nor the earlier continuity repairs can propagate another assignment.

| Metric | Former coarse chain | Relation DAG + continuity | Boundary-text current | Flat selected baseline |
|---|---:|---:|---:|---:|
| Membership recall / coverage | 0.99353243 | 0.99505421 | 0.99752711 | n/a |
| Within-region successor F1 | 0.98901099 | 0.99188544 | 0.99297033 | 0.98359865 |
| Line cross-region F1 | 0.76518219 | 0.92624585 | 0.93473962 | 0.92146597 |
| Region transition F1 | 0.72936660 | 0.89761751 | 0.90607029 | 0.86111111 |

The current fit/calibration line-transition F1 values are
`0.93802345/0.92260062`; region-transition F1 values are
`0.90835361/0.89759036`. Calibration region F1 now exceeds its flat control
`0.88563050`, while calibration line F1 remains `0.00619195` below the flat
`0.92879257`; runtime promotion therefore remains disabled. Graphical
multi-column line/region F1 reaches `0.90609555/0.85007728`, equal to the flat
line control and above its `0.81049563` region control. Plain multi-column
reaches `0.95828636/0.95291480`. Fit/calibration within-region F1 reaches
`0.99191794/0.99642675`; membership remains zero-wrong with unassigned elements
reduced from 34 to 13.

The report aggregates 8 interior continuity and 13 boundary-text membership
repairs, 5,150 selected fine edges, 972 cross-region evidence edges, 905
boundary-aligned candidates, 67 non-boundary evidence edges, 9 tied
cross-region edges, and 3 region-cycle suppressions. It emits 902 acyclic
review transitions. The changed cross-region evidence count is an expected
consequence of resolving membership before classifying selected fine edges.
The frozen Chunkr ranker remains an explicit A/B control; its OOD suppression
leaves region-transition F1 at `0.15531915` on the earlier identical corpus.

Two broader experiments were rejected. Splitting every region stream around
all non-boundary relation edges raises fit line F1 `0.93265993 -> 0.94176373`,
but removes 25 correct within-region edges, lowers within F1
`0.99097698 -> 0.98873592`, and creates one cycle. Combining non-boundary
relation edges with flat adjacency raises endpoint-aware partial line F1 to
`0.93891213`, but 22 of 29 additions are unscored and region F1 falls
`0.90301548 -> 0.89402390`. Neither experiment is implemented.

The frozen current predictions leave 55 fit truth edges: 30 are absent from
both relation and base order, 12 are exact non-boundary relation edges, 9 touch
an unassigned member, and 4 are base-only. Calibration leaves 15: 8 absent
from both sources, 5 base-only, and 2 membership-unassigned; no calibration
truth edge is recoverable by relaxing the non-boundary gate. A fit-only audit
of all 74 base-boundary candidates absent from selected relation evidence finds
only 3 exact truth edges. Even the four candidates supported simultaneously by
visual Y/X, box-flow, and recursive XY-Cut contain zero truth edges. More
geometry voting is therefore rejected; the next useful evidence must be an
explicit provider relation/stream or an independently trained semantic
successor scorer.

The frozen implementation was then replayed, without tuning, on the same real
provider inputs used by the earlier coverage audit:

| Page/provider | Assigned | Former chain transitions | Cross evidence / boundary / emitted | Non-boundary / tied |
|---|---:|---:|---:|---:|
| Attention p. 1 / PP-Structure | 52 | 6 | 9 / 3 / 3 | 6 / 0 |
| BYD annual report p. 136 / PP-Structure | 33 | 13 | 14 / 9 / 9 | 5 / 0 |
| JD image source / Docling | 108 | 20 | 27 / 11 / 11 | 16 / 1 |
| PUMA annual report p. 5 / PP-Structure | 23 | 7 | 6 / 6 / 6 | 0 / 0 |

These four rows are structural diagnostics, not accuracy: JD, PUMA, and BYD do
not provide complete human relation labels for this replay. They show that the
new path abstains from unsupported page-wide adjacency and preserves rejected
non-boundary evidence for review. Continuity repair triggers zero times on all
four replays. The new boundary-text rule also triggers zero times on the four
current provider replays, so it does not expand those unlabelled pages. No new
Comp-HRDoc test window was opened and the promotion decision remains
`development-benchmark-only-review-only`.

The representation follows the EMNLP 2024 ordering-relations result and the
official ROOR implementation, which model complex layouts as immediate
successor relations rather than one permutation. Docling's current rule-based
predictor likewise builds directional maps over page elements; its open
discussion about many small orphan clusters reinforces the need for explicit
granularity and abstention. XY-Cut++ independently supports multi-granularity
segmentation plus lightweight semantic/geometric matching. GraphDoc supports a
joint graph representation for order, hierarchy, and reference relations, but
its MIT repository still lists dataset, model, and code releases as TODO, so it
is research direction rather than an available dependency.

- Ordering relations paper: https://aclanthology.org/2024.emnlp-main.540/
- Official ROOR implementation: https://github.com/chongzhangFDU/ROOR
- Docling reading-order implementation: https://github.com/docling-project/docling-ibm-models/blob/73cf24d321f74f77de5f974e6c048da0e1512a3d/docling_ibm_models/reading_order/reading_order_rb.py
- Relation-graph/max-regret path-cover analysis: https://arxiv.org/html/2607.01018
- XY-Cut++ hierarchical/cross-modal ordering: https://arxiv.org/abs/2504.10258
- GraphDoc relation-graph project: https://github.com/yufanchen96/GraphDoc

### Cached Semantic Successor Screening

The July 2026 max-regret study uses per-target-token conditional likelihood from
`EleutherAI/pythia-410M` plus BERT NSP `log p(IsNext)`, with fixed weights
`1.0/0.2`. It reports that sentence embeddings do not help and that dense
semantic scoring costs a mean `93.5 s/page` on an A40. Scriptorium therefore
first screened the Apache-2.0, 4.4M-parameter Google BERT-Tiny NSP checkpoint on
the existing answer-separated ROOR train partition. Its revision is pinned to
`30b0a37ccaaa32f332884b96992754e246e48c5f`; 402,395 unique directed text pairs
were cached, so repeated model training does not rerun the transformer.

On the 27 internal calibration documents, pure NSP is weak but non-random:
positive-pair mean probability is `0.73074756`, negative-pair mean is
`0.68807782`, top-1 source accuracy is `0.03766334`, and MRR is `0.11700226`.
Directly appending the score to the existing pair classifier is harmful:

| ROOR train internal calibration | Precision | Recall | F1 | Correct / predicted |
|---|---:|---:|---:|---:|
| Geometry/text-shape v2 top edge | 0.66132556 | 0.64857143 | 0.65488640 | 908 / 1,373 |
| Geometry/text-shape v2 + branch | 0.65991620 | 0.67500000 | 0.66737288 | 945 / 1,432 |
| Direct feature-26 Tiny NSP top edge | 0.65175953 | 0.63500000 | 0.64327062 | 889 / 1,364 |
| Direct feature-26 Tiny NSP + branch | 0.65618299 | 0.65571429 | 0.65594855 | 918 / 1,399 |

The direct fusion is rejected. A fit-only screen then fixed and implemented a
two-stage design: score only the geometry ranker's top five targets, combine
base probability, rank/margins, NSP relative scores, and the original pair
features, and select a threshold from five document-hash OOF folds. Fit
candidate recall is `0.94110838`; OOF F1 is `0.74600465` at the frozen `0.59`
threshold. The development calibration result improves further when the
existing branch gate is retrained over semantic rankings:

| ROOR train internal calibration | Precision | Recall | F1 | Correct / predicted |
|---|---:|---:|---:|---:|
| Frozen v4 semantic top edge | 0.70720372 | 0.65214286 | 0.67855816 | 913 / 1,291 |
| Frozen v4 semantic + branch | 0.70895522 | 0.67857143 | 0.69343066 | 950 / 1,340 |

The 250-page Comp-HRDoc cross-domain corpus was then replayed under a stricter
two-phase evaluator: all 500 mode predictions complete before any semantic
sidecar is opened. The v4 scorer improves every tracked body/path-cover metric:

| Comp-HRDoc 250 pages | v2 F1 | v4 semantic F1 | Delta |
|---|---:|---:|---:|
| Native ranker edges | 0.84129386 | 0.87467713 | +0.03338327 |
| Native ranker path cover | 0.86904643 | 0.90414107 | +0.03509464 |
| Plus structure-role edges | 0.85716953 | 0.89082846 | +0.03365893 |
| Plus structure-role path cover | 0.88501708 | 0.92115119 | +0.03613411 |

The report declares both answer-free inference input and
`labels_opened_after_all_predictions: true`. A second strict two-phase A/B then
used all 49 official ROOR validation pages, which are outside the train split:

| ROOR validation 49 pages | v2 F1 | v4 semantic F1 | Delta |
|---|---:|---:|---:|
| Top edge | 0.67510713 | 0.71032949 | +0.03522236 |
| Branch edges | 0.69167292 | 0.73061145 | +0.03893853 |
| Degree-one path cover | 0.68729852 | 0.71334792 | +0.02604940 |

### Hierarchy Semantic Conflict Arbitration

Naively appending semantic boundary edges is too permissive. Of 181 novel path
edges on the 64-page hierarchy corpus, 62 were boundary-aligned and 9 initially
filled empty region slots. Those additions gained one correct region relation
but no exact line edge, lowering calibration line F1. The retained algorithm was
selected on fit only: a semantic edge must conflict with exactly one selected
native region edge, exceed its confidence by at least `0.10`, and preserve the
region DAG. It replaces that edge one-for-one; it never adds transition count or
changes membership/within-region streams.

| Comp-HRDoc hierarchy | Native line / region F1 | Semantic arbitration | Replacements |
|---|---:|---:|---:|
| 50-page fit | 0.93802345 / 0.90835361 | 0.93969849 / 0.90997567 | 2 |
| 14-page calibration | 0.92260062 / 0.89759036 | 0.93209877 / 0.90690691 | 2 |
| 32-page official-test window | 0.94021102 / 0.91762014 | 0.94255569 / 0.91990847 | 2 |

On calibration, semantic line F1 exceeds the flat control `0.92879257` and
region F1 remains above `0.88563050`. The separate test window confirms positive
line and region deltas with prediction count, membership, and within-region F1
unchanged. At this stage its line F1 still trailed the test-window flat control
`0.94712644` by `0.00457075`; that isolated the next problem as hierarchy
endpoint structure rather than Tiny model capacity.

### Graphical Object Branch Endpoints

A fit-only residual audit found that graphical objects were being serialized as
through-path nodes. Among scorable fit transitions, every selected edge leaving
a `table` region (`7/7`) and every selected edge entering a `figure` region
(`5/5`) was wrong; none was correct. The corpus relations consistently express
the useful local branch in the other direction: figure object to caption, and
caption or body text to table object. This also matches the translation model:
an object and its caption are a bounded unit, not a bridge between unrelated
body streams.

Policy v4 therefore keeps `table` regions as terminal branch endpoints and
`figure` regions as root branch endpoints. A table-source or figure-target
candidate remains in `cross_region_relation_evidence` with an explicit
suppression reason, but cannot consume a region predecessor/successor slot.
The rule was frozen on fit, then replayed unchanged on calibration and the
previously separate official-test window:

| Comp-HRDoc hierarchy | Semantic line / region F1 | Object-branch v4 | Flat line / region control |
|---|---:|---:|---:|
| 50-page fit | 0.93969849 / 0.90997567 | 0.94843618 / 0.92727273 | 0.91950207 / 0.85475285 |
| 14-page calibration | 0.93209877 / 0.90690691 | 0.94968553 / 0.92638037 | 0.92879257 / 0.88563050 |
| 32-page official-test window | 0.94255569 / 0.91990847 | 0.94811321 / 0.93055556 | 0.94712644 / 0.90064795 |

The new line gate exceeds the independent flat control by `0.00098677`; the
region gate exceeds it by `0.02990761`. Correct transition counts remain
`561/151/402` on fit, calibration, and test. Membership and within-region
metrics are bit-for-bit unchanged. The three partitions suppress 28, 10, and 24
object-branch candidates respectively, while preserving each rejected edge for
review. This clears the previously recorded hierarchy accuracy gate without
adding a larger language model.

`runtime_reorder` remains false because this is an oracle-region relation
benchmark, not end-to-end proof over OCR-derived regions, and its output is a
partial DAG rather than a page permutation. The next promotion evidence must
show that the same branch contract survives provider-derived hierarchy on a
broader independent document family. BERT-Base and Pythia remain deferred.

### Provider-Derived Hierarchy and Unassigned Fallback

The end-to-end hierarchy benchmark now replaces oracle coarse regions with
PP-DocLayout provider blocks while retaining answer-free fine lines. The
materializer strips provider order and relation fields, writes every inference
input before opening any hierarchy label, and requires the provider-run corpus
manifest hash to match the upstream hierarchy corpus. Benchmark partitions can
be selected independently with `--partition` so fit, calibration, and test
predictions cannot share an evaluation pass:

```bash
scriptorium materialize-provider-hierarchy \
  /tmp/scriptorium-hierarchy-train64-v1 \
  /tmp/scriptorium-ppdoclayout-train64 \
  --output /tmp/scriptorium-provider-hierarchy-train64
scriptorium benchmark-provider-hierarchy \
  /tmp/scriptorium-provider-hierarchy-train64 \
  --partition calibration \
  --output /tmp/scriptorium-provider-hierarchy-calibration.json
```

Provider detectors require a lower membership coverage threshold than oracle
regions because their boxes frequently contain padding or split text. The
threshold was frozen at `0.10` with margin `0.10` from fit evidence. Policy v5
then restores only selected-native adjacencies for which at least one endpoint
has no provider membership. Consecutive unassigned elements become explicit
`unassigned-fallback` review streams. Boundary edges are emitted only into free
source/target degree slots and only when they preserve an acyclic graph.
Assigned-to-assigned flat fallback was rejected: its fit precision was only
`0.11515152`, versus `0.94339623` for the guarded unassigned adjacency family.

The provider metric is segmentation-invariant: it unions local-stream and
cross-stream successor edges and compares line-level relations, while reporting
assignment coverage and pairwise co-membership separately. Results below use
the same frozen detector and fallback policy on every partition:

| Provider hierarchy | Without fallback F1 | v5 fallback F1 | Flat F1 | Assignment coverage | Segmentation pair F1 |
|---|---:|---:|---:|---:|---:|
| 50-page fit | 0.94842599 | 0.97433893 | 0.94768195 | 0.96362287 | 0.67995655 |
| 14-page calibration | 0.93254330 | 0.96754386 | 0.97694650 | 0.95065789 | 0.65042468 |
| 32-page official-test window | n/a | 0.97016660 | 0.96606248 | 0.97687225 | 0.80643143 |

The independent test relation precision/recall is
`0.96979086/0.97054264`. The semantic ranker produces the same test result as
native v5 and only changes calibration F1 from `0.93254330` to `0.93345488`
before fallback, so the gain comes from explicit membership abstention rather
than language-model tuning. On the parallel oracle-region test, the same v5
rule raises line F1 `0.94811321 -> 0.95571096` and correct line edges
`402 -> 410`, while membership, region relations, and within-region metrics do
not change.

This is meaningful end-to-end provider evidence, but not a runtime promotion:
calibration remains below its flat control, pairwise provider segmentation is
still weak, and the output is a review-only partial relation graph. The next
work must improve provider region grouping or select between hierarchy and flat
relations using evidence independent of evaluation labels. The evaluation
design follows PRImA's correspondence-aware reading-order principle: score
relations after accounting for segmentation mismatch rather than requiring
identical region ids ([Clausner et al., ICDAR 2013](https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf)).

### Provider Continuity Segments v6

A fit-only residual audit found two provider-specific failure families. First,
one detector region could contain members separated in selected-native order by
another region; v5 still linked every member of that region. Correct fit links
were locally vertical, while errors were backward jumps or large vertical gaps.
Second, native relation-graph transitions spanning many selected-order
positions had weak precision. Policy v6 therefore:

- splits a provider region into local streams when a non-adjacent member pair
  falls outside `[-0.25, 1.25]` mean line heights of forward vertical
  continuity;
- keeps native cross-region candidates with selected-rank displacement above
  four as evidence-only, while leaving external semantic edges exempt;
- preserves every selected-native adjacent edge, all membership decisions, the
  base/candidate element orders, and `runtime_reorder: false`.

The thresholds were selected on fit only and frozen before calibration/test
replay. This follows the cluster-and-sort separation used by sparse graph
reading-order work ([Wang et al., ICDAR 2023](https://arxiv.org/abs/2305.02577))
and retains relation-DAG output rather than forcing one permutation.

| Provider hierarchy | v5 F1 | Continuity v6 precision / recall / F1 | Flat F1 | Splits / nonlocal suppressions |
|---|---:|---:|---:|---:|
| 50-page fit | 0.97433893 | 0.97905759 / 0.97471983 / 0.97688390 | 0.94768195 | 12 / 28 |
| 14-page calibration | 0.96754386 | 0.97966401 / 0.96768559 / 0.97363796 | 0.97694650 | 8 / 17 |
| 32-page official-test window | 0.97016660 | 0.97966367 / 0.97093023 / 0.97527740 | 0.96606248 | 18 / 28 |

The independent-test gain over v5 is `+0.00511080` and over flat is
`+0.00921492`. Fit improves on 15 pages, is unchanged on 32, and regresses on
3; calibration is `7/6/1`, with the only regression `-0.00063939`; test is
`9/22/1`, with the only regression `-0.00072107`. The oracle-region 32-page
control remains bit-for-bit at line/region F1
`0.95571096/0.93055556`, with zero provider-only suppressions.

The same v5/v6 code A/B was replayed on real complex pages without labels. It
is diagnostic, not correctness evidence:

| Real page/provider | v5 -> v6 streams | Within edges | Relation transitions | Fallback transitions | Split / nonlocal |
|---|---:|---:|---:|---:|---:|
| Attention p. 1 / PP-Structure | 11 -> 11 | 45 -> 45 | 3 -> 3 | 4 -> 4 | 0 / 0 |
| BYD annual report p. 136 / PP-Structure | 16 -> 16 | 18 -> 18 | 8 -> 7 | 1 -> 1 | 0 / 1 |
| JD homepage / Docling | 45 -> 53 | 89 -> 81 | 11 -> 5 | 10 -> 18 | 8 / 6 |
| PUMA annual report p. 5 / PP-Structure | 10 -> 10 | 15 -> 15 | 6 -> 6 | 1 -> 1 | 0 / 0 |

JD exposes the intended behavior: eight discontinuous provider chains become
separate translation/review streams, six unsupported long jumps abstain, and
new fallback transitions only restore native adjacencies touching unassigned
elements. Base and candidate orders remain identical between v5 and v6 on all
four pages. Calibration still trails flat by `0.00330854`, so provider grouping
and an answer-independent hierarchy/flat selector remain open work.

### Graph-Supported Native Adjacency v7

The remaining calibration deficit is recall-heavy, but reopening generic
assigned-to-assigned flat fallback is still invalid. A geometry-only rescue
control selected 5 fit edges with `4/5` correctness and 8 calibration edges
with `7/8`, then failed the independent window at `1/3`; it was rejected.

The relation graph already computes a sparse top-k candidate graph before its
max-regret path cover. v7 exposes those candidates from the same inference pass
(at most six targets per source) instead of rebuilding the quadratic graph.
Across all scorable selected-native adjacencies between distinct text regions,
the frozen `score >= 0.95` bucket has correctness:

| Raw relation-supported adjacency | Correct / scorable | Precision |
|---|---:|---:|
| 50-page fit | 460 / 469 | 0.98081023 |
| 14-page calibration | 94 / 96 | 0.97916667 |
| 32-page official-test window | 318 / 321 | 0.99065421 |

The rescue still requires selected-native adjacency, distinct provider text
regions, v6 vertical continuity, horizontal overlap `>= 0.5`, free element and
region degree slots, and an acyclic element/region graph. A supported edge is a
review transition with candidate-score and geometry provenance; it never
changes membership or enables runtime reorder.

| Provider hierarchy | Continuity v6 F1 | Adjacency v7 precision / recall / F1 | Rescue correctness | Flat F1 |
|---|---:|---:|---:|---:|
| 50-page fit | 0.97688390 | 0.97907403 / 0.97550169 / 0.97728460 | 3 / 3 | 0.94768195 |
| 14-page calibration | 0.97363796 | 0.97975352 / 0.97205240 / 0.97588777 | 5 / 5 | 0.97694650 |
| 32-page official-test window | 0.97527740 | 0.97967162 / 0.97131783 / 0.97547684 | 1 / 1 | 0.96606248 |

Calibration gains `+0.00224981` over v6 and now trails flat by only
`0.00105873`; independent test gains `+0.00019944` and remains above flat by
`0.00941436`. The oracle 32-page line/region control stays exactly
`0.95571096/0.93055556`. Attention, BYD, JD, and PUMA real-page replays emit no
new rescue edge, and their base/candidate order remains unchanged. This is the
desired abstention behavior on unlabelled complex pages, but the small emitted
sample and remaining calibration gap keep `runtime_reorder: false`.

The design follows relation-prediction work that applies degree and cycle
constraints globally rather than trusting one local score
([Qiao et al., Pattern Recognition 2024](https://doi.org/10.1016/j.patcog.2024.110314)).
The next improvement should target provider split/merge grouping itself; adding
more flat rescue edges is no longer the highest-value path.

### Assigned-Stream Grouping Diagnostic

Provider-region co-membership alone cannot show whether v6's discontinuity
splits improve the actual streams exposed to editors and translators. The
benchmark now also computes oracle co-membership pair F1 over provider-derived
reading streams. Streams with `region_id: null`, including
`unassigned-fallback`, are excluded so the fallback cannot create artificial
same-group pairs.

| Partition | Provider-region pair F1 | Assigned-stream pair F1 | Delta |
|---|---:|---:|---:|
| 50-page fit | 0.67995655 | 0.67895654 | -0.00100001 |
| 14-page calibration | 0.65042468 | 0.65148234 | +0.00105766 |
| 32-page official-test window | 0.80643143 | 0.80042627 | -0.00600516 |

The split streams do not improve grouping consistently: the small calibration
gain does not generalize to fit or independent test. This metric is retained as
a regression gate, and broader splitting remains rejected. Future grouping
work must improve this diagnostic and relation quality together on held-out
documents.

### Audited 128-Page Training Expansion

Larger train-only reconstruction previously stopped when an annotation page was
outside its pinned arXiv v1 PDF and text alignment was ambiguous. Alignment
thresholds were deliberately not relaxed. The fetcher now has an explicit
whole-document recovery mode:

```bash
scriptorium fetch-comphrdoc-provider-calibration \
  --sample-count 128 --document-count 64 \
  --calibration-fraction 0.2 --arxiv-version v1 \
  --annotation-archive /path/to/CompHRDoc.zip \
  --skip-unaligned-documents \
  --out-dir data/external/comphrdoc-provider-calibration-128
```

The default still fails closed. In recovery mode every selected page is aligned
before any derived sample for that document is written. A failure excludes the
entire document, records its source hash, failed page, best candidate F1,
margin, and fixed thresholds, then selects the next document from the same
deterministic hash partition. It never drops one page silently.

The real run produced 128 unique pages from 64 unique documents:

| Corpus property | Result |
|---|---:|
| Fit / calibration pages | 102 / 26 |
| Graphical-multicolumn / multicolumn / graphical | 64 / 60 / 4 |
| Whole documents rejected and replenished | 2 |
| Oracle membership coverage | 0.99786574 |
| Oracle within-region successor F1 | 0.99211930 |
| Oracle region-transition F1 | 0.92806959 |

Both rejected documents were fit documents whose annotated page index equalled
the source PDF page count. Their best alignment F1 values were approximately
`0.1482`, well below the unchanged `0.6` minimum, and neither appears in the
final document or sample lists.

The grouping architecture should now move from merging detector rectangles to a
sparse line graph with separate paragraph-membership and successor heads.
Post-OCR paragraph recognition reports better results for its two-stage
line-splitting/line-clustering model than for the later unified local model, and
explicitly notes line width/indentation as useful paragraph context
([Wang et al., WACV 2022](https://arxiv.org/abs/2101.12741),
[Liu et al., DAS 2022](https://arxiv.org/abs/2203.09638)). This matches the
assigned-stream result above: region splitting alone is not a general grouping
solution. Any learned line graph remains benchmark-only until document-held-out
segmentation and relation gates pass together.

PP-DocLayoutV3 was then rerun over all 128 rendered images; every provider JSON
is bound to the new corpus manifest hash. Provider-derived hierarchy predicts
fit and calibration in separate passes before opening the corresponding labels:

| Provider hierarchy | Relation F1 | Flat F1 | Provider-region pair F1 | Assigned-stream pair F1 | Assignment coverage |
|---|---:|---:|---:|---:|---:|
| 102-page fit | 0.97747518 | 0.96136149 | 0.71930643 | 0.71988356 | 0.96719334 |
| 26-page calibration | 0.97717622 | 0.97578947 | 0.62020524 | 0.62210668 | 0.92364898 |
| 32-page official-test window | 0.97547684 | 0.96606248 | 0.80643143 | 0.80042627 | 0.97687225 |

The larger calibration relation result is now `+0.00138675` above flat, while
fit is `+0.01611369` above flat and the unchanged independent test remains
`+0.00941436` above flat. This closes the previous relation-level calibration
deficit on a broader document set. It does not close grouping: assigned streams
gain only `+0.00057713/+0.00190144` over provider regions on expanded
fit/calibration and still lose `-0.00600516` on independent test.

The first expanded replay also exposed a performance correctness defect. Short
overlapping boxes on pages with a larger median line height could form a cycle
in spatial-graph predecessor links; root traversal had no cycle guard and two
otherwise ordinary pages did not terminate. Sampling isolated the loop, and
cycles are now normalized to their deterministic visual-minimum root. The slow
page completes in `2.48s`; the current full 411-test suite passes, and all prior
50/14/32-page benchmark values remain bit-for-bit unchanged.

A strict safe-merge ranker was reevaluated after expansion. Candidates are
adjacent text-region pairs generated before labels open. Features contain only
region member counts/order spans, local boundary geometry, text-continuation
flags, and relation-graph scores. A positive label requires both provider
regions to be individually pure and their complete member union to belong to
one oracle region. Five-fold fit OOF keeps whole documents together:

| Strict safe-merge split | Candidates / positives | ROC AUC | Average precision |
|---|---:|---:|---:|
| 102-page fit, document OOF | 1420 / 282 | 0.86211189 | 0.58226815 |
| 26-page calibration replay | 235 / 38 | 0.78760353 | 0.49479894 |
| 32-page independent-test replay | 483 / 68 | 0.88807583 | 0.61506653 |

No fit-only threshold reaches precision `>= 0.98` with at least 20 candidates.
The best eligible-size fit bucket is only `19/25 = 0.76`; even a 50-candidate
minimum peaks at `77/103 = 0.74757282`. The ranker is rejected and no provider
region is merged. Edge-level successor correctness must not be interpreted as
cluster-level merge safety.

### Source-Neutral Fine-Line Paragraph Graph

The rejected region merge is replaced by a source-neutral experiment over fine
elements. It ignores provider rectangles and constructs sparse candidate pairs
from selected-order adjacency, relation-graph candidates, and three local
forward geometry neighbours. The 23 answer-free features cover normalized pair
geometry, overlap, line dimensions, page position, text length and continuation
signals, selected adjacency, and relation-graph scores. Equivalent element
arrays are first canonicalized by geometry and stable id; reversing every input
array across all 160 pages changes zero candidate records.

```bash
scriptorium benchmark-paragraph-graph \
  /path/to/comphrdoc-provider-train-128 \
  --test-corpus /path/to/comphrdoc-provider-test-32 \
  --output outputs/paragraph-graph-report.json \
  --proposals-dir outputs/paragraph-graph-proposals \
  --model-output outputs/models/paragraph-graph.joblib
```

Five-fold OOF training keeps whole fit documents together. Only fit OOF labels
select the operating point: at least 100 edges, edge precision `>= 0.97`, then
maximum complete co-membership pair F1. The resulting threshold `0.94971959` is
frozen before calibration or independent-test labels are opened. All inputs are
loaded before fit labels, evaluation predictions and proposals are written
before evaluation labels, corpus paths are confined and hash-checked, and
sample ids must be globally unique.

| Partition | Provider-region pair F1 | Assigned-stream pair F1 | Fine-line graph pair F1 | Delta vs assigned | Selected-edge precision |
|---|---:|---:|---:|---:|---:|
| 102-page fit, document OOF | 0.71930643 | 0.71988356 | 0.81549627 | +0.09561271 | 0.99202393 |
| 26-page calibration | 0.62020524 | 0.62210668 | 0.83054081 | +0.20843413 | 0.99642147 |
| 32-page independent test | 0.80643143 | 0.80042627 | 0.85162046 | +0.05119419 | 0.99548736 |

The independent result also exceeds provider-region grouping by `+0.04518903`.
Within that test, graphical-multicolumn pages score `0.86536272` and
multicolumn pages score `0.83935335`. The one graphical-only page contains two
non-text objects and zero labelled text pairs, so the report marks it as
unscorable rather than treating its zero-valued metric as evidence of failure.

Each proposal contains thresholded candidate edges and review-required local
paragraph streams, never oracle membership. It remains
`runtime_reorder: false`: the current head predicts paragraph co-membership but
not paragraph-to-paragraph successors, and the held-out corpus is still an
English scientific-paper family. The separate successor head is evaluated
below; joint decoding still needs cross-domain annual-report, portal,
Chinese-document, and image-source labels. This follows relation-first
reading-order work ([Qiao et al. 2024](https://doi.org/10.1016/j.patcog.2024.110314),
[ROOR](https://aclanthology.org/2024.emnlp-main.540/)) and the multi-relation
GraphDoc direction ([ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/cf3d7d8e79703fe947deffb587a83639-Paper-Conference.pdf)).

### Directed Fine-Line Successor Graph

Paragraph membership does not determine the immediate edge between two lines
or the handoff between paragraphs. A second source-neutral benchmark therefore
trains a directed successor head without provider rectangles or paragraph
labels:

```bash
scriptorium benchmark-successor-graph \
  /path/to/comphrdoc-provider-train-128 \
  --test-corpus /path/to/comphrdoc-provider-test-32 \
  --output outputs/successor-graph-report.json \
  --proposals-dir outputs/successor-graph-proposals \
  --model-output outputs/models/successor-graph.joblib
```

Candidates are bidirectional selected adjacencies, bidirectional sparse
relation candidates, and the fixed 20 nearest directed geometry targets per
source. This gives 175,748 fit candidates with 7,858 positive edges and a
fit-only candidate-recall ceiling of `0.99632306`. The 39 answer-free features
contain directed geometry/text signals, base-rank direction, relation scores,
and coarse source/target roles. Five document-level OOF folds choose threshold
`0.52131309` under fit-only precision `>= 0.97` and at least 1,000 selected
edges. Each source contributes only its top target; score-ordered degree-one
and cycle guards then produce an acyclic path cover.

| Partition | Flat F1 | Provider hierarchy F1 | Directed graph F1 | Delta vs provider | Precision / recall | Candidate recall | Cross-region recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| 102-page fit, document OOF | 0.96136149 | 0.97747518 | 0.98391591 | +0.00644073 | 0.98279570 / 0.98503867 | 0.99632306 | 0.94290375 |
| 26-page calibration | 0.97578947 | 0.97717622 | 0.98679345 | +0.00961723 | 0.98523207 / 0.98835979 | 0.99682540 | 0.95205479 |
| 32-page independent test | 0.96606248 | 0.97547684 | 0.98585545 | +0.01037861 | 0.98566447 / 0.98604651 | 0.99496124 | 0.94796380 |

The independent direction and magnitude agree with fit/calibration. Test
graphical-multicolumn F1 is `0.97319400`; ordinary multicolumn is
`0.99527027`. The single graphical-only page has no labelled successor and is
excluded through explicit labelled-page counts. Path-cover decoding rejects
`1/1/1` cycles and `6/6/9` incoming conflicts on fit/calibration/test; it raises
test top-candidate F1 `0.98529412 -> 0.98585545` rather than merely asserting a
graph invariant.

All 160 forward/reversed element-array comparisons produce identical
candidates. The full run writes top-three alternatives, score margins, selected
review edges, and local chains before evaluation labels open; it took `5:16`
and about `1.07 GB` peak RSS in the observed environment. The output remains
`runtime_reorder: false`. Both graph heads generalize within the held-out
English-paper family. A separate joint decoder now consumes their review-only
proposals. Both heads can serialize a hash-checked `.joblib` model via
`--model-output`, score evaluation pages with page-wise feature batches so the
dense fit matrix is released before proposal generation, and emit single-page
review proposals through `predict-paragraph-graph` /
`predict-successor-graph`. Real PDF/image pages can enter that path without
provider structure via `export-hierarchy-input` (fine-only DocumentIR export).
Cross-domain annual-report / portal / Chinese / image-source labels remain open
gates before any automatic semantic-order replacement.

### Joint Paragraph/Successor Decode

`benchmark-joint-graph` does not retrain either head. It loads existing
review-only paragraph and successor proposals, writes joint hierarchical
proposals, then opens labels:

```bash
scriptorium benchmark-joint-graph   /path/to/comphrdoc-provider-train-128   --paragraph-proposals-dir outputs/paragraph-graph-proposals   --successor-proposals-dir outputs/successor-graph-proposals   --test-corpus /path/to/comphrdoc-provider-test-32   --output outputs/joint-graph-report.json   --proposals-dir outputs/joint-graph-proposals
```

Decoder contract:

1. Paragraph proposal streams define co-membership components.
2. Successor rank-1 candidates are split into within-paragraph and
   cross-paragraph pools.
3. Within-paragraph edges are protected in a degree-one acyclic path cover.
4. Cross-paragraph edges may connect only a chain tail to a chain head and are
   accepted score-first under the same path-cover guards.
5. Joint proposals keep `runtime_reorder: false` and never contain oracle
   membership or oracle scope fields.

Synthetic multi-column fixture tests cover answer separation, degree-one
conflicts, missing/poisoned proposals, and proposal schema hygiene. Full
Comp-HRDoc partition numbers are intentionally not claimed here until the
frozen train/test corpora are re-run end-to-end with the joint decoder.

DocumentIR bridge path for real sources:

```bash
scriptorium convert data/external/attention-is-all-you-need.pdf \
  --out-dir outputs/attention-page0 --dpi 144
scriptorium export-hierarchy-input outputs/attention-page0/document.ir.json \
  --page-index 0 --sample-id attention-page-0 \
  --output outputs/attention-page0/hierarchy-input.json
scriptorium predict-paragraph-graph outputs/attention-page0/hierarchy-input.json \
  --model outputs/models/paragraph-graph.joblib \
  -o outputs/attention-page0/paragraph.proposal.json
scriptorium predict-successor-graph outputs/attention-page0/hierarchy-input.json \
  --model outputs/models/successor-graph.joblib \
  -o outputs/attention-page0/successor.proposal.json
```

A local smoke run exported 56 fine elements from Attention page 0, produced
review-only paragraph/successor proposals with `runtime_reorder: false`, and
joint-decoded synthetic train proposals at perfect fixture F1. That validates
the pipeline only; models trained on synthetic multi-column fixtures are not
cross-domain evidence for papers, annual reports, portals, Chinese documents,
or image-source OCR.

### Graph Hierarchy Materialize and 8-Page Comp-HRDoc Smoke

`materialize-graph-hierarchy` converts an answer-separated hierarchy corpus into
the provider-hierarchy corpus schema used by the fine-line graph heads without
running a layout provider. Inputs keep fine text/geometry only (`regions: []`);
labels open only after every input is written:

```bash
scriptorium materialize-comphrdoc-hierarchy \
  data/external/comphrdoc-provider-calibration-smoke \
  -o outputs/comphrdoc-hierarchy-smoke
scriptorium materialize-graph-hierarchy \
  outputs/comphrdoc-hierarchy-smoke \
  -o outputs/graph-hierarchy-smoke
scriptorium benchmark-paragraph-graph outputs/graph-hierarchy-smoke \
  --cross-validation-folds 2 \
  --minimum-edge-precision 0.9 --minimum-selected-edges 20 \
  --model-output outputs/graph-hierarchy-smoke/models/paragraph.joblib \
  -o outputs/graph-hierarchy-smoke/paragraph-report.json
scriptorium benchmark-successor-graph outputs/graph-hierarchy-smoke \
  --cross-validation-folds 2 --nearest-candidates 10 \
  --minimum-edge-precision 0.9 --minimum-selected-edges 50 \
  --model-output outputs/graph-hierarchy-smoke/models/successor.joblib \
  -o outputs/graph-hierarchy-smoke/successor-report.json
scriptorium benchmark-joint-graph outputs/graph-hierarchy-smoke \
  --paragraph-proposals-dir outputs/graph-hierarchy-smoke/paragraph-proposals \
  --successor-proposals-dir outputs/graph-hierarchy-smoke/successor-proposals \
  -o outputs/graph-hierarchy-smoke/joint-report.json
```

On an 8-page train-only Comp-HRDoc smoke (`fit/calibration = 6/2` pages from 4
documents; 2-fold document OOF, not the frozen 128/32 protocol):

| Head | Fit metric | Calibration metric |
|---|---:|---:|
| Paragraph pair F1 | 0.85743802 | 0.78170674 |
| Paragraph selected-edge precision | 0.96118012 | 0.96888889 |
| Successor relation F1 | 0.97297297 | 0.95681063 |
| Successor cross-region recall | 0.85526316 | 0.73684211 |
| Joint relation F1 | 0.97297297 | 0.95681063 |
| Joint segmentation pair F1 | 0.85743802 | 0.78170674 |
| Joint within-region recall | 0.98921833 | 1.0 |
| Joint cross-region recall | 0.85526316 | 0.73684211 |

Joint decode prefers packaging a successor path cover with paragraph hierarchy
labels (`decoder_mode: successor-path-cover-package`); it falls back to
paragraph-protected re-decode only when the loaded successor edges are not a
valid path cover. Relation scoring is partial-label-aware and matches the
successor head on this smoke (`0.97297297 / 0.95681063`). All outputs remain
`runtime_reorder: false`. These 8-page numbers are pipeline evidence only: they
use a tiny document-disjoint split, relaxed operating gates, and are not a
frozen independent-test promotion window.

A larger train-only 32-page Comp-HRDoc smoke (`fit/calibration = 26/6` pages from
12 documents after one audited unaligned-document skip; 3-fold document OOF;
still not the frozen 128/32 protocol) was materialized the same way and scored
with tighter gates (`edge precision >= 0.95`):

| Head | Fit metric | Calibration metric |
|---|---:|---:|
| Paragraph pair F1 | 0.81574340 | 0.67942089 |
| Paragraph selected-edge precision | 0.99407992 | 1.0 |
| Successor relation F1 | 0.97468010 | 0.98010471 |
| Successor multicolumn F1 | 0.98110831 | 0.98863636 |
| Successor graphical-multicolumn F1 | 0.96693767 | 0.96955504 |
| Successor cross-region recall | 0.93354430 | 0.90196078 |
| Joint relation F1 | 0.97468010 | 0.98010471 |
| Joint segmentation pair F1 | 0.81574340 | 0.67942089 |
| Joint within-region recall | 0.98875661 | 0.99528302 |
| Joint cross-region recall | 0.93354430 | 0.90196078 |

All 32 joint proposals used `successor-path-cover-package`. Joint relation
metrics match the successor head exactly while adding paragraph co-membership
streams. Qualitative transfer to Transformer-XL page 1 (fine-only export, 99
lines) produced 90 successor edges. Because the OOD paragraph head was fully
singleton, joint packaging used
`successor-path-cover-package-chain-geometry-fallback`: 5 geometry splits turned
9 raw chains into 14 packaging components (title/author block, abstract-like
block, introduction heading, left-column body, right-column continuation) while
keeping the same 90 relation edges and 9 reading streams. In-domain 32-page
pages stayed on ordinary package mode, so labeled segmentation metrics were
unchanged. Outputs remain `runtime_reorder: false` and are not a promotion
window.
