<p align="center">
  <a href="../README.md"><img alt="返回首页" src="https://img.shields.io/badge/%E8%BF%94%E5%9B%9E%E9%A6%96%E9%A1%B5-README-2b6cb0"></a>
  <img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D-blue">
  <a href="external-benchmarks.md"><img alt="English" src="https://img.shields.io/badge/English-Read-2f855a"></a>
</p>

# 外部基准样本

这些样本故意不进入 git，因为 `data/` 和 `outputs/` 已被忽略。本文档记录如何重新创建本地 source，以及当前 benchmark 报告中保存的测量结果。

## 当前样本

| 样本 | 本地 source | 来源 | 目的 |
|---|---|---|---|
| PUMA 2024 Annual Report | `data/external/puma-2024-annual-report.pdf` | `https://annualreports.com/Click/27465` | 上市公司公开年报，包含密集图片、文本、表格和形状排版。 |
| 比亚迪 2024 年年度报告 | `data/external/byd-2024-annual-report.pdf` | `https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF` | 中国 A 股上市公司年报，290 页中文公告/财务表格/密集矢量线框。 |
| JD 首页完整截图 PNG | `outputs/external/jd-home/full-page.png` | `https://www.jd.com/` 在当前环境会跳转到 `https://hk.jd.com/` | 同一电商首页截图的一等 image source 路径。 |
| JD 首页完整截图 PDF | `outputs/external/jd-home/input.pdf` | `https://www.jd.com/` 在当前环境会跳转到 `https://hk.jd.com/` | 电商首页完整截图封装成 image-only PDF，用于考察网页图文混排和 OCR 锚点。 |
| Hacker News 打印 PDF | `outputs/external/web-hn/input.pdf` | `https://news.ycombinator.com/` | 真实网页打印 PDF，包含门户/列表式排版，并有已跟踪的 semantic sidecar。 |

## 重新创建输入

下载 PUMA 年报：

```bash
curl --fail --location https://annualreports.com/Click/27465 \
  --output data/external/puma-2024-annual-report.pdf
```

从巨潮资讯下载比亚迪 A 股年报：

```bash
curl --fail --location https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF \
  --output data/external/byd-2024-annual-report.pdf
```

当前本地校验：

```text
SHA256 e9c2d7fdd088e151ccb6c8ad3d95587b2b014b10f2c9731508d23ce07fde4de3
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

通过 Playwright 打印路径捕获 Hacker News 门户/列表页：

```bash
./.venv/bin/scriptorium capture-pdf \
  https://news.ycombinator.com/ \
  --pdf outputs/external/web-hn/input.pdf \
  --mode print
```

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

JD 截图 PNG 一等 image source 路径：

```bash
./.venv/bin/scriptorium benchmark outputs/external/jd-home/full-page.png \
  --out-dir outputs/external/jd-home-image-source-benchmark-v1 \
  --dpi 144 \
  --input-kind image \
  --image-dpi 96 \
  --html-mode auto \
  --fidelity-background auto
```

比亚迪 A 股年报前 40 页：

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-source-smoke-v1 \
  --dpi 144 \
  --max-pages 40 \
  --html-mode auto \
  --fidelity-background auto
```

比亚迪财务报表区间，按源页码抽样第 136-160 页：

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-financial-pages-v1 \
  --dpi 144 \
  --page-ranges 136-160 \
  --html-mode auto \
  --fidelity-background auto
```

比亚迪翻译回渲染压力测试前 40 页：

```bash
./.venv/bin/scriptorium benchmark data/external/byd-2024-annual-report.pdf \
  --out-dir outputs/external/byd-2024-annual-report-translation-stress-v1 \
  --dpi 144 \
  --max-pages 40 \
  --html-mode fidelity \
  --fidelity-background auto \
  --translation-stress pseudo-expand
```

当关键内容不在长文档开头时，使用 `--page-ranges`。页码是 1-based 源页码，不能和 `--max-pages` 同时使用，并且会保留原始 `page_index`，用于 semantic sidecar 和结构 JSON 对齐。

年报、电商截图和真实门户页的翻译回渲染压力测试：

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

## 当前结果

| 样本 | 评分页数 | 选择路径 | 视觉相似度 | 最大差异 | 平均差异 | 元素 | 可编辑 | OCR 页 | OCR 文本 | 混合表格流 | 表格行优先 | Spatial Graph | Box-Flow 元素 | Caption | Box-Flow Pairwise | Box-Flow Successor | Relation Pairwise | Relation Successor | 页边 Artifact | 脚注 | 边栏 | RO 置信度 | 低置信 RO | 阅读风险 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 Annual Report | 12 | `fidelity/raster` | 0.9795117 | 0.0204883 | 0.01089482 | 815 | 521 | 0 | 0 | 238 | 0 | 0 | 0 | 0 | 0.17460108 | 199/509 | 0.16306211 | 166/509 | 20 | 2 | 36 right | 0.82476488 | 0 | `0.35 / high` |
| 比亚迪 2024 年年度报告 | 40 | `fidelity/raster` | 0.89780001 | 0.10219999 | 0.05377595 | 9531 | 3015 | 0 | 0 | 1052 | 0 | 0 | 0 | 0 | 0.32890849 | 2496/2975 | 0.09694495 | 981/2975 | 0 | 33 | 97 right | 0.89081217 | 0 | `0.35 / high` |
| JD 首页截图 PDF | 1 | `fidelity/raster` | 0.99576887 | 0.00423113 | 0.00423113 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0 | 0.42778588 | 127/133 | 0.21624958 | 117/133 | 0 | 0 | 0 | 0.83 | 0 | `0.35 / high` |
| JD 首页截图 PNG | 1 | `structured/image-source` | 0.99236799 | 0.00763201 | 0.00763201 | 135 | 134 | 1 | 134 | 0 | 0 | 0 | 0 | 0 | 0.43833464 | 128/133 | 0.21894288 | 120/133 | 8 | 0 | 0 | 0.77151567 | 0 | `0.35 / high` |

JD PNG 直接输入验证了 image source 一等路径。它和 image-only PDF 兼容路径产生相同语义规模：135 个总元素、134 个可编辑 OCR 文本锚点、35 个 grid-island 元素，并且同样因为缺少 semantic sidecar evidence 而保持 high 阅读风险。视觉分数略低，是因为评分路径直接比较 source image visual layer 与 HTML 打印结果，而不是和 image-only PDF rasterization 比较；页数和页面尺寸仍然匹配。当前 benchmark 也会输出 `semantic_layer_driver`，用于区分结构 JSON、OCR JSON、OCR fallback 和 visual-only 图片运行。

比亚迪是当前复杂中文年报压力样本。本地 PDF 为 290 页、10,092,140 bytes。快速 PyMuPDF profile 显示：前 20 页有 497 个 text blocks 和 1088 个 drawing objects，而 PUMA 同页数只有 257 个 text blocks 和 375 个 drawing objects。全 PDF 中，比亚迪有 50,724 个 drawing objects，并有 101 页 `blocks >= 30`；PUMA 对应为 37,081 个 drawing objects 和 65 页。因此它补上了 PUMA 覆盖不足的中文公告、财务表格、密集线框和翻译回渲染维度。

## PP-StructureV3 A/B

真实 PP-StructureV3 `save_to_json` 输出现已通过 `benchmark-structure-ab` 验证。CPU 运行只启用 layout 和文本识别；公式、表格、图表、印章模块在这些阅读顺序样本中关闭。结构证据只改变语义层，不改变 source visual layer，因此视觉输出保持不变。

| 样本页 | 结构区域 / 匹配元素 | 视觉相似度 | 已标注语义结果 | 结构顺序结果 |
|---|---:|---:|---|---|
| Attention 第 1 页 | 78 / 56 | `0.95231377` | Pair `1.0`，successor `1.0` | 不触发 selected reorder；此前的 sparse-order 回退已消除。 |
| Transformer-XL 第 1-3 页 | 458 / 321 | `0.93853503` | Pair `1.0`，successor `1.0` | 保留 native column flow；stream needs 减少 1，consensus successor disagreement 减少 26。 |
| JD 首页第 1 页 | 160 / 128 | `0.99536129` | 尚无 semantic sidecar | successor-consensus disagreement 减少 62；native grid stream 保持受保护，stream `needs-structure-evidence` 维持基线。 |
| PUMA 第 5 页 | 42 / 25 | `0.95767110` | 尚无 semantic sidecar | implicit-image guard 后 selected order 和 review 诊断均回到 native baseline。 |

这证明 block order 有价值但并不充分：模型 block order 可以改善候选 successor；而翻译仍需要显式局部 `successor_edges` 和 `reading_streams`，才能安全处理门户卡片、图片说明、边栏和重复文本。

## 翻译压力测试结果

`outputs/external/translation-stress-v3` 会把确定性伪扩展译文写入 `translated_text`，再把 fidelity HTML 打印回 PDF，同时测量视觉相似度和 replacement 风险。该运行覆盖 PUMA、JD、web-HN 共 15 页，`mismatched_case_count = 0`，`dimension_match_rate = 1.0`，`page_count_match_rate = 1.0`。

| 样本 | 页数 | 选择路径 | 视觉相似度 | 最大差异 | 平均差异 | 翻译元素 | 扩展倍率 | Overflow | Conflict | 冲突目标 | 最小 Fit | 平均 Fit | Grid Islands | 页数/尺寸匹配 | Semantic Successor |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| PUMA 2024 Annual Report | 12 | `fidelity/svg` | 0.67616927 | 0.32383073 | 0.12495262 | 398 | 1.99511484 | 187 | 396 | 637 | 0.62 | 0.68186231 | 31 | yes / yes | n/a |
| JD 首页截图 PDF | 1 | `fidelity/raster` | 0.87463572 | 0.12536428 | 0.12536428 | 104 | 6.15817223 | 97 | 104 | 192 | 0.62 | 0.63202981 | 35 | yes / yes | n/a |
| Hacker News 打印 PDF | 2 | `fidelity/raster` | 0.90618105 | 0.09381895 | 0.04962468 | 65 | 2.23526357 | 42 | 65 | 70 | 0.62 | 0.63153077 | 0 | yes / yes | 1.0 |

合并摘要：平均视觉相似度为 `0.81899535`，最大差异为 `0.32383073`，平均差异为 `0.09998053`，p95 差异为 `0.30398408`；总翻译元素 `567`，总 overflow `326`，总 conflict `565`，总冲突目标 `899`。`grid_island_element_count` 合计 `66`，其中 PUMA 为 31，JD 为 35。

同一次运行的页级候选建议为：`keep-selected-low-consensus: 1`、`keep-selected-supported: 5`、`needs-structure-evidence: 7`、`review-disagreement: 2`。流级诊断更关注局部结构：`keep-selected-low-consensus: 5`、`keep-selected-supported: 39`、`needs-structure-evidence: 17`、`review-consensus: 1`、`review-disagreement: 1`。

之前 JD-only 的翻译压力测试暴露了 Chromium 额外尾部空白页：真实页面 diff 是 `0.12536428`，但报告被额外空白导出页主导，导致 case 被记为 `visual_similarity = 0.0`。现在 `print_html_to_pdf()` 只删除超出源页数的尾部空白伪页，因此 JD stress 分数回到 `0.87463572`，而真正有内容的溢出页仍会被保留和评分。

比亚迪 standalone 翻译压力测试记录在 `outputs/external/byd-2024-annual-report-translation-stress-v1`；它比 PUMA/JD/web-HN 三样本 sweep 更重，因此单独跟踪。前 40 页 `visual_similarity = 0.90512026`，`max_diff_ratio = 0.09487974`，`mean_diff_ratio = 0.05402886`，`p95_diff_ratio = 0.07766083`，没有页数或尺寸 mismatch。它产生 2274 个伪译文 replacement，扩展倍率 `3.31871867`，overflow 1182，conflict 1257，冲突目标 764，最小 fit scale `0.62`，平均 fit scale `0.66336636`。

比亚迪也覆盖了 stream-local replacement 诊断。按 stream type 统计的 replacement conflict 为：`body: 648`、`grid-island: 453`、`table-island: 124`、`sidebar-right: 19`、`footnote: 13`。最大的 stream-id 冲突桶为：`body-main: 378`、`grid-island-001: 199`、`grid-island-002: 141`、`body-segment-002: 109`、`table-island-001: 103`，适合后续优化 mask/fitting。

PUMA 目前没有 semantic sidecar，因此高阅读顺序风险是下一步标注工作的有效信号。它的 OCR fallback 为 0，因为采样页已经包含原生 PDF 文本。当前诊断报告显示 5 个 repeated-anchor 页面、最多 3 个锚点、4 个 table-like 页面，并且 table-like visual-yx 页面为 0。混合表格、artifact、sidebar、footnote 路径识别出 99 个直接 column-flow 元素、238 个 mixed-table-flow 元素、20 个页眉 artifact、36 个右侧边栏/旁注元素和 2 个脚注元素。

JD 是刻意构造的 image-only PDF。当前运行在保留源视觉分数的同时新增了 134 个透明 `native-ocr` 可编辑锚点。它的 OCR 文本不再误入 mixed-table strategy，而是由 recursive XY-Cut 处理。因为已经有文本但缺少 semantic sidecar，阅读风险保持 high；这比过去 0 文本时的低风险报告更有诊断价值。

Semantic successor-edge 指标在 PUMA 和 JD 上暂不可用，直到添加被跟踪的 `.semantic-order.json` sidecar。当前 successor 验证由内置 fixtures 的 47/47、arXiv Attention 的 33/33、Transformer-XL 前 3 页的 41/41、Hacker News 的 24/24 覆盖。后续扩展外部复杂页面 sidecar 后，这个指标会成为局部阅读连续性的主要分数。

PUMA 和 JD 的下一轮 sidecar 更适合使用关系式标签：用 `successor_edges` 标注正文、边栏、OCR 列表等局部连续链，用 `precedence_edges` 标注 section、caption、table、marginalia 等较松的先后约束。这样避免把多种合理阅读路径的复杂页面强行压成一个任意全局序列。

Box-flow、relation-graph 和 successor-consensus disagreement 不是正确率。Pairwise disagreement 用来发现整体候选顺序差异；successor disagreement 更关注相邻后继边。PUMA 从 box-flow 的 199/509 改善到 relation-graph 的 166/509，JD 从 127/133 改善到 117/133。这说明 relation graph 和 successor consensus 值得作为候选信号保留，但 PUMA/JD 仍然需要 semantic sidecar 或 Paddle/PP-Structure/Docling 外部结构证据，才能安全改变默认排序规则。

`structure_relation` 候选指标已经在代码中可用：它会把页眉页脚 artifact、脚注、边栏、caption-target proximity 和正文 relation-graph 顺序组合成结构感知候选。由于 PUMA/JD 目前还没有 semantic sidecar，本表不写未重跑的候选分数；下一次外部 benchmark 刷新会把 structure-relation 与 successor-consensus 指标一起纳入。

新的 benchmark 报告也会输出 `reading_order_stream_count`、`reading_order_stream_type_counts` 和 `reading_order_stream_id_counts`。最新 grid-island rerun 显示：PUMA 除正文、边栏、脚注、artifact 和 table-island 流外，还暴露 2 个 `grid-island` 流、覆盖 31 个元素；JD 暴露 3 个 `grid-island` 流、覆盖 35 个 OCR 元素。外部 Paddle/PP-Structure/Docling label 现在也可以把明确的 card/grid/product/tile 区域强化为 `grid-island` 翻译流；普通 list label 仍只作为列表证据，不作为卡片网格证据。

Caption-target proximity 诊断已经在代码中可用：后续外部 benchmark 刷新会在样本文本暴露 `Figure/Table` 标签时报告 targeted/orphan caption 数量和 target coverage。当前 PUMA/JD 表格仍沿用之前的结果，因为采样文本没有 leading caption labels，本轮没有重跑外部分数。
