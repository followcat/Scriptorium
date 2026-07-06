<p align="right">
  <strong>简体中文</strong>
  |
  <a href="external-benchmarks.md">English</a>
</p>

# 外部基准样本

这些样本故意不进入 git，因为 `data/` 和 `outputs/` 已被忽略。本文档记录如何重新创建本地 PDF，以及当前 benchmark 报告中保存的测量结果。

## 当前样本

| 样本 | 本地 PDF | 来源 | 目的 |
|---|---|---|---|
| PUMA 2024 Annual Report | `data/external/puma-2024-annual-report.pdf` | `https://annualreports.com/Click/27465` | 上市公司公开年报，包含密集图片、文本、表格和形状排版。 |
| JD 首页完整截图 PDF | `outputs/external/jd-home/input.pdf` | `https://www.jd.com/` 在当前环境会跳转到 `https://hk.jd.com/` | 电商首页完整截图封装成 image-only PDF，用于考察网页图文混排和 OCR 锚点。 |

## 重新创建输入

下载 PUMA 年报：

```bash
curl --fail --location https://annualreports.com/Click/27465 \
  --output data/external/puma-2024-annual-report.pdf
```

捕获 JD 首页完整截图并封装成 PDF：

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

在受限自动化环境里，Chrome 可能需要在沙箱外运行。

## Benchmark 命令

PUMA 年报前 12 页：

```bash
./.venv/bin/scriptorium benchmark data/external/puma-2024-annual-report.pdf \
  --out-dir outputs/external/puma-2024-annual-report-relation-graph-diagnostics-v1 \
  --dpi 144 \
  --max-pages 12 \
  --html-mode auto \
  --fidelity-background auto
```

JD 截图 PDF：

```bash
./.venv/bin/scriptorium benchmark outputs/external/jd-home/input.pdf \
  --out-dir outputs/external/jd-home-relation-graph-diagnostics-v1 \
  --dpi 144 \
  --html-mode auto \
  --fidelity-background auto
```

## 当前结果

| 样本 | 评分页数 | 选择路径 | 视觉相似度 | 最大差异 | 平均差异 | 元素 | 可编辑 | OCR 页 | OCR 文本 | 混合表格流 | 表格行优先 | Spatial Graph | Box-Flow 元素 | Caption | Box-Flow Pairwise | Box-Flow Successor | Relation Pairwise | Relation Successor | 页边 Artifact | 脚注 | 边栏 | RO 置信度 | 低置信 RO | 阅读风险 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 Annual Report | 12 | `fidelity/raster` | 0.9795117 | 0.0204883 | 0.01089482 | 815 | 521 | 0 | 0 | 238 | 0 | 0 | 0 | 0 | 0.17460108 | 199/509 | 0.16306211 | 166/509 | 20 | 2 | 36 right | 0.82476488 | 0 | `0.35 / high` |
| JD 首页截图 PDF | 1 | `fidelity/raster` | 0.99576887 | 0.00423113 | 0.00423113 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 127/133 | 0.21624958 | 117/133 | 0 | 0 | 0 | 0.83 | 0 | `0.35 / high` |

PUMA 目前没有 semantic sidecar，因此高阅读顺序风险是下一步标注工作的有效信号。它的 OCR fallback 为 0，因为采样页已经包含原生 PDF 文本。当前诊断报告显示 5 个 repeated-anchor 页面、最多 3 个锚点、4 个 table-like 页面，并且 table-like visual-yx 页面为 0。混合表格、artifact、sidebar、footnote 路径识别出 99 个直接 column-flow 元素、238 个 mixed-table-flow 元素、20 个页眉 artifact、36 个右侧边栏/旁注元素和 2 个脚注元素。

JD 是刻意构造的 image-only PDF。当前运行在保留源视觉分数的同时新增了 134 个透明 `native-ocr` 可编辑锚点。它的 OCR 文本不再误入 mixed-table strategy，而是由 recursive XY-Cut 处理。因为已经有文本但缺少 semantic sidecar，阅读风险保持 high；这比过去 0 文本时的低风险报告更有诊断价值。

Semantic successor-edge 指标在 PUMA 和 JD 上暂不可用，直到添加被跟踪的 `.semantic-order.json` sidecar。当前 successor 验证由内置 fixtures 的 47/47、arXiv Attention 的 33/33、Transformer-XL 前 3 页的 41/41、Hacker News 的 24/24 覆盖。后续扩展外部复杂页面 sidecar 后，这个指标会成为局部阅读连续性的主要分数。

Box-flow、relation-graph 和 successor-consensus disagreement 不是正确率。Pairwise disagreement 用来发现整体候选顺序差异；successor disagreement 更关注相邻后继边。PUMA 从 box-flow 的 199/509 改善到 relation-graph 的 166/509，JD 从 127/133 改善到 117/133。这说明 relation graph 和 successor consensus 值得作为候选信号保留，但 PUMA/JD 仍然需要 semantic sidecar 或 Paddle/PP-Structure/Docling 外部结构证据，才能安全改变默认排序规则。
