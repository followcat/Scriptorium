# External Benchmark Samples

These samples are intentionally kept out of git because `data/` and `outputs/` are ignored. The table records how to recreate the local PDFs and which benchmark reports currently hold the measured results.

## Current Samples

| Sample | Local PDF | Source | Purpose |
|---|---|---|---|
| PUMA 2024 Annual Report | `data/external/puma-2024-annual-report.pdf` | `https://annualreports.com/Click/27465` | Public listed-company annual report with dense image, text, table, and shape layout. |
| JD homepage screenshot PDF | `outputs/external/jd-home/input.pdf` | `https://www.jd.com/` redirects to `https://hk.jd.com/` in this environment | Full-page ecommerce homepage screenshot converted into an image-only PDF. |

## Recreate Inputs

Download the PUMA annual report:

```bash
curl --fail --location https://annualreports.com/Click/27465 \
  --output data/external/puma-2024-annual-report.pdf
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

## Benchmark Commands

PUMA annual report, first 12 pages:

```bash
./.venv/bin/scriptorium benchmark data/external/puma-2024-annual-report.pdf \
  --out-dir outputs/external/puma-2024-annual-report-box-flow-fallback-v1 \
  --dpi 144 \
  --max-pages 12 \
  --html-mode auto \
  --fidelity-background auto
```

JD screenshot PDF:

```bash
./.venv/bin/scriptorium benchmark outputs/external/jd-home/input.pdf \
  --out-dir outputs/external/jd-home-box-flow-fallback-v1 \
  --dpi 144 \
  --html-mode auto \
  --fidelity-background auto
```

## Current Results

| Sample | Pages Scored | Selected Path | Visual Similarity | Max Diff | Mean Diff | Elements | Editable | OCR Pages | OCR Text | Mixed Table Flow | Table Row-Major | Spatial Graph | Box-Flow Elements | Box-Flow Disagreement | Page Artifacts | Footnotes | Sidebars | RO Confidence | Low-Conf RO | Reading Risk |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 Annual Report | 12 | `fidelity/raster` | 0.9795117 | 0.0204883 | 0.01089482 | 815 | 521 | 0 | 0 | 238 | 0 | 0 | 0 | 0.17460108 | 20 | 2 | 36 right | 0.82476488 | 0 | `0.35 / high` |
| JD homepage screenshot PDF | 1 | `fidelity/raster` | 0.99576887 | 0.00423113 | 0.00423113 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0.42778588 | 0 | 0 | 0 | 0.83 | 0 | `0.35 / high` |

PUMA has no semantic sidecar yet, so its high reading-order risk is a useful signal for the next labeling pass. Its OCR fallback counts are 0 because the sampled pages already expose native PDF text. The current diagnostics report 5 repeated-anchor pages, max 3 anchors, 4 table-like pages, and 0 table-like visual-yx pages. The current mixed-table/artifact/sidebar/footnote pass reports 99 direct column-flow elements, 238 mixed-table-flow elements, 20 header artifacts, 36 right-side sidebar/marginalia elements, and 2 footnote elements, keeping detected local table islands row-major while surrounding body text can still use column flow.

Box-flow fallback v1 keeps the same PUMA/JD external scores while adding a guarded weak-column path and a candidate-order disagreement signal. Both current external cases report 0 spatial-graph and 0 box-flow nodes because stronger existing paths win first. PUMA has no pure `table-row-major-v1` nodes in the sampled pages because table-like regions are handled as mixed table islands; it still reports 36 `sidebar-secondary-flow` / `right-sidebar` evidence hits, 2 `footnote-secondary-flow` / `bottom-note-zone` evidence hits, 46 `table-island-row-major` hits, 20 `page-edge-artifact` hits, 163 `column-flow` hits, and 271 `single-column-visual-order` hits. JD reports 134 `recursive-xy-cut` OCR anchors with horizontal/vertical whitespace-cut evidence.

The box-flow disagreement ratio is not a correctness score. PUMA's `0.17460108` indicates moderate candidate disagreement across 11 sampled pages; JD's `0.42778588` is high and expected for dense screenshot/OCR anchors. These values are useful for selecting pages that need semantic sidecars or external Paddle/PP-Structure/Docling evidence before changing ordering rules.

JD is image-only by design. The latest run keeps the same source-preservation score while adding 134 transparent `native-ocr` editable anchors. Its OCR text now stays out of the mixed-table strategy after the duplicate-slot formula/table guard and is handled by recursive XY-Cut. Its reading risk is high because text is available but no semantic sidecar exists yet; that is a better diagnostic than the previous 0-text low-risk result.
