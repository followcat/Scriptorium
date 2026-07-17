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
  --out-dir outputs/external/translation-stress-padding-v1 \
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
| 比亚迪财务报告第 136 页 | 66 / 34 | `0.96518593` | 尚无 semantic sidecar | PP 表格识别把 10 个单元格映射到一个 row-major `table-island`；page needs-structure-evidence 减少 1。 |

新的 `run-pp-structure` 命令还在 Attention 第 1 页、144 DPI 上独立重放，产物位于 `outputs/research/attention-pp-structure-runner-ab-v1`。其 layout-only 配置归一化出 58 个 region、匹配 55 个 native element。visual delta 仍为 `0.0`，有标注的 pair/successor accuracy 仍为 `1.0`；direct strict sidecar successor coverage 从 `3/9` 升至 `5/9`（`0.33333333 -> 0.55555556`）。相比旧控制组 region 数较少是预期行为：该命令默认关闭 table、formula 和 region 模块，只有显式请求时才加载。

这证明 block order 有价值但并不充分：模型 block order 可以改善候选 successor；而翻译仍需要显式局部 `successor_edges` 和 `reading_streams`，才能安全处理门户卡片、图片说明、边栏和重复文本。

## PaddleOCR-VL 1.6 A/B

第一组真实 PaddleOCR-VL 重放使用 PUMA 年报第 5 页。模型在 96 DPI、
`794 x 1123` 的页面图像上运行，再将完全相同的 raw JSON 分别交给 96 和
144 DPI 的 `benchmark-structure-ab`。这是坐标契约测试：模型结果记录输入画布，
而 benchmark 可以按另一种 DPI 渲染 PDF。

| 样本 | 模型区域 / 匹配元素 | Benchmark DPI | 视觉相似度 | 阅读风险 delta | Selected order / candidate delta | 模型关系证据 |
|---|---:|---:|---:|---:|---|---|
| PUMA 第 5 页 | 11 / 24 | 96 和 144 | `0.95767110` | `0.0` | 没有 selected reorder；stream needs 和全部 successor-disagreement delta 均为 `0` | 无：模型没有 relation 或 stream edge |

修正后的 structure 分支生成 3 条局部 sidecar stream、13 条 strict edge、9 条
review edge 和 2 条 review transition。这些是 proposal 证据，不是正确率声明：
PUMA 仍没有 semantic order sidecar。关键结果是 DPI 不变的区域对齐；如果没有
这层映射，把 96-DPI 模型 bbox 重放到 144-DPI 页面时会把段落错误挂到其它文本区域，
并制造虚假的顺序回退。

另一次真实 PaddleOCR-VL 1.6 重放覆盖 Attention 第 1 页，产物位于 `outputs/research/attention-paddle-ab-v1`。16 个模型 region 匹配到 56 个 native element；visual delta 和 selected semantic 指标均不变，但 direct strict sidecar successor coverage 从 `3/9` 升至 `6/9`（`0.33333333 -> 0.66666667`）。这支持把 VLM 的 block order 当作可复核的局部流证据，而不是替代显式 relation prediction。

### 显式模型 block 的翻译流

有些 layout provider 会给出带顺序的段落 block，却不提供 relation edge 或
`reading_streams`。block-stream bridge 只会在 native 匹配后使用这类边界：每个成员
必须是同一个已选 flow segment 和列里的正文文本，coverage 不低于 `0.5`。随后它保留
native 局部顺序，创建 primary `external-block-body-*` stream；不会把 block order 变成
整页 permutation，也不会跨越更强的 table/grid/caption/artifact stream。

| 样本 | 派生 block stream / 成员 | Selected-order 结果 | Sidecar proposal 结果 | 翻译压力结果 |
|---|---:|---|---|---|
| PUMA 年报第 5 页 | 4 / 17 | 没有重排；坐标 A/B baseline visual 为 `0.95767110` | stream / strict / review / transition 为 `7 / 12 / 6 / 7` | 23 个 replacement、6 个 overflow、18 个 conflict/target；v2 在不改变视觉相似度下约束 17 个 mask、26 个方向边。 |
| Transformer-XL 第 1 页 | 6 / 85 | 有标注 successor accuracy 仍为 `1.0` | `13 / 81 / 5 / 13` | 不把它当作翻译视觉保真的声明。 |
| JD 首页第 1 页 | 0 / 0 | 35 个 native grid-island 成员全部保留 | 不创建 generic block stream | 稠密卡片/页面结构仍需要显式模型 relation 或 stream。 |

这项改动有意只提升局部语义，不走分数捷径：PUMA 和 JD 还没有人工 relation 标签，
stream/proposal 计数只能作为诊断证据。PUMA 压力结果也说明下一步 fidelity 工作必须在
这些 stream 内约束 mask 和 fitting，不能把新增 stream id 当成 conflict 已降低。

## Docling Body-Tree A/B

Docling 现在只贡献有边界的同页 sibling run，而不再把序列化的整页 body order 当作证据。Root-body run 会在几何断点结束，遇到更强的 native 局部 stream 时还会再次切断。落在明确 native column 上的 membership 和 edge 会保留为可审查的 secondary evidence，而不会成为主翻译流或可执行的页级重排约束。最新重跑使用带空白 PDF 检测的 Chromium 路径，因此 native-only 和 structure 分支会先得到相同的非空视觉层，再比较语义指标。

| 样本 | Docling 区域 / 匹配元素 | 视觉相似度 | 语义 / 局部流结果 |
|---|---:|---:|---|
| Transformer-XL 第 1-3 页 | 72 / 321 | `0.93853503` | Pair 和 successor 仍为 `1.0`。Strict successor coverage 保持 `17/41`，strict anchor-path coverage 保持 `32/41`，reviewable path 保持 `41/41`；stream `needs-structure-evidence` 从 `3` 降到 `2`，candidate successor disagreement 从 `108` 降到 `82`。 |
| JD 首页第 1 页 | 93 / 127 | `0.99536129` | 全部 35 个 native grid-island 元素保持受保护。5 条 Docling stream 解析 26 个成员和 3 条 relation edge；stream `needs-structure-evidence` 从 `2` 降到 `1`，candidate successor disagreement 从 `114` 降到 `47`。 |

同一条已检查打印路径还重跑了 PUMA 第 5 页 `0.95767110` 和比亚迪第 136 页 `0.96518593`，两者 A/B 视觉 delta 均为 0。比亚迪仍会获得一个 `table-island` stream 和 1 个 page `needs-structure-evidence` 改善；这些重跑验证的是渲染稳定性，不是新增的人工语义标签。

### 阅读顺序 sidecar proposal v3

每个 benchmark branch 现在都会写入可审查的 `reading-order.sidecar.proposal.json`。下表刻意区分严格可执行的局部边、仅供复核的局部边，以及仅供复核的跨 stream transition。没有人工标注的样本，这些不是语义正确率。

| 样本 | Native proposal：streams / strict / review / transitions | Structure proposal：streams / strict / review / transitions | 解读 |
|---|---:|---:|---|
| Transformer-XL 第 1-3 页 | 18 / 299 / 4 / 15 | 18 / 287 / 16 / 25 | 结构证据把少量局部边从自动执行移到 review，而不是虚构跨栏的全局顺序。 |
| JD 首页第 1 页 | 10 / 39 / 85 / 11 | 16 / 39 / 79 / 24 | 模型 block 新增局部 stream boundary 和 grid/body partition，同时保持相同的 strict edge 数。 |
| PUMA 第 5 页 | 2 / 1 / 22 / 1 | 2 / 1 / 22 / 1 | local-stream guard 正确避免 generic model text block 把稳定的 native flow 切碎。 |
| 比亚迪财务报告第 136 页 | 17 / 17 / 0 / 16 | 17 / 17 / 0 / 16 | 表格证据把一个 stream type 改为 `table-island`；高置信局部 chain 保持稳定。 |

Transformer-XL 是本 proposal layer 的有标注检查。在 `outputs/external/transformer-xl-ppstructurev3-ab-pages-1-3-sidecar-proposal-v3` 中，native-only direct strict 边的已标注 precision 为 `17/17`，覆盖 `17/41` 个锚点 transition（`0.41463415`）。native-plus-structure 的 direct strict precision 为 `15/15`，覆盖 `15/41`（`0.36585366`）；移入 review 的两条边均正确，因此 direct strict 加 review 的 coverage 仍为 `17/41`（`0.41463415`）。

但这个 direct 指标不足以评估 `ordered-subsequence` 语义 sidecar：标签可以跳过未标注的 IR 节点。新的路径指标会检查相邻标注锚点之间是否存在路径，同时不允许路径穿过另一个已标注锚点。native-only 的 strict 局部路径 coverage 为 `32/41`（`0.78048780`），包含 review graph path 后为 `41/41`（`1.0`）；native-plus-structure 分别为 strict local `30/41`、strict 加 local review `32/41`、reviewable graph `41/41`。native 中最后的 9 条 anchor transition 只依赖跨 stream 的 review handoff，因此仍不可自动执行。这修正了部分标签的计分缺口，但不把 review transition 宣称为安全的自动版面约束。

### 显式 block-order review transition v2

Paddle layout 输出经常只有有序 block，没有 successor relation。Sidecar schema `1.1` 只把唯一、显式编号、数值连续的 primary text block 转成带完整 provenance 的 review transition。Secondary 内容和非线性 island 都是断点，缺失数值 order 时不能跨越，strict transition 数固定为 0。新的 benchmark 字段把这些模型提案与 native 局部边、通用 selected-order handoff 分开计分。

Image A/B 现在使用双输入边界：`--ocr-json` 在两侧创建完全相同的 text/bbox anchors，`--structure-json` 只进入 structure 分支。PP-Structure 中精确但无序的 OCR 行可以关联到唯一的显式 ordered parent；精确 anchor 仍拥有 label/bbox/confidence，parent order 只作为 `ordered_companion` review 证据，不能触发 runtime partial order 或派生 block stream。不同 order 的重叠 parent 会被拒绝。ROOR 的 `ro_linkings` 只存在于相邻评测 sidecar，输入 anchors 已删除全部答案关系。

| Provider / 样本 | Review candidates | 已标注 / 正确 | Precision | 标签覆盖 | Strict | Visual delta |
|---|---:|---:|---:|---:|---:|---:|
| PaddleOCR-VL 1.6，Attention p. 1 | 2 | 1 / 1 | `1.0` | `1/9`（`0.11111111`） | 0 | `0.0` |
| PP-StructureV3 runner，Attention p. 1 | 1 | 0 / 0 | 不可用 | `0/9` | 0 | `0.0` |
| PP-StructureV3，Transformer-XL pp. 1-3 | 12 | 5 / 5 | `1.0` | `5/41`（`0.12195122`） | 0 | `0.0` |
| PP-StructureV3，固定 ROOR 前缀 5 页 | 4 | 4 / 4 | `1.0` | `4/205`（`0.01951220`） | 0 | `0.0` |

10 条已标注 transition 均正确是正向信号，但覆盖仍太稀疏，不足以把 block order 提升为 runtime constraint。Ordered-parent fusion 把 Transformer 的正确覆盖从 `3/41` 提高到 `5/41`；review-only 隔离后 selected successor delta、order-driven reorder 和 visual delta 都保持 0。进一步把模型 block 改为从属 subgroup 后，Transformer 的 strict anchor-path coverage 从 native `0.78048780` 提升到 structure `0.80487805`，reviewable path 两侧均为 `1.0`，stream `needs-structure-evidence` 从 `3` 降到 `2`。

固定 ROOR 五页是 `82251504`、`82837252`、`85201976`、`86263525`、`93106788`，不是按结果选择。4 条提案全部来自 `86263525`，该页为 `4/24` 正确，并把 stream `needs-structure-evidence` 从 `4` 降到 `2`；其余四页没有满足 guard 的提案。五页的 strict transition、order-driven reorder、selected successor delta 和 visual delta 全部为 0。产物位于 `outputs/research/*-block-transitions-v3` 与 `outputs/research/roor-pp-structure-block-transitions-v4`。

### Surya FastLayout learned-order review v1

`scriptorium run-surya-layout` 会运行 Surya 0.21.1 FastLayout 及其 learned order head，并保存可重放的 structure JSON。命令要求显式接受模型许可；若 order head、detector feature、模型容量或完整整数 permutation 缺失，会直接失败而不接受 raster fallback。本次权重声明的 order 容量为 128 个 box。所有 label/order/successor edge 都带 review-only 的 semantic/order/relation policy，因此不能改变 role、stream、semantic-layer ownership 或 runtime order。

先评测固定 ROOR 五页，再在不修改 provider 阈值和融合规则的前提下运行 held-out Attention、Transformer-XL、JD 与 PUMA：

| 样本 | Review candidates | 已标注 / 正确 | Precision | 正确标签覆盖 | 完整 external candidate | Runtime / visual delta |
|---|---:|---:|---:|---:|---:|---:|
| 固定 ROOR 前缀 5 页 | 42 | 41 / 30 | `0.73170732` | `30/205`（`0.14634146`） | relation successor `99/205`（`0.48292683`） | 0 / `0.0` |
| Transformer-XL pp. 1-3 | 23 | 9 / 2 | `0.22222222` | `2/41`（`0.04878049`） | successor `21/41`（`0.51219512`） | 0 / `0.0` |
| Attention p. 1 | 3 | 1 / 1 | `1.0` | `1/9`（`0.11111111`） | successor `9/9`（`1.0`） | 0 / `0.0` |
| JD 首页 | 5 | 无标签 | 不可用 | 不可用 | 无标签 | 0 / `0.0` |
| PUMA p. 5 | 4 | 无标签 | 不可用 | 不可用 | 无标签 | 0 / `0.0` |

ROOR 运行让 stream `needs-structure-evidence` 减少 4，但 held-out Transformer 的 precision 明显下降，直接否定了通用 runtime promotion。语义隔离前，Surya label/relation 会间接改变 sidecar role/stream 构造：Transformer strict anchor-path coverage 从 native `32/41` 回退到 `20/41`，Attention 则从 `3/9` 变成 `6/9`。落实 `semantic_policy: review-only` 后，两者都保留 native strict path（`32/41` 与 `3/9`），模型提案仍可独立计分。Strict block transition、relation/order-driven reorder 与 visual delta 始终为 0。

产物位于 `outputs/research/surya-fast-layout-roor-v1/fixed-five-semantic-isolated-ab` 和 `outputs/research/surya-fast-layout-heldout-v1/*-semantic-isolated-ab`。结论是保留为 review provider，不作为 runtime reading-order driver。

### OpenDataLoader XY-Cut++ review v1

OpenDataLoader PDF 2.4.7 提供 Apache-2.0、确定性的 CPU/Java XY-Cut++ 路径。
`scriptorium run-opendataloader` 会保留 raw JSON，并输出带稳定 id、top-left PDF
坐标及 review-only block order/successor edge 的 normalized replay。本次 A/B 前已
从源 PDF 重新生成 CLI 输出，没有使用手写保存的 sidecar。

| 样本 | Blocks / provider edges | Block review candidates | 已标注 / 正确 | 完整 external successor | Stream needs delta | Runtime / visual delta |
|---|---:|---:|---:|---:|---:|---:|
| Attention p. 1 | 22 / 21 | 4 | 1 / 1 | `9/9`（`1.0`） | `+1` | 0 / `0.0` |
| Transformer-XL pp. 1-3 | 57 / 54 | 29 | 11 / 11 | `34/41`（`0.82926829`） | `-1` | 0 / `0.0` |

Transformer 的 external candidate 明显高于 Surya held-out 的 `21/41`，但仍低于
selected native order 的 `41/41`。该样本上的已标注 block-review edge 很精确，
但独立性和覆盖仍不足以允许 runtime promotion。OpenDataLoader 与现有
PP-StructureV3 proposal 求交后，从 32 条 provider 唯一候选得到 9 条 consensus
edge，其中 4 条有标签且全部正确（覆盖 `4/41`）。它仍是降低 review 噪声的工具，
不是 accepted relation model。产物位于
`outputs/research/opendataloader-xycut-v1`；可复现重跑位于被忽略的 `replay-cli`
子目录。

### 独立 provider consensus v1

`scriptorium consensus-reading-sidecars` 会对至少两个独立 provider 的显式 block-order review transition 求交。它会拒绝 page 集合不一致或 stable document fingerprint（element id、text、PDF bbox）不一致的输入，保留 provider/confidence provenance，并始终输出尚未 accepted 的 review-only proposal，且 `runtime_reorder: false`。

| 样本 | Providers | Provider candidate edges | Consensus edges | 已标注 / 正确 | 正确标签覆盖 |
|---|---:|---:|---:|---:|---:|
| Attention p. 1 | 3 | 4 | 2 | 1 / 1 | `1/9`（`0.11111111`） |
| Transformer-XL pp. 1-3 | 2 | 33 | 2 | 1 / 1 | `1/41`（`0.02439024`） |
| 固定 ROOR 前缀 5 页 | 2 | 42 | 4 | 4 / 4 | `4/205`（`0.01951220`） |
| PUMA p. 5 | 2 | 4 | 3 | 无标签 | 不可用 |

在有标签集合上，consensus 产出 8 条候选，其中 6 条有标签且全部正确。已标注候选 precision 为 `6/6`，但正确覆盖只有 `6/255`（`0.02352941`）。它适合降低 review 噪声，还不足以接受边或扩大 runtime arbitration。产物位于 `outputs/research/provider-consensus-v1`。

### Secondary block subgroup v1

Ordered model block 只有在所有成员共享同一个 native flow segment 和 column 时才会派生。它现在写入 `external_structure_stream_*` 且标记 `primary = false`，不再覆盖主 `reading_order_stream_*`；HTML 同时输出独立的 `data-scriptorium-structure-stream-*`，供翻译器在稳定主流内按 paragraph/block 分组。Block transition 的 review provenance 仍保留。

| Provider / 样本 | Derived blocks / members | Stream needs：native -> structure | Strict anchor path | 其他结果 |
|---|---:|---:|---:|---|
| PaddleOCR-VL 1.6，Attention p. 1 | 2 / 17 | `0 -> 0` | `0.33333333 -> 0.33333333` | 2 个 block review 候选保持不变；successor/visual delta 为 0。 |
| PP-StructureV3 runner，Attention p. 1 | 2 / 17 | `0 -> 0` | `0.33333333 -> 0.33333333` | 1 个未标注 block review 候选保持不变；successor/visual delta 为 0。 |
| PP-StructureV3，Transformer-XL pp. 1-3 | 21 / 158 | `3 -> 2` | `0.78048780 -> 0.80487805` | 12 个候选仍为 `5/5`；successor-consensus disagreement `-26`，selected successor/visual delta 为 0。 |
| PP-StructureV3，JD 首页 | 5 / 29 | `1 -> 1` | 无标签 | 修复旧的 `1 -> 2` stream 回退，同时保留 successor-consensus disagreement `-62`；visual delta 为 0。 |
| PP-StructureV3，PUMA p. 5 | 4 / 15 | `0 -> 0` | 无标签 | 主流诊断和 visual delta 均不变。 |

JD/PUMA 没有人工 relation 标签，因此其行只证明“没有再因 block 分组制造主流碎片”，不是语义正确率。产物位于 `outputs/research/*-secondary-block-streams-v1`。

### Evidence-gated local promotion v1

`reading_order_confidence` 描述的是页面策略，而不是单条边的可信度。Sidecar 现在只有在边位于同一个 provisional stream 且三类独立证据同时成立时，才把 review-only edge 提升为 strict：互为最近的前向几何邻居、全页 relation graph 实际选中且 score 至少为 `0.86`、visual-YX、box-flow、relation-graph 三个 stream candidate 都给出直接 successor。即使三项都通过，只要 relation graph 存在完全同分的可行替代边，也会阻止提升。Proposal 会写入 `geometry-mutual-neighbor`、`relation-graph-selected`、`stream-consensus-3-of-3`；同分 review edge 还会带上选择时的 `relation_graph` margin 诊断。跨 stream transition 始终保持 review-only。

下面的重跑产物位于 `outputs/external/*-edge-evidence-v1`。计数格式为 streams / strict / review / transitions。未标注样本的计数只是 proposal 证据，不是正确率。

| 样本 | Native proposal | Native-plus-structure proposal | 结果 |
|---|---:|---:|---|
| Transformer-XL pp. 1-3 | 18 / 299 / 4 / 15 | 18 / 299 / 4 / 21 | 没有 review edge 满足全部 gate。strict anchor-path coverage 仍为 native `32/41`、structure `30/41`；两侧 reviewable path 都是 `41/41`。 |
| PUMA 年报 p. 5 | 2 / 13 / 10 / 1 | 2 / 13 / 10 / 1 | 相比之前的 `1 / 22` 局部拆分，有 12 条稳定正文边从 review 升为 strict。PUMA 还没有 semantic sidecar，因此这里只能说明证据覆盖，不能当作正确率。 |
| JD 首页 p. 1 | 10 / 39 / 85 / 11 | 15 / 39 / 80 / 45 | 没有低共识 OCR/card edge 被自动提升。structure 分支仍有 `-60` successor-consensus disagreement delta，但 raw block boundary 让 review transition 增多。 |
| BYD 年报 p. 136 | 17 / 17 / 0 / 16 | 17 / 17 / 0 / 19 | 表格型内容没有被意外提升。本轮 raw PP-Structure page JSON 也没有降低 stream `needs-structure-evidence` 计数。 |

这个 gate 是 precision-first 的：它展示了如何恢复稳定的局部链，而不是把整页低置信策略都改成可执行关系。下一次能扩大自动提升范围的依据应是更多复杂文档上的 labelled relation 或 stream coverage，而不是更大的 raw strict-edge 数量。

比亚迪第 136 页的 pseudo-translation A/B 仍有 17 个 overflow 和 17 个 conflict，因此表格结构本身不是 fidelity 修复。它把 10 个 replacement 正确归入 `table-island`，并把 9 个 conflict 归因到同一个局部流，而不是正文流。这就是后续 table-aware mask padding 和 text fitting 的可量化目标。

### Relation-Graph 选择歧义

Relation graph 现在报告选择时的替代边，而不是只给出序列化 candidate order。`path_cover_edge_count` 不包含序列化时拼接的 handoff；`tied_edge_count` 只统计完全同分的可行替代项；`mean_minimum_margin` 只汇总存在替代项的边。它们不是正确率，也不是 runtime 切换阈值。

| 样本 | 输出目录 | Path-cover 边 | 完全同分 | 平均最小 Margin | 结果 |
|---|---|---:|---:|---:|---|
| Transformer-XL pp. 1-3 | `outputs/external/transformer-xl-relation-ambiguity-v1` | 288 | 3 (1.041667%) | 0.00123018 | 视觉 `0.98160664`，semantic pair/successor accuracy 仍为 `1.0`；同分边保持 review-only。 |
| PUMA pp. 1-12 | `outputs/external/puma-2024-annual-report-relation-ambiguity-v1` | 329 | 0 | 0.03710031 | 视觉 `0.9795117`；主要未解问题是弱/缺失 structure evidence，不是完全同分。 |
| JD 截图 PDF | `outputs/external/jd-home-relation-ambiguity-v1` | 93 | 2 (2.150538%) | 0.03896739 | 视觉 `0.99576887`；仍需要显式 local stream 或 successor relation。 |
| 比亚迪 p. 136 | `outputs/external/byd-2024-annual-report-relation-ambiguity-v1` | 30 | 0 | 0.09570952 | 视觉 `1.0`；即使没有完全同分，表格/翻译流仍需结构证据。 |

这验证了 margin gate 不是 semantic structure 的替代品：它只阻止任意提升，真正解决剩余低证据局部流仍需要 PaddleOCR-VL/PP-Structure/Docling 的 relation 或 stream 输出。

## 原生局部结构证据 v1

`outputs/external/local-structure-evidence-v2` 用同一组 PUMA、JD、Hacker News 的 15 页翻译压力样本在 144 DPI、raster fidelity 下重跑。它不改变 selected page order 或视觉渲染，而是把原生 table/grid island 的 successor 与通用 page-level candidate vote 分开。`local_structure_successor_coverage` 表示 island 潜在边中的严格边覆盖率；consensus-conflict 列则表示通用 successor-consensus 没有保留的严格局部边。

| 样本 | 原生局部 stream | 严格局部边 | 严格覆盖率 | 与通用 consensus 冲突的局部边 | 局部边在全页 reference 的覆盖率 | 流级建议 |
|---|---:|---:|---:|---:|---:|---|
| PUMA 年报 pp. 1-12 | 6 | 71 | 1.0 | 50 / 71 | 0.13948919 | 6 个 `keep-selected-local-structure` |
| JD 首页截图 PDF | 3 | 32 | 1.0 | 30 / 32 | 0.24060150 | 3 个 `keep-selected-local-structure` |
| Hacker News 打印 PDF | 0 | 0 | 0.0 | 0 / 0 | 0.0 | 无 |

合并后得到 9 条原生局部 stream、103 条严格边、完整严格覆盖率，以及 80 / 103（`0.77669903`）条会被通用 consensus 打断的严格边。这不是语义正确率提升：这些受保护边本来就由原生 table/grid detector 选中。它的作用是把分歧如实暴露出来，避免通用 page candidate 把已有完整证据的 island 误报成 `needs-structure-evidence`。

两个当前 PP-StructureV3 A/B control 说明这些维度必须一起看。JD 的 `outputs/external/jd-local-structure-ppstructure-ab-v1` 匹配 128 个结构元素后，三条原生局部 stream 和 32 条严格边保持不变，局部边与 consensus 的冲突从 30 降到 13，但 stream `needs-structure-evidence` 从 1 增至 2；视觉和 reading-risk delta 都是 0。PUMA p. 5 的 `outputs/external/puma-local-structure-ppstructure-ab-v1` 匹配 24 个元素并派生 4 条受限 body stream，但该页没有原生 local table/grid island，所以所有 local-structure delta 均为 0。两次运行都没有提供显式 relation 或 stream edge，因此都不能推动 runtime order 切换。

## 受保护局部约束 v1

`outputs/external/protected-local-structure-v1` 用同一组 15 页重跑 `protected_successor_consensus`。它只是一条诊断候选：有效的原生 table/grid strict edge 会先进入 degree-constrained DAG，之后才考虑通用 candidate edge，因此不会被当作伪造 vote，也不会制造跨 stream relation。报告会区分 protected edge、unresolved constraint、各类拒绝原因，以及约束序列化后仍未保留的 strict edge。

| 样本 | 严格局部边 | 被通用 consensus 打断 | 已保护 | 未解决 | 约束 consensus 后仍缺失 |
|---|---:|---:|---:|---:|---:|
| PUMA 年报 pp. 1-12 | 71 | 50 | 71 | 0 | 0 |
| JD 首页截图 PDF | 32 | 30 | 32 | 0 | 0 |
| Hacker News 打印 PDF | 0 | 0 | 0 | 0 | 0 |

合并后，103 条严格局部边全部作为约束被接受；通用 consensus 原本会打断的 80 条局部边都被约束候选保留；没有出现 unknown endpoint、degree 或 cycle 拒绝。视觉相似度仍为 `0.92760169`，与 browser-fit 基线相同，因为 selected IR order 和视觉渲染都没有变化。这证明的是局部约束链路，不是 semantic accuracy：PUMA 和 JD 仍没有已跟踪的 relation-style ground truth，而有标注的 Hacker News 没有适用的原生 table/grid constraint。因此 protected candidate 的聚合 semantic 指标为不可用（`null`），它仍被排除在 runtime 和自动 candidate 建议之外。

## ROOR 关系基准 v1

[ROOR](https://github.com/chongzhangFDU/ROOR-Datasets) 将版面阅读顺序标注为 document segment 之间有向的即时 successor 关系（`ro_linkings`）。`scriptorium fetch-roor` 下载官方发布 split 的固定前缀，不会按运行结果筛选样本；它固定在 upstream revision `6b5ca2b2cc6ad02ab1dd8ec1c17551ab614f0aa0`，并把 revision 写入 manifest。每页会保存 source image、原始 annotation、仅用于评测的相邻 `.semantic-order.json`，以及派生的 `layout-anchor-only` structure JSON。后者只保留 image metadata 和 `document` text/bbox anchor，并删除 `ro_linkings`、`label_entities` 及全部 task label，因此答案关系不会通过 `--structure-json` 进入融合。

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

完整官方 `val` split 有 49 页。提供的 2,602 个 layout anchor 全部匹配生成的 IR；转换器没有得到任何官方 relation。49 页全部通过稳定 element ID 解析 relation endpoint，未解析 identifier 为 0。标注的即时 successor relation 共 2,612 条。这里必须使用 ID：34 页存在重复 segment text，text-only normalization 会压缩不同的 relation。此设置的 source-image fidelity 分数会机械地得到 `1.0`，因为 raster fidelity 保留输入图片；它不是阅读顺序质量证据。

| 证据 / 候选 | 正确 / 已标注 relation | Relation successor accuracy | 范围 |
|---|---:|---:|---|
| Selected native order | 1,274 / 2,612 | `0.48774885` | 全部已标注 relation |
| Generic `successor_consensus` | 1,043 / 2,612 | `0.39931087` | 全部已标注 relation |
| 诊断用 `protected_successor_consensus` | 778 / 1,856 | `0.41918103` | 只包含该 candidate 存在的页面 |
| Strict native local table/grid proposal edge | 316 / 617 | `0.51215559` | endpoint 都有直接标注的 proposal edge |
| Strict native table-island proposal edge | 227 / 406 | `0.55911330` | endpoint 都有直接标注的 table edge |
| Strict native grid-island proposal edge | 89 / 211 | `0.42180095` | endpoint 都有直接标注的 grid edge |

这是 oracle-layout/order 评测，不是端到端 OCR 分数：ROOR 提供 text 和 bbox，因此它隔离评估 reading-order 问题。它也推翻了“native geometry table/grid chain 可以自动成为 runtime hard constraint”的早期直觉。约束序列化能够保留这些 chain，但在独立 relation 集上 chain 本身只有约 51% precision，protected candidate 也没有超过 selected native order。因此 native local edge 继续只作为 review 和 translation-stream evidence。runtime hard constraint 必须来自显式 external successor/stream relation、经过验证的 relation predictor，或已接受的人工复核。

## 翻译压力测试结果

`outputs/external/translation-stress-padding-v1` 会把确定性伪扩展译文写入 `translated_text`，再把 fidelity HTML 打印回 PDF，同时测量视觉相似度和 replacement 风险。该运行覆盖 PUMA、JD、web-HN 共 15 页，`mismatched_case_count = 0`，`dimension_match_rate = 1.0`，`page_count_match_rate = 1.0`。该运行使用 `fidelity-replacement-fit-v2`：不改变文本坐标或 fitting 策略，只让局部 mask padding 在相邻可见元素处停止。

| 样本 | 页数 | 选择路径 | 视觉相似度 | 最大差异 | 平均差异 | 翻译元素 | 扩展倍率 | Overflow | Conflict | 冲突目标 | 受约束 Mask / 方向边 | 最小 Fit | 平均 Fit | Grid Islands | 页数/尺寸匹配 | Semantic Successor |
|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---:|
| PUMA 2024 Annual Report | 12 | `fidelity/svg` | 0.67731004 | 0.32268996 | 0.12552682 | 398 | 1.99511484 | 187 | 306 | 320 | 314 / 529 | 0.62 | 0.68186231 | 31 | yes / yes | n/a |
| JD 首页截图 PDF | 1 | `fidelity/raster` | 0.87466976 | 0.12533024 | 0.12533024 | 104 | 6.15817223 | 97 | 104 | 189 | 50 / 110 | 0.62 | 0.63202981 | 35 | yes / yes | n/a |
| Hacker News 打印 PDF | 2 | `fidelity/raster` | 0.90613373 | 0.09386627 | 0.04964834 | 65 | 2.23526357 | 42 | 65 | 69 | 32 / 33 | 0.62 | 0.63153077 | 0 | yes / yes | 1.0 |

合并摘要：平均视觉相似度为 `0.81937118`，最大差异为 `0.32268996`，平均差异为 `0.10016847`，p95 差异为 `0.30295399`；总翻译元素 `567`，总 overflow `326`，总 conflict `475`，总冲突目标 `578`。本次共约束 `396` 个 replacement mask、`672` 个方向边。和同输入的 v1 baseline 相比，conflict 减少 `90`，冲突目标减少 `321`，overflow 不变；这说明它提高了 mask 安全性，而不是解决长译文 fitting。`grid_island_element_count` 合计 `66`，其中 PUMA 为 31，JD 为 35。

### Browser-Layout Fit v3

`outputs/external/translation-stress-browser-fit-v2` 在 v3 browser-layout replacement pass 后重跑同样 15 页。三个选中的 case 都使用 raster fidelity；生成 HTML 会在 Chromium 切换到 print media、字体 ready 后实测，并为每个 case 写出 `quality/fidelity_replacement_layout_report.json` sidecar。导出 PDF 前会执行同一轮 fitting。

| 样本 | 页数 | 选择路径 | 视觉相似度 | 静态估算 Overflow | Chromium 实测裁切 | 实际平均 Fit | Browser Fitted / 行高压缩 | 深色 Mask | 页数/尺寸匹配 |
|---|---:|---|---:|---:|---:|---:|---:|---:|---|
| PUMA 2024 年报 | 12 | `fidelity/raster` | 0.89910421 | 187 | 0 | 0.95120879 | 398 / 13 | 101 | yes / yes |
| JD 首页截图 PDF | 1 | `fidelity/raster` | 0.95797219 | 97 | 79 | 0.67350577 | 104 / 8 | 0 | yes / yes |
| Hacker News 打印 PDF | 2 | `fidelity/raster` | 0.92572866 | 42 | 2 | 0.92540769 | 65 / 60 | 0 | yes / yes |

合并视觉相似度为 `0.92760169`；max / mean / p95 diff 为 `0.10089579` / `0.04620805` / `0.09823334`。全部 567 个 replacement 都经过 browser fitting；实际平均 fit scale 为 `0.89731428`，静态为 `0.66695203`，81 个选择压缩行高，101 个使用深色背景采样 mask。本运行记录 326 个静态估算 overflow 和 81 个浏览器实测裁切；它们刻意是不同指标，不能写成直接的 `326 -> 81` 前后降幅。按实测 clipping policy，conflict 为 400，冲突目标为 578。

这是端到端分数，不是单变量因果归因：PUMA 在 v3 选择 raster，而历史 padding-only 表选择 SVG；本运行还包含 print-DPI 坐标修复和深色 mask 行为。JD 剩余的 79 个真实裁切是最主要的通用 reflow 目标，应通过局部 stream/region layout 解决，而不是写样例特例。

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

## Docling ROOR 全量验证

隔离的 Docling 实验使用官方 ROOR validation split 全部 49 页。官方
`ro_linkings` 不参与抽取，只用于评分；A/B 两个分支共享同一份 layout-anchor OCR
JSON。

| 候选 | 正确 | 已标注 | 准确率 |
|---|---:|---:|---:|
| Selected native，两分支一致 | 1274 | 2612 | 0.48774885 |
| 隔离的 Docling external candidate | 785 | 1888 | 0.41578390 |
| Native successor consensus | 1043 | 2612 | 0.39931087 |

Docling 在 38 页上形成完整可比候选：3 页更好、3 页相同、32 页更差。安全不变量
保持不变：两分支均为 241 个 grid-island element、92 个 stream-level 和 49 个
page-level `needs-structure-evidence`，视觉相似度 1.0，provider stream 为 0，重排页
为 0。运行产生 1,916 个 review region 和 1,522 条 review relation。这是明确的
负向泛化证据：Docling 继续作为隔离审查候选，不作为 runtime orderer。

## 学习式后继模型可用性审计

ROOR 官方仓库使用 Apache-2.0，并已包含 relation-prediction 代码，但作者明确说明
受单位政策限制，无法发布微调后的 ROP 权重。FocalOrder 公布了实验结果，却没有可复现
代码或 checkpoint。两者目前都不能形成可执行 provider benchmark。

作为外部研究对照，本轮在 held-out Transformer-XL 第 2 页运行了 OpenRAIL 的
HURIDOCS LightGBM 权重。其两阶段 predictor 先选 18 个后继候选，再做 pairwise
next-token ranking。bbox/text 匹配后解析出 107 个元素和 86 条 relation；隔离的
external candidate 在已标注后继上为 `13/16`（`0.8125`）。selected native 保持
`16/16`，box-flow 和 relation-graph 均为 `8/16`。由于必需的 GitHub 推理仓库没有
声明代码许可，项目不集成该 provider。本次运行同时发现并验证了通用隔离契约修复：
native 与 structure 分支的 successor consensus 都保持 `8/16`，page recommendation
都保持 `needs-structure-evidence`。

## ROOR 训练的 Relation Ranker

可复现 ranker 使用官方 train split 中 122 个文档拟合，并用 UID hash 划出的 27 个
train 文档校准。validation 评分前阈值已固定为 `0.16`；calibration precision 为
`0.66132556`、recall 为 `0.64857143`、F1 为 `0.65488640`。项目跟踪的 49 页
validation 与 train index 没有样本重叠。

branch gate 使用同一条 train/calibration 边界。其固定 calibration 阈值为 `0.75`，
对应 relation F1 `0.66737288`；阈值选择没有使用 validation 结果。

| 指标 | Native selected | Top-1 ranker | Branching ranker |
|---|---:|---:|---:|
| candidate 解码后命中的官方 relation / 总数 | 1274/2612 | 1513/2612 | 1529/2612 |
| 官方 relation accuracy | 0.48774885 | 0.57924962 | 0.58537519 |
| 直接预测边 precision | n/a | 0.68715305 | 0.67794118 |
| 直接预测边 recall | n/a | 0.66347626 | 0.70597243 |
| 直接预测边 F1 | n/a | 0.67510713 | 0.69167292 |

branching external candidate 相对 native 的 relation accuracy 绝对提升 `0.09762634`，
并在 49 页中的 35 页成为最佳候选。模型增加 198 条校准后的 rank-2 edge，共预测
2,720 条 review edge，其中 2,686 条解析到 IR 元素。视觉
相似度、grid-island 数、selected order 和重排页数均不变。过滤 isolated relation
provenance 后，两分支的 stream recommendation 也完全一致，包括 92 个
`needs-structure-evidence` stream。该结果足以继续把模型作为独立候选开发，但还不足以
提升到 runtime order：解码后 accuracy 仍只有 0.585，且部分多页/form-like 样本的
直接 precision 较低。

### 跨域 DocumentIR Replay

不做再训练，直接把同一 ROOR 模型应用到两个论文族的 native PDF `DocumentIR`
anchor：

| 样本 | Selected | External ranker | Relation graph | Box flow |
|---|---:|---:|---:|---:|
| Transformer-XL pp. 1-3 | 41/41 | 29/41 | 22/41 | 14/41 |
| Attention pp. 1-3, 12-13 | 33/33 | 29/33 | 21/33 | 18/33 |

external candidate 跨域后仍高于几何 baseline，但没有超过成熟的 native 论文顺序。
502 条预测 relation 全部解析；视觉、selected order、consensus、stream diagnostics 和
runtime reorder delta 都为 0。

仅由 fit 数据计算的 feature envelope 揭示了 confidence 看不到的域漂移。ROOR
validation 的 feature-value OOD 中位数为 `0.02000`、最大值为 `0.04875`；
Transformer 页面为 `0.03028-0.03797`，Attention 页面为 `0.05309-0.10667`。
PUMA p. 5 为 `0.07304`，JD 首页为 `0.08274`，但二者 mean pair confidence 仍达到
`0.86286` 和 `0.90136`。因此 OOD 是 rejection/triage 诊断，不是分数修正或正确率
声明。PUMA 23/23、JD 146/146 review edge 均解析成功，重排、视觉和 stream
diagnostic delta 都为 0。

### Comp-HRDoc 固定 Test Prefix

两个官方 test 文档按发布文件名顺序选择，而不是按 benchmark 结果选择：
`1401.3699` 和 `1402.2741`，各取前 5 页。arXiv PDF 渲染到官方 Comp-HRDoc
尺寸，line anchor 和 relation-only sidecar 来自固定版本的 unified test annotation。

| 范围 | Native selected | External decoded | Relation graph | Visual-YX | Box flow |
|---|---:|---:|---:|---:|---:|
| `1401.3699`, pp. 1-5 | 191/205 | 205/205 | 205/205 | 205/205 | 100/205 |
| `1402.2741`, pp. 1-5 | 155/155 | 155/155 | 155/155 | 155/155 | 122/155 |
| 合计 | 346/360 | 360/360 | 360/360 | 360/360 | 222/360 |

10 页上，原始 learned edge precision 为 `0.93472585`、recall 为 `0.99444444`、
F1 为 `0.96366083`（358 correct / 383 predicted / 360 labels），383 条预测 relation
全部解析。视觉、selected order、consensus、stream diagnostics 和 reorder delta 都为
0。该结果独立确认了较强的 line/paragraph continuity，但 visual-yx 和 relation graph
也得到 360/360，因为这些页面主要是局部 textline chain。不能据此声称 floating
figure、table、annual report 或 portal grid 已经解决。

### Comp-HRDoc Floating-Role Replay

保留图形 node 后重放 `1401.3699` 前 5 页。由于第 2 页包含两条官方
`figure -> caption` edge，label 从 205 增加到 207。raw prediction 从
204/217 correct（precision `0.94009217`、recall `0.98550725`、F1 `0.96226415`）
提高到 206/219（precision `0.94063927`、recall `0.99516908`、F1 `0.96713615`）。
两条 structure-role edge 都正确。下游 external/relation-graph 仍为 207/207，
visual similarity 仍为 `0.97221549`，runtime 和 stream diagnostic delta 全为 0。
这个只含两条 floating edge 的 replay 验证了机制，不代表广泛的 floating layout 已解决。

### 扩展 Floating Figure/Table Prefix

更大的固定 replay 使用首个发布 test 文档 `1401.3699` 的全部 27 页，加上
`1411.3334` 前 6 页，后者是按发布顺序首个覆盖 table floating group 的文档
prefix。样本在推理前固定。33 页共有 1,225 条官方 relation 和 18 条图形
relation：15 条 figure-caption、3 条 table-caption。

| Relation source | Correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| 同一模型，禁用 role fusion | 1186 / 1277 / 1225 | 0.92873923 | 0.96816327 | 0.94804157 |
| 启用 structure-role geometry | 1203 / 1293 / 1225 | 0.93039443 | 0.98204082 | 0.95552025 |
| 仅 structure-role edge | 18 / 18 / 18 | 1.00000000 | 1.00000000 | 1.00000000 |

该实验还暴露并修复了 adapter oracle 问题：官方 table group 中，caption annotation
可以在 table annotation 之前或之后。现在两种表示都会将多行 caption 尾行连到
table。推理可使用 layout block id，但官方 reading-order id 仍只存在 sidecar。
18 条 edge 仍不足以支持 runtime 提升；该结果支持当前架构，并说明下一步应扩大
跨文档 floating split。

### 250 页跨文档 Floating Corpus

Annotation-only corpus 在推理前按发布 image-name 顺序选择前 250 个图形
floating 页。共覆盖 53 个 test 文档、10,465 条官方 successor label 和 347 条
floating 图形 label；不下载或重新分发页面图像。同一模型按三种模式运行：

| 模式 | Correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|
| Native ranker | 8895 / 10681 / 10465 | 0.83278719 | 0.84997611 | 0.84129386 |
| Native + global/calibrated structure role | 9193 / 10963 / 10465 | 0.83854784 | 0.87845198 | 0.85803621 |
| Native + trained floating | 9210 / 10964 / 10465 | 0.84002189 | 0.88007645 | 0.85958281 |

Calibrated global structure-role 相对 native 的 F1 delta 为 `+0.01674235`。其
graphical edge 单独对 347 条 label 得到 306/350：precision `0.87428571`、recall
`0.88184438`、F1 `0.87804878`，高于原始 greedy 的 295/342 和 `0.85631350`。

Locality 参数没有使用该 test corpus。受限的官方 train 搜索保持纵向 gap 为 `0.12`
页高，取消强制水平 bbox overlap，并把水平中心距离从 `0.50` 收紧到 `0.35` 页宽。
Fit correct/predicted 为 `5284/5658 -> 5291/5665`；document-hash calibration
partition 为 `1348/1473 -> 1349/1474`，所有变化的 fit/calibration 页都改善。
门槛冻结后，固定 test 只变化 3 页且全部改善；相对仅全局 assignment 增加 5 条 correct、
4 条 prediction。Role fusion 仍保持 review-only。

Learned decoder 现在在阈值校准和推理中都运行 cardinality-first 最大权重全局
assignment。Margin 取 source row 中其他 caption 与 target column 中其他 graphical
block 两类 score gap 的较小者。官方 train document-hash calibration partition 上，
阈值 `0.36` 对 1,446 条 label 得到 1,373/1,489，precision `0.92209537`、recall
`0.94951591`、F1 `0.93560477`，高于 greedy decoder 的 `0.91295681`。250 页 test
没有参与阈值或 reliability 选择。

Decoder 冻结后，graphical test edge 从 321/356、F1 `0.91322902` 提高到 323/357、
F1 `0.91761364`；唯一变化页 `1507.01067_7` 从 2/3 提高到 4/4。整体 F1 相对
native 增加 `+0.01828895`，相对 calibrated global 启发式 fusion 增加 `+0.00154660`。

| Train-calibrated test 子集 | Correct / predicted / labels | Precision | Recall |
|---|---:|---:|---:|
| High-precision review | 235 / 242 / 347 | 0.97107438 | 0.67723343 |
| Review + zero OOD | 204 / 209 / 347 | 0.97607656 | 0.58789625 |
| Strict | 196 / 201 / 347 | 0.97512438 | 0.56484150 |
| Strict + zero OOD | 169 / 173 / 347 | 0.97687861 | 0.48703170 |

Answer-free label audit 得到 306 条局部几何 exact agreement、24 页上的 32 条冲突
官方 graphical label、9 条没有几何建议的 label，以及 12 条没有官方 label 的几何建议。
Clean strict 的 5 条错误全部触及 conflict set 中的 graphical 对象；strict zero-OOD 的
4 条错误也全部如此。这只是诊断上下文，不能用于改写官方分数或 label。

### Body/Floating 联合 Path Cover

同一批 250 页通过共享 degree-one acyclic path cover 解码。它会过滤相互矛盾的
raw edge，只评分被选中的图关系：

| 模式 | Correct / selected / labels | Precision | Recall | F1 | Cycles rejected |
|---|---:|---:|---:|---:|---:|
| Native ranker | 8667 / 9481 / 10465 | 0.91414408 | 0.82818920 | 0.86904643 | 18 |
| Native + calibrated global role | 8951 / 9746 / 10465 | 0.91842807 | 0.85532728 | 0.88575528 | 3 |
| Native + trained floating | 8983 / 9758 / 10465 | 0.92057799 | 0.85838509 | 0.88839440 | 2 |

Trained 模式会优先保护并保留全部 209 条 high-precision zero-OOD floating edge。
全 corpus 共拒绝 14 个 outgoing conflict 和 1,190 个 incoming conflict。约束后 F1 提升是
body 与 float relation 可以共享一张图的真实诊断证据，但不是 runtime promotion：
corpus 使用 oracle layout anchor，strict gate 还必须通过噪声和真实 provider 输入验证。

### 确定性 Layout/OCR Noise Sensitivity

固定 250 页在预定义合成 profile 下重放。Mild 保留 11,267/11,369 个元素和
10,276/10,465 条可解析 label；stress 保留 11,028/11,369 个元素和
9,829/10,465 条 label。

| Profile 与模式 | Raw F1 | Joint F1 | Joint P/R | Protected | Strict P | Strict zero-OOD P |
|---|---:|---:|---:|---:|---:|---:|
| Mild native | 0.81361888 | 0.84100588 | 0.89577708 / 0.79254658 | 0 | n/a | n/a |
| Mild trained floating | 0.82955665 | 0.85764341 | 0.90055713 / 0.81863354 | 170 | 0.96571429 | 0.96551724 |
| Stress native | 0.59203145 | 0.59914677 | 0.71294831 / 0.51667463 | 0 | n/a | n/a |
| Stress trained floating | 0.60318967 | 0.61366500 | 0.71923966 / 0.53511706 | 101 | 0.93495935 | 0.96551724 |

Trained floating evidence 在两个受控 profile 下都保持正向 joint-order delta，但 mild 和
stress 的 strict precision 都没有达到 `0.97` promotion target。Mild 的 6 条 strict 错误
全部触及 audit-conflict graphical；stress 的 8 条中只有 2 条，因此高噪声错误不能由
label 歧义解释。Stress 还包含 2,698 个 fragmented element 和 341 个 dropped element。
因此下一层 reliability 只使用 train-derived perturbation 拟合。

#### Noise-Aware Abstention A/B

官方 train text block 从精确 line polygon 重建。四个 document-hash cross-fit pair model
在 clean/mild/stress view 上生成 15,413 条 held-out correctness record。标准化 L2 logistic
forecaster 只使用 12 个 domain-general 的 score、assignment stability、OOD 与页面规模特征；
不使用 raw coordinate、caption text、profile identity 或 label。Noise-aware gate 是合取式：
forecaster 可以拒绝旧 gate edge，但不能绕过旧 gate 接纳 edge。最终模型字节确定，SHA-256
为 `8fbd68a177b978f23759290a4cc6eaa24586c7a2e3316407377a3135f3f719b1`。

| Train calibration profile | Review correct/predicted | Review P/R | Strict correct/predicted | Strict P/R |
|---|---:|---:|---:|---:|
| Clean | 996 / 1048 | 0.95038168 / 0.68879668 | 856 / 880 | 0.97272727 / 0.59197787 |
| Mild | 879 / 925 | 0.95027027 / 0.60788382 | 748 / 767 | 0.97522816 / 0.51728907 |
| Stress | 621 / 653 | 0.95099541 / 0.42946058 | 514 / 529 | 0.97164461 / 0.35546335 |

Correctness threshold 在 review 为 `0.29`、strict 为 `0.44`，并且必须先通过原
confidence/margin gate。参数冻结后才运行以下 test replay：

| Test profile | 旧 strict | Noise-aware strict | Precision delta | Strict error 位于 audit conflict | Noise-aware joint F1 |
|---|---:|---:|---:|---:|---:|
| Clean | 196 / 201 | 192 / 195 | 0.97512438 -> 0.98461538 | 3 / 3 | 0.88839440 |
| Mild | 169 / 175 | 163 / 167 | 0.96571429 -> 0.97604790 | 4 / 4 | 0.85784363 |
| Stress | 115 / 123 | 109 / 116 | 0.93495935 -> 0.93965517 | 1 / 7 | 0.61366500 |

Review filtering 分别保留 clean/mild/stress 的全部 235/198/133 条正确边，并去掉 1/1/2
条错误。Noise-aware protected path cover 在 clean/stress 不变，在 mild 恢复 2 条 relation。
Stress 仍远低于 promotion target，并有 6 条 strict 错误不在 audit-conflict set。这些合成
结果不能证明真实 OCR 鲁棒性，`runtime_reorder` 继续为 false。

### 真实 PaddleOCR-VL 与 Docling Anchor

PaddleOCR-VL 1.6 和 Docling 2.111.0 + Tesseract 4.1.1 在 `1401.3699` 固定前 5 个渲染
页上运行。该 prefix 主要是单栏：

| Provider | Oracle anchors | Provider anchors | Relation correct / predicted / labels | Precision | Recall | F1 |
|---|---:|---:|---:|---:|---:|---:|
| Docling | 220/224 | 62/66 | 201 / 215 / 207 | 0.93488372 | 0.97101449 | 0.95260663 |
| PaddleOCR-VL 1.6 | 224/224 | 63/66 | 207 / 219 / 207 | 0.94520548 | 1.00000000 | 0.97183099 |

两者都找到 2/2 个 oracle figure。Docling 输出两条正确显式 float edge；Paddle 没有输出
显式 relation。Noise-aware 层保留 Paddle 的 2 条正确 trained review edge；Docling 在该
tier 为 0，两者都没有 strict edge。Combined F1 不变。Graphical-label audit 得到 2/2
exact geometry agreement，官方 label conflict 为 0。

v2 provider 报告不使用 relation label，会分解 recognition/layout degradation。各计数使用
自然分母：missing/hallucination 使用 anchor，split/merge 使用语义 unit，size error 使用已匹配
provider group。

| Provider | Missing | Hallucination | 图内 Nested OCR | Split | Merge | Size error | 字符相似度 | Token F1 | Caption prefix | 最近合成 profile / 距离 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 4/224 | 2/66 | 2/66 | 4/56 | 0/66 | 7/62 | 0.74785572 | 0.71956298 | 0/2 | mild / 0.30899512 |
| PaddleOCR-VL 1.6 | 0/224 | 1/66 | 2/66 | 4/56 | 0/66 | 8/63 | 0.85877792 | 0.88688310 | 2/2 | mild / 0.05474538 |

两个 provider 的页归一化定位误差都很低：Docling p90 center/edge 为
`0.00393755/0.00394033`，Paddle 为 `0.00415761/0.00564990`。Paddle 的 profile 距离更小，
文本保真度也更高，说明合成 mild family 可以局部描述这个 prefix。Docling 虽然最近 mild，
但距离仍很大，主要来自 OCR 文本损失和两个 caption prefix 都损坏；profile 名不能被理解为
已校准的域标签。

固定复杂页 `1412.1395` p. 4 包含两栏、两组交错 figure/caption、code、顶部全宽图和正文：

| Provider | Anchor recall | Figure recall | Relation precision | Relation recall | Relation F1 |
|---|---:|---:|---:|---:|---:|
| Docling | 43/49 | 2/2 | 0.69047619 | 0.74358974 | 0.71604938 |
| PaddleOCR-VL 1.6 | 44/49 | 1/2 | 0.72093023 | 0.79487179 | 0.75609756 |

Degradation 报告改变了对 Docling 70 个 text/caption anchor 的解释。其 74 个 provider anchor 中有
53 个是嵌套在两个 oracle figure 内的小型文本框，主要是图表/图示 OCR，而不是幻检正文区域。
分开这个有用的 nested layer 后，真正 hallucination 为 9/74，missing 为 6/49。Paddle 在该页
没有图内 nested OCR，hallucination 为 2/14、missing 为 4/49，文本保真度更高，但它合并了
14 个 provider unit 中的 2 个，且漏掉一个 figure。上表 raw score 保持不变。但固定版本的
Comp-HRDoc unified annotation 和 `test_eval/1412.1395.json` 都把顶部
Figure 1 bbox 绑定到下方 “Fig. 2” caption，把下方 Figure 2 bbox 绑定到上方 “Fig. 1”
caption。Answer-free 局部几何审计因此将 2/2 条官方 graphical label 标记为 conflict，
但不会用诊断建议替换官方 label。

Docling explicit edge 与两个 provider 的 trained edge 都在预测的 1 条 edge 上与局部几何
建议一致（precision `1.0`，对两条几何建议的 recall 为 `0.5`）。Docling trained edge
没有通过新 strict gate；Paddle edge 同时通过旧 strict 与 noise-aware strict，但在交叉绑定的
官方 raw label 下仍判错。这修正了先前“provider 错配”的判断：该页 provider recognition 与文本顺序仍弱，
但观测到的 float 扣分来自已审计的 oracle conflict，而不是 graphical anchor 重复
assignment。该 edge 仍为 review-only，不会重排 runtime 输出。

| Provider | Missing | Hallucination | 图内 Nested OCR | Split | Merge | Type error | 字符相似度 | Token F1 | Caption prefix | 最近合成 profile / 距离 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Docling | 6/49 | 9/74 | 53/74 | 3/17 | 2/74 | 0/43 | 0.70025503 | 0.64571963 | 1/2 | mild / 0.20701574 |
| PaddleOCR-VL 1.6 | 4/49 | 2/14 | 0/14 | 3/17 | 2/14 | 1/45 | 0.75789504 | 0.76982884 | 2/2 | mild / 0.12464003 |

Docling 到 clean/mild/stress 的距离几乎相同（`0.21559071/0.20701574/0.21405694`），这是当前
合成扰动没有建模其“图内 OCR”域的直接证据。因此这些诊断仍只是 benchmark evidence；
观察该页后没有改动任何 threshold、relation label 或 runtime gate。

## Train-Only 多栏 Provider 校准

此前固定 5 页 prefix 主要是单栏，而且来自公开 test split。现在新增命令从官方
Comp-HRDoc **train** annotation 和原始 arXiv PDF 重建确定性的真实 provider 语料：

```bash
scriptorium fetch-comphrdoc-provider-calibration \
  --sample-count 8 --document-count 4 --calibration-fraction 0.2 \
  --out-dir data/external/comphrdoc-provider-calibration
```

命令校验固定 annotation archive 的 SHA-256，只在本地下载并重建 source PDF，同时记录
每个 URL 与 PDF hash。Scriptorium 不重新分发这些 PDF；论文继续遵循各自 arXiv 记录中的
许可。样本选择只读取 `bbox`、`category_id` 和 `textline_polys`，不读取
`reading_order_id`、`reading_order_label` 或 `ro_linkings`。文档通过 SHA-256 分到互斥的
fit/calibration partition，同一论文的页面不会跨 partition。

固定语料包含 `1710.06349`、`1609.04214`、`1709.05631` 的 6 个 fit 页，以及
`1702.07651` 的 2 个 calibration 页；两个 partition 都同时包含普通多栏页和
graphical-multicolumn 页。Provider 只接收渲染图像；layout anchor 只用于匹配，semantic
sidecar 只由评分器读取。

每个 manifest 图片分别运行一次 provider，并用 sample id 作为输出文件名；快速 layout-only
路径如下：

```bash
scriptorium run-paddle-layout \
  data/external/comphrdoc-provider-calibration/images/1710.06349_4.png \
  --input-kind image --device cpu \
  --output outputs/pp-doclayoutv3/1710.06349_4.structure.json

scriptorium benchmark-provider-anchor-suite \
  data/external/comphrdoc-provider-calibration \
  outputs/pp-doclayoutv3
```

下表是在 provider anchor 和序列化 order edge 映射到答案隔离 oracle 后得到的 micro relation
F1。PaddleOCR-VL 与 PP-Structure 各运行了每个 partition 的一个 graphical 页；它们的两页
结果不能直接视为与两个八页 provider 的完整语料对比。

| Provider | 页数 | Fit F1 | Calibration F1 | Overall F1 | 能力 |
|---|---:|---:|---:|---:|---|
| PaddleOCR-VL 1.6 | 2 | 0.94193548 | 0.83969466 | 0.89510490 | OCR + layout/order |
| PP-StructureV3 lightweight | 2 | 0.96774194 | 0.79069767 | 0.88732394 | OCR + layout/order |
| PP-DocLayoutV3 | 8 | 0.89882353 | 0.87248322 | 0.89198606 | layout/order，不识别文本 |
| Docling 2.111.0 | 8 | 0.88119954 | 0.84415584 | 0.87148936 | OCR + layout/order |

在相同 8 页上，PP-DocLayoutV3 相对 Docling 全部非负，其中 7 页为正、1 页持平；平均
page F1 delta 为 `+0.01846737`，micro F1 delta 为 `+0.02049670`，paired page bootstrap
95% 区间为 `[+0.00832645, +0.02927668]`，四个文档的平均 delta 也都为正。
Graphical-multicolumn 仍是较难 stratum：PP-DocLayoutV3 与 Docling 的 micro F1 分别为
`0.82627119` 和 `0.80081301`，普通 multicolumn 则为 `0.93786982` 和 `0.92240117`。

CPU 耗时是操作性记录，不是严格速度 benchmark：PP-DocLayoutV3 的受控单页探针约
`8.48 s/page`、约 `1.28 GB`；Docling 约 `23 s/page`；轻量 PP-Structure 约
`148-316 s/page`；PaddleOCR-VL 约 `682-2193 s/page`，包含模型和冷启动差异。关闭对已渲染
直立 PDF 页冗余的整页方向、去畸变和文本行方向预处理后，PP-Structure fit 页从 `334 s`
降到 `148 s`，relation F1 从 `0.9333` 变为 `0.9677`。旋转页或拍照页仍可通过显式参数
重新开启这些阶段。

PP-DocLayoutV3 声明 `text_recognition: false`，因此空文本字段会标记为不适用，字符、token
和 caption 指标不会进入 synthetic-profile 距离；其余 9 个 layout 特征仍参与比较。所有输出
继续保留 `review-only`、`runtime_reorder: false`、包版本、模型参数、输入 hash 和 capability
provenance。8 个 train-only 页不足以覆盖真实域，因此没有据此晋升 runtime order，也没有
调整任何运行时 threshold。

### 32 页粒度审计与独立 Test Gate

8 页结果把同一个 Provider paragraph 内的多条 oracle line 与真正的跨 block order 混在
一个 serialized F1 中。32 页扩展语料固定为 16 篇 train 文档、24 个 fit 页和 8 个
calibration 页；每个 partition 都包含 `multicolumn` 与
`graphical-multicolumn`。arXiv 使用 `v1`，避免 annotation 与后续修订页数错位。
本地 annotation archive 可以直接复用，但仍校验固定 SHA-256：

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

Suite schema v7 将 Provider 顺序拆为三种粒度。`within-anchor` 是同一模型 block 内按
oracle 几何排列的 line edge；`direct inter-anchor` 才是两个相邻模型 block 的直接
transition。旧 relation summary 把所有预测都当作可评分，因此只能作为 raw exact-match：

| 指标 | Fit 24 页 | Calibration 8 页 | Overall 32 页 |
|---|---:|---:|---:|
| Serialized aggregate F1 | 0.90329611 | 0.84264832 | 0.88870500 |
| Within-anchor precision | 1389/1396 = 0.99498567 | 440/443 = 0.99322799 | 1829/1839 = 0.99456226 |
| Raw direct inter-anchor precision | 237/306 = 0.77450980 | 50/102 = 0.49019608 | 287/408 = 0.70343137 |
| Partial-label-aware direct precision | 237/278 = 0.85251799 | 50/84 = 0.59523810 | 287/362 = 0.79281768 |
| Unscored direct transitions | 28 | 18 | 46 |

每条 direct transition 现在记录两个端点的最小 detection confidence，以及具体哪些
answer-free native candidate 给出同一直接 successor。Suite v8 暴露四个可观测通道：
`visual-yx`、`box-flow`、非平凡 `recursive-xy-cut` tree edge，以及
`relation-graph` max-regret path-cover 真正选中的 edge。候选 provenance 与允许计入 gate
支持票的子集分开保存。Eligibility 先根据这些字段确定，之后评分器才读取 semantic
sidecar；改变 relation label 不会改变 eligible edge。Comp-HRDoc `ro_linkings` 是 partial
label，所以 v3 review 只有在 source/target 都属于 relation endpoint universe 时才计入
precision，其他候选单列为 `unscored`。Gate 还要求最小 `scorable_fraction`，曲线同时报告
95% Wilson 下界。

同一 32 页 fit/calibration 上，legacy v1 gate 的 endpoint-aware 冻结点为
`native support >= 1 && confidence >= 0.5`：fit `230/237 = 0.97046414`，Wilson
`0.94029969`，另有 26 条 unscored；calibration `46/48 = 0.95833333`，Wilson
`0.86024344`，但 scorable fraction 只有 `0.76190476`。它只用于重算已经打开的历史 test
window，不能授权 runtime：

```bash
scriptorium freeze-provider-transition-gate \
  outputs/pp-doclayoutv3-calibration-32/suite.json \
  --partition fit --minimum-precision 0.95 \
  --minimum-wilson-lower-95 0.90 --minimum-predicted 50 \
  --output outputs/pp-doclayoutv3-transition-gate.json
```

独立验证使用官方 Comp-HRDoc test annotation 的另一组固定 document hash：16 篇文档、
32 页，其中 17 页 graphical-multicolumn、15 页 multicolumn。选择仍不读取 relation label。
使用 latest arXiv source 时 annotation/PDF token alignment F1 为最低 `0.69785276`、平均
`0.90739489`；Provider 仍只读取渲染图。test 过程只加载冻结 gate，不重新搜索阈值：

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

未筛选 test raw direct transition 为 `269/384 = 0.70052083`。旧报告的
`209/219 = 0.95433790` 把未标注边当作错误，已撤回为当前指标。相同候选与相同已打开
test window 的 endpoint-aware 结果如下：

| 独立 test 分层 | Eligible | Scorable / unscored | Correct / scorable | Precision | Wilson lower 95% |
|---|---:|---:|---:|---:|---:|
| Aggregate | 284 | 268 / 16 | 256/268 | 0.95522388 | 0.92337855 |
| Graphical-multicolumn | 91 | 84 / 7 | 81/84 | 0.96428571 | 0.90018306 |
| Multicolumn | 193 | 184 / 9 | 175/184 | 0.95108696 | 0.90966772 |

FocalOrder 指出的 positional disparity 也纳入 veto-only 事后审计。test 中页首、页中、页末
分别为 `72/78`、`91/92`、`93/98`；Wilson 下界为 `0.84216770`、`0.94097214`、
`0.88607548`。页首与页末仍失败，因此该 window 继续只能否决 promotion。

随后扩大到 64 个 train-only 页面、32 篇文档（25 fit / 7 calibration document）。Gate v4
把“全部可观测候选”与“允许计入支持票的候选”分开：默认只使用此前已校准的
`visual-yx`、`box-flow` 和 selected `relation-graph`，`recursive-xy-cut` 保留为诊断但不算
独立一票。所有 rule 仍至少要求两票，再按 document 做 5-fold OOF。

```bash
scriptorium freeze-stratified-provider-transition-gate \
  outputs/pp-doclayoutv3-calibration-64/suite-v8-structural-edges.json \
  --minimum-native-support 2 --cross-validation-folds 5 \
  --output outputs/pp-doclayoutv3-transition-gate-v4.json
```

Fit rule 为 `234/237 = 0.98734177`。OOF aggregate 为 `192/195 = 0.98461538`，Wilson
`0.95575171`，但 fold 2/3 各为 `30/31`、Wilson `0.83805895`；
`graphical-multicolumn/middle` 只有 18 条，`multicolumn/start` 只有 `8/9`，Wilson
`0.56500029`、scorable fraction `0.75`。Calibration 为 `21/21`，但 Wilson
`0.84536098 < 0.85` 且 `21 < 30`。`support = 3` 又没有 fit bucket 达到 20 条。
Gate v4 精确复现上述三通道结果。显式启用全部四通道后，full-fit 仍为 `234/237`，但 OOF
变为 `173/176 = 0.98295455`；calibration 变为 `23/24 = 0.95833333`，Wilson
`0.79758194`。新增的 calibration 错误只由 `visual-yx + recursive-xy-cut` 支持，说明
XY-cut 是相关的几何证据，而不是已经独立校准的一票。严格的 `visual-yx + box-flow` 两通道
对照在 `support = 2` 下没有任何 fit bucket 达标。因此 v4 仍为
`document-cross-validation-rejected-review-only`，第四个 test window 未打开。旧 v2/v3 gate
继续保留历史 support 计数；v4 在需要过滤却缺少逐 candidate provenance 时会 fail closed。

### Chunkr 跨领域阅读顺序开发基准

Chunkr Reading Order Bench OSS 是宽松许可的跨领域 COCO 语料，包含财务、法律、政府、
研究、杂志等页面。Scriptorium 固定 revision
`d6b5ddf06a6479a42bb0b33c243801171e042fc7` 和 annotation SHA-256
`93974a16cb43a44656f293b933abd1a713d2bff2bfa71cd7b74987edb26bdbfa`。该固定文件包含
733 页、9,267 个 layout element；本次运行只认原始文件计数，不复制会继续变化的 dataset
card 汇总。

标准 COCO record 没有独立 `reading_order` 字段；发布顺序编码为每页连续递增的 annotation
id。Loader 会拒绝缺失、重复、不连续、乱序、未知 category 和超出页面的记录。Candidate
推理前，anchor 按 category+bbox 的 SHA-256 fingerprint 重排，annotation id 完全不参与，
避免答案序列成为 stable-sort tie-break。该语料只用于 development benchmark，不能授权
runtime reorder。

```bash
scriptorium fetch-chunkr-reading-order \
  --out-dir data/external/chunkr-reading-order

scriptorium benchmark-chunkr-reading-order \
  data/external/chunkr-reading-order/_annotations.coco.json \
  --output outputs/chunkr-reading-order/report.json
```

| 顺序候选 | Exact 页面 | Pairwise accuracy | Successor accuracy | 复杂页 exact / pairwise |
|---|---:|---:|---:|---:|
| Selected `auto` | 449/733 = 0.61255116 | 0.87452713 | 0.75041012 | 136/331 = 0.41087613 / 0.86935761 |
| Visual Y/X | 484/733 = 0.66030014 | 0.84918215 | 0.72099836 | 166/331 = 0.50151057 / 0.84227794 |
| Box-flow | 163/733 = 0.22237381 | 0.85557652 | 0.53808296 | 28/331 = 0.08459215 / 0.85872998 |
| Recursive XY-cut | 484/733 = 0.66030014 | 0.85826842 | 0.72931802 | 167/331 = 0.50453172 / 0.85193170 |
| Relation graph | 342/733 = 0.46657572 | 0.81817613 | 0.70635107 | 94/331 = 0.28398792 / 0.81279666 |

Selected 算法相对 visual Y/X 提高了长程 pairwise 和局部 successor 质量，但 whole-page exact
match 与 position accuracy 更低。下一步优化目标因此很具体：保留复杂页的 pairwise 收益，
同时不要改坏原本已正确的简单页。

| 直接 edge 证据 | Precision | Recall | F1 |
|---|---:|---:|---:|
| Visual Y/X | 0.72099836 | 0.72099836 | 0.72099836 |
| Box-flow | 0.53808296 | 0.53808296 | 0.53808296 |
| 非平凡 recursive XY-cut edge | 0.78614579 | 0.45746426 | 0.57837037 |
| Selected relation-graph edge | 0.80938502 | 0.65080853 | 0.72148610 |
| 稳定三通道 support >= 2 | 0.91591928 | 0.67014296 | 0.77398836 |
| 全四通道 support >= 2 | 0.82393096 | 0.74958988 | 0.78500430 |

这个独立领域组合再次确认 Provider 结论：加入 XY-cut 会提高覆盖并轻微提高 F1，但让
support-2 precision 下降超过 9 个百分点，因此继续保持 audit-only。第一次运行还发现并修复
了一个通用所有权 bug：同时被识别为 grid island 与 sidebar 的元素曾被 emit 两次；修复后
733 页的 selected order 都是完整 permutation。

隔离的 learned candidate 使用 68 个 role、归一化 geometry 和 candidate-rank 特征，通过
双向 pairwise classifier 与 Borda decoder 生成顺序。五折按 category/complexity 分层，并用
SHA-256 分配完整页面。上游没有发布 document id，所以这里只是 page-level OOF development
证据，不是 document-level cross-validation，也不声称存在 held-out test。

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

Learned ranker 在域内提高 whole-page exact、position 和长程 pairwise，但相对 selected
`auto` 少了 59 条正确 adjacent successor。对 selected，它增加 87 个 exact 页面、损失 21
个；successor 在 142 页更好、105 页更差。因此这只是 global-ranking 研究候选，不能替换
局部 reading stream。

在答案隔离的 ROOR validation 49 页上原样 replay 后，结果完全反转。Benchmark 会先完成
所有页面预测，再打开任何 semantic sidecar；structure 与 label 文件被限制在 corpus 路径内，
并分别记录 hash。

| ROOR 49 页 candidate | Direct relation recall | Precedence accuracy |
|---|---:|---:|
| Learned Chunkr ranker | 500/2612 = 0.19142420 | 2013/2612 = 0.77067381 |
| Selected `auto` | 1217/2612 = 0.46592649 | 2173/2612 = 0.83192956 |
| Visual Y/X | 1390/2612 = 0.53215926 | 2317/2612 = 0.88705972 |

Learned candidate 的 direct relation 在 47/49 页低于 selected，precedence 在 35/49 页更差。
Chunkr 是 coarse mixed-role block，ROOR 则是 fine-grained all-text line。基于 element count、
bbox size quantile、role mix 和 candidate disagreement 的 page-profile envelope 将 ROOR 49 页
全部标为 Chunkr 训练域外。但这个 `49/49` 拒绝器是在观察到上述粒度失败后加入的，所以只能
诊断这个已打开窗口，不能当作独立验证过的 OOD calibration。模型继续保持
`runtime_reorder: false`、`candidate_consensus_policy: isolated`，并明确拒绝 promotion。
下一次实验应先冻结 hierarchical coarse-block-then-line 契约，再去未打开的文档家族验证，
不能继续对 ROOR 调整 flat ranker。

### 分层 Proposal Coverage 审计

冻结的 hierarchy 契约现已实现为隔离 proposal 路径。它可以直接读取一页 `DocumentIR` 和
provider structure JSON，不会把 provider sequence 或 relation answer 复制进 ordering
candidate：

```bash
scriptorium build-hierarchical-order \
  outputs/research/attention-pp-structure-block-transitions-v3/native-only/cases/attention-is-all-you-need/document.ir.json \
  --structure-json outputs/research/pp-structure-attention-page-1/page_0001_res.json \
  --page-index 0 \
  --output /tmp/attention-hierarchy.proposal.json
```

PP parent block 与 OCR line 会按不同粒度类归一化。Adapter 只保留真正的 coarse provider
block，再比较原 geometry-only membership 与“精确/包含文本 + 局部空间”证据：

| 页面/provider | Fine elements | Normalized / selected regions | Assigned | Unassigned | Non-empty regions | Eligible cross transitions |
|---|---:|---:|---:|---:|---:|---:|
| Attention 第 1 页 / PP-Structure | 56 | 61 / 9 | 47 -> 52 | 9 -> 4 | 6 -> 9 | 1 -> 6 |
| 比亚迪年报第 136 页 / PP-Structure | 34 | 61 / 17 | 29 -> 33 | 5 -> 1 | 11 -> 15 | 7 -> 13 |
| JD image source / Docling | 64 | 93 / 93 | 49 -> 53 | 15 -> 11 | 31 -> 37 | 16 -> 20 |

这是 **coverage audit**，不是有标签的 reading-order benchmark。它证明 adapter 无需降低全局
geometry threshold，就能在论文、中文财报和 image-source 门户三类页面中恢复更多合理的
region membership；它不能证明新增的 within-region 或 cross-region edge 正确。所有 edge 仍为
review-only，不完整 region chain 会抑制 candidate expansion。任何 promotion 前都必须用独立
标签分别评分 within-region successor 与 cross-region transition。

### 答案隔离的分层 Relation Benchmark

有标签的 hierarchy benchmark 现从官方 Comp-HRDoc **train** split 物化 32 篇文档、
64 页：50 个 fit 页、14 个 calibration 页、32 个 graphical-multicolumn 页、30 个
multicolumn 页和 2 个 graphical 页。Document-hash partition 保证同一论文不会跨越 fit 与
calibration。`block_id` 只在物化阶段构造 oracle coarse-region geometry 和 membership label；
member id、`ro_linkings`、provider sequence 与 relation value 都不会进入 inference input。
Evaluator 会先预测所有页面，再解析或打开任何 label path，同时验证 input/label SHA-256；
proposal 始终保持 review-only：

```bash
scriptorium materialize-comphrdoc-hierarchy \
  /tmp/scriptorium-comphrdoc-provider-calibration-64-v1 \
  --output /tmp/scriptorium-hierarchy-train64-v1
scriptorium benchmark-hierarchical-order-corpus \
  /tmp/scriptorium-hierarchy-train64-v1 \
  --output /tmp/scriptorium-hierarchy-train64-boundary-text-report-v1.json
```

旧 control 会把 coarse region 强制串成一条 adjacency chain。新默认路径把 fine relation graph
真正选中的 edge 保留成 partial DAG：所有跨区域 edge 都进入 evidence；只有从 source local
stream 尾部连到 target local stream 头部的 edge 才能成为 transition；region 级 predecessor /
successor 必须保持 degree-one；会闭合 region cycle 的最低 regret edge 会被抑制。基于 member
completion 的 region sequence 只作诊断，`total_order_asserted` 与 `runtime_reorder` 都为 false。

另有一次非迭代 membership refinement：只有无同分替代的 relation graph 前后邻居，以及
selected-order 前后邻居，都已经属于同一个 geometry-tied candidate region 时，才解除完整重叠
tie。它修复 5 个 fit 和 3 个 calibration membership，新分配结果不会继续传播。

第二条非迭代规则处理互补的 boundary split。无 tie 的 relation 邻居与 selected-order 邻居必须
同时形成相同的 `A -> element -> B` region 模式；element 归一化后至少包含 4 个字母或数字，且
原 geometry tie 内必须只有一个 region 包含该文本，这个唯一文本 parent 还必须是 `A` 或 `B`。
它解除 6 个 fit 与 7 个 calibration membership，全部正确；这些结果和上一轮 continuity repair
都不会继续传播。

| 指标 | 旧 coarse chain | Relation DAG + continuity | 当前 boundary-text | Flat selected baseline |
|---|---:|---:|---:|---:|
| Membership recall / coverage | 0.99353243 | 0.99505421 | 0.99752711 | n/a |
| Within-region successor F1 | 0.98901099 | 0.99188544 | 0.99297033 | 0.98359865 |
| Line cross-region F1 | 0.76518219 | 0.92624585 | 0.93473962 | 0.92146597 |
| Region transition F1 | 0.72936660 | 0.89761751 | 0.90607029 | 0.86111111 |

当前 fit/calibration 的 line-transition F1 为 `0.93802345/0.92260062`，region-transition F1
为 `0.90835361/0.89759036`。Calibration region F1 已超过 flat control `0.88563050`；但
calibration line F1 仍比 flat `0.92879257` 低 `0.00619195`，所以 runtime promotion 继续关闭。
Graphical-multicolumn 的 line/region F1 达到 `0.90609555/0.85007728`：line 与 flat control
持平，region 高于其 `0.81049563`。普通 multicolumn 达到 `0.95828636/0.95291480`。
Fit/calibration 的 within-region F1 达到 `0.99191794/0.99642675`；membership 保持 0 错分配，
unassigned 从 34 降到 13。

报告汇总 8 次内部 continuity repair、13 次 boundary-text membership repair、5,150 条 fine
selected edge、972 条 cross-region evidence、905 条 boundary-aligned candidate、67 条
non-boundary evidence、9 条同分跨区 edge 和 3 次 region-cycle suppression，最终输出 902 条
无环 review transition。Cross-region evidence 数量变化来自 membership 先被解除，再对 selected
fine edge 分类，属于预期结果。冻结的 Chunkr ranker 仍是显式 A/B control；它在此前相同语料上
经过 OOD suppression 后的 region-transition F1 只有 `0.15531915`。

另有两个更激进的实验被否决。按所有 non-boundary relation edge 切分 region stream 会把 fit
line F1 从 `0.93265993` 提高到 `0.94176373`，但会删除 25 条正确 within-region edge，使
within F1 从 `0.99097698` 降到 `0.98873592`，并产生 1 个环。把 non-boundary relation 与 flat
adjacency 合并会使 endpoint-aware partial line F1 达到 `0.93891213`，但 29 条新增边中有 22 条
无法评分，region F1 反而从 `0.90301548` 降到 `0.89402390`。两者都没有进入实现。

冻结后的当前 prediction 还漏掉 55 条 fit truth edge：30 条在 relation/base 中都不存在，12 条是
exact non-boundary relation，9 条涉及未分配 member，4 条只有 base。Calibration 剩余 15 条：
8 条两边都不存在、5 条只有 base、2 条涉及未分配 member；没有 calibration truth 可以通过放宽
non-boundary gate 恢复。只读取 fit label 的审计还检查了全部 74 条“base boundary 但 selected
relation evidence 不存在”的候选，其中只有 3 条 exact truth。即使 visual Y/X、box-flow 与
recursive XY-Cut 三路同时支持，4 个候选仍是 0 命中。因此继续叠加 geometry vote 被否决；下一类
有效证据必须来自显式 provider relation/stream，或经过独立训练的 semantic successor scorer。

算法冻结后，又在旧 coverage audit 使用的同一批真实 provider input 上直接重放，没有继续调参：

| 页面/provider | Assigned | 旧 chain transition | Cross evidence / boundary / emitted | Non-boundary / tied |
|---|---:|---:|---:|---:|
| Attention 第 1 页 / PP-Structure | 52 | 6 | 9 / 3 / 3 | 6 / 0 |
| 比亚迪年报第 136 页 / PP-Structure | 33 | 13 | 14 / 9 / 9 | 5 / 0 |
| JD image source / Docling | 108 | 20 | 27 / 11 / 11 | 16 / 1 |
| PUMA 年报第 5 页 / PP-Structure | 23 | 7 | 6 / 6 / 6 | 0 / 0 |

这四行是结构诊断，不是准确率：JD、PUMA 和比亚迪此次重放没有完整 human relation label。
它们说明新路径会拒绝没有依据的 page-wide adjacency，同时把未对齐边界的 evidence 留给 review。
Continuity repair 在四次重放中触发数都为 0；新 boundary-text 规则在当前四个 provider replay
中也全部触发 0 次，因此没有扩张这些无标签页面。本轮没有打开新的 Comp-HRDoc test window，
promotion 继续保持
`development-benchmark-only-review-only`。

该表示与 EMNLP 2024 ordering-relations 和官方 ROOR 实现一致：复杂布局应表达为即时 successor
relation，而不是一个 permutation。Docling 当前 rule-based predictor 同样在页面元素上构造方向
映射；其关于大量 small orphan cluster 的公开讨论进一步说明必须显式处理 granularity 与 abstention。
XY-Cut++ 也独立支持 multi-granularity segmentation 与轻量 semantic/geometry matching。GraphDoc
支持把 order、hierarchy 和 reference relation 放进同一张图，但其 MIT 仓库仍把 dataset、model
和 code release 标为 TODO，因此目前只能作为研究方向，不能作为可接入依赖。

- Ordering relations 论文：https://aclanthology.org/2024.emnlp-main.540/
- 官方 ROOR 实现：https://github.com/chongzhangFDU/ROOR
- Docling reading-order 实现：https://github.com/docling-project/docling-ibm-models/blob/73cf24d321f74f77de5f974e6c048da0e1512a3d/docling_ibm_models/reading_order/reading_order_rb.py
- Relation graph / max-regret path-cover 分析：https://arxiv.org/html/2607.01018
- XY-Cut++ hierarchical/cross-modal ordering：https://arxiv.org/abs/2504.10258
- GraphDoc relation-graph 项目：https://github.com/yufanchen96/GraphDoc

### 可缓存的 Semantic Successor 筛选

2026 年 7 月的 max-regret 研究使用 `EleutherAI/pythia-410M` 的逐 target token 条件似然，
再加 BERT NSP 的 `log p(IsNext)`，固定权重为 `1.0/0.2`。论文同时报告 sentence embedding
没有增益，稠密语义评分在 A40 上平均需要 `93.5 s/page`。因此 Scriptorium 先在已有的答案
隔离 ROOR train partition 上筛选 Apache-2.0、4.4M 参数的 Google BERT-Tiny NSP；revision
固定为 `30b0a37ccaaa32f332884b96992754e246e48c5f`。本轮缓存了 402,395 个唯一有向文本
pair，重复训练不再运行 transformer。

在 27 个内部 calibration 文档上，纯 NSP 很弱但不是随机信号：正 pair 平均概率
`0.73074756`，负 pair `0.68807782`，source top-1 accuracy `0.03766334`，MRR
`0.11700226`。但直接把该分数拼入现有 pair classifier 会退化：

| ROOR train 内部 calibration | Precision | Recall | F1 | Correct / predicted |
|---|---:|---:|---:|---:|
| Geometry/text-shape v2 top edge | 0.66132556 | 0.64857143 | 0.65488640 | 908 / 1,373 |
| Geometry/text-shape v2 + branch | 0.65991620 | 0.67500000 | 0.66737288 | 945 / 1,432 |
| 直接添加第 26 个 Tiny NSP 特征，top edge | 0.65175953 | 0.63500000 | 0.64327062 | 889 / 1,364 |
| 直接添加第 26 个 Tiny NSP 特征 + branch | 0.65618299 | 0.65571429 | 0.65594855 | 918 / 1,399 |

直接融合已被否决。随后只读取 fit label 筛选、冻结并实现了二阶段设计：只给 geometry ranker
的 top-5 target 计算 NSP，再组合 base probability、rank/margin、NSP 相对分数与原 pair
feature，阈值来自 5 个 document-hash OOF fold。Fit candidate recall 为 `0.94110838`，
冻结阈值 `0.59` 下的 OOF F1 为 `0.74600465`。在 semantic ranking 上重新训练已有 branch
gate 后，development calibration 继续提升：

| ROOR train 内部 calibration | Precision | Recall | F1 | Correct / predicted |
|---|---:|---:|---:|---:|
| 冻结 v4 semantic top edge | 0.70720372 | 0.65214286 | 0.67855816 | 913 / 1,291 |
| 冻结 v4 semantic + branch | 0.70895522 | 0.67857143 | 0.69343066 | 950 / 1,340 |

随后使用更严格的两阶段 evaluator 重放 250 页 Comp-HRDoc 跨域 corpus：全部 500 次 mode
prediction 完成后，才打开任何 semantic sidecar。v4 在全部 body/path-cover 指标上提升：

| Comp-HRDoc 250 页 | v2 F1 | v4 semantic F1 | Delta |
|---|---:|---:|---:|
| Native ranker edge | 0.84129386 | 0.87467713 | +0.03338327 |
| Native ranker path cover | 0.86904643 | 0.90414107 | +0.03509464 |
| Plus structure-role edge | 0.85716953 | 0.89082846 | +0.03365893 |
| Plus structure-role path cover | 0.88501708 | 0.92115119 | +0.03613411 |

报告同时声明 inference input 不含答案，并写入
`labels_opened_after_all_predictions: true`。第二个严格两阶段 A/B 使用了 train split 外的
官方 ROOR validation 全部 49 页：

| ROOR validation 49 页 | v2 F1 | v4 semantic F1 | Delta |
|---|---:|---:|---:|
| Top edge | 0.67510713 | 0.71032949 | +0.03522236 |
| Branch edge | 0.69167292 | 0.73061145 | +0.03893853 |
| Degree-one path cover | 0.68729852 | 0.71334792 | +0.02604940 |

### Hierarchy Semantic 冲突仲裁

直接追加 semantic boundary edge 仍然太宽。64 页 hierarchy corpus 中有 181 条 novel path
edge，其中 62 条 boundary-aligned，最初有 9 条会填补空 region slot；这些新增边只增加 1 条
正确 region relation，没有增加 exact line edge，反而降低 calibration line F1。最终算法只用
fit 选择：semantic edge 必须与恰好一条已选 native region edge 冲突，置信度至少高 `0.10`，
且替换后仍保持 region DAG；只允许一换一，不增加 transition 总数，也不改变 membership/
within-region stream。

| Comp-HRDoc hierarchy | Native line / region F1 | Semantic arbitration | 替换数 |
|---|---:|---:|---:|
| 50 页 fit | 0.93802345 / 0.90835361 | 0.93969849 / 0.90997567 | 2 |
| 14 页 calibration | 0.92260062 / 0.89759036 | 0.93209877 / 0.90690691 | 2 |
| 32 页官方 test window | 0.94021102 / 0.91762014 | 0.94255569 / 0.91990847 | 2 |

在 calibration 上，semantic line F1 已超过 flat control `0.92879257`，region F1 也继续高于
`0.88563050`。独立 test window 同时确认 line/region 正增益，而且 prediction 总数、membership
与 within-region F1 不变；这一阶段的 line F1 仍比 test-window flat control `0.94712644` 低
`0.00457075`，因此下一处问题被隔离为 hierarchy endpoint 结构，而不是 Tiny 模型容量。

### 图表对象的 Branch Endpoint

只读取 fit label 的残余错误审计发现，图表对象被错误地串成了 through-path node。在可评分的 fit
transition 中，从 `table` region 出发的已选 edge 全部错误（`7/7`），进入 `figure` region 的
已选 edge 也全部错误（`5/5`），正确数均为 0。Corpus relation 始终用相反方向表达有用的局部
branch：figure object 指向 caption，caption 或 body text 指向 table object。这也符合翻译架构：
对象及 caption 应构成有界单元，不能成为两个无关 body stream 之间的桥。

因此 policy v4 把 `table` region 保持为 terminal branch endpoint，把 `figure` region 保持为
root branch endpoint。Table-source 或 figure-target candidate 仍会留在
`cross_region_relation_evidence` 并记录明确 suppression reason，但不能占用 region 的
predecessor/successor slot。规则只在 fit 上冻结，随后不改动地重放 calibration 与此前隔离的
官方 test window：

| Comp-HRDoc hierarchy | Semantic line / region F1 | Object-branch v4 | Flat line / region control |
|---|---:|---:|---:|
| 50 页 fit | 0.93969849 / 0.90997567 | 0.94843618 / 0.92727273 | 0.91950207 / 0.85475285 |
| 14 页 calibration | 0.93209877 / 0.90690691 | 0.94968553 / 0.92638037 | 0.92879257 / 0.88563050 |
| 32 页官方 test window | 0.94255569 / 0.91990847 | 0.94811321 / 0.93055556 | 0.94712644 / 0.90064795 |

新的 line gate 比独立 flat control 高 `0.00098677`，region gate 高 `0.02990761`。Fit、
calibration、test 的正确 transition 数分别保持 `561/151/402`，membership 与 within-region
指标逐项不变。三个 partition 分别抑制 28、10、24 个 object-branch candidate，同时把每条被拒
edge 留作 review。此前记录的 hierarchy accuracy gate 已经通过，且不需要更大的语言模型。

`runtime_reorder` 继续保持 false，因为这是 oracle-region relation benchmark，不是 OCR-derived
region 的端到端证明，而且输出仍是 partial DAG，不是页面 permutation。下一项 promotion evidence
必须证明同一 branch contract 能在更广泛的独立文档家族上适用于 provider-derived hierarchy。
BERT-Base 与 Pythia 继续延后。

### Provider-Derived Hierarchy 与未分配元素 Fallback

端到端 hierarchy benchmark 现在会用 PP-DocLayout provider block 替换 oracle coarse region，
同时保留 answer-free fine line。Materializer 会移除 provider order/relation 字段，在打开任何
hierarchy label 前先写完全部 inference input，并要求 provider-run corpus manifest hash 与上游
hierarchy corpus 一致。Benchmark 可通过 `--partition` 单独选择 fit、calibration 或 test，避免
不同 partition 在同一评测 pass 中混用：

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

Provider detector 的 box 经常带 padding 或拆分文本，因此 membership coverage threshold 不能
直接沿用 oracle region。该阈值只根据 fit 证据冻结为 `0.10`，margin 为 `0.10`。Policy v5
只恢复“至少一个 endpoint 没有 provider membership”的 selected-native adjacency；连续未分配
元素成为显式 `unassigned-fallback` review stream。Boundary edge 仅在 source/target degree slot
空闲且不会产生环时发出。Assigned-to-assigned flat fallback 已被否决：其 fit precision 只有
`0.11515152`，而带 degree guard 的 unassigned adjacency family 为 `0.94339623`。

Provider metric 不依赖相同 segmentation：它合并 local-stream 与 cross-stream successor edge，
统一评分 line-level relation，并单独报告 assignment coverage 和 pairwise co-membership。以下结果
在所有 partition 上使用同一套冻结 detector 与 fallback policy：

| Provider hierarchy | 无 fallback F1 | v5 fallback F1 | Flat F1 | Assignment coverage | Segmentation pair F1 |
|---|---:|---:|---:|---:|---:|
| 50 页 fit | 0.94842599 | 0.97433893 | 0.94768195 | 0.96362287 | 0.67995655 |
| 14 页 calibration | 0.93254330 | 0.96754386 | 0.97694650 | 0.95065789 | 0.65042468 |
| 32 页官方 test window | n/a | 0.97016660 | 0.96606248 | 0.97687225 | 0.80643143 |

独立 test relation precision/recall 为 `0.96979086/0.97054264`。Semantic ranker 在 test 上与
native v5 完全相同；fallback 前也只把 calibration F1 从 `0.93254330` 提高到 `0.93345488`，
因此主要增益来自显式 membership abstention，而不是语言模型调参。在平行的 oracle-region test
中，同一个 v5 规则把 line F1 从 `0.94811321` 提高到 `0.95571096`，正确 line edge 从
`402` 增到 `410`，membership、region relation 与 within-region 指标均不变。

这已经是有意义的 provider 端到端证据，但还不足以 runtime promotion：calibration 仍低于其
flat control，provider pairwise segmentation 仍弱，而且输出只是 review-only partial relation
graph。下一步需要提高 provider region grouping，或用独立于评测 label 的证据在 hierarchy 与
flat relation 间选择。评测设计遵循 PRImA 的 correspondence-aware reading-order 原则：先处理
segmentation mismatch，再评分 relation，而不是要求预测与真值使用相同 region id
（[Clausner 等，ICDAR 2013](https://www.primaresearch.org/www/assets/papers/ICDAR2013_Clausner_ReadingOrder.pdf)）。

### Provider Continuity Segments v6

只读取 fit label 的残余审计找到两类 provider 特有错误。第一，同一个 detector region 的 member
可能在 selected-native order 中被其他 region 打断；v5 仍会把该 region 的全部 member 连成一条
链。正确 fit edge 都具有局部纵向连续性，错误则集中在纵向回跳或大间距。第二，native relation
graph 跨越大量 selected-order position 的 transition precision 偏弱。因此 policy v6：

- 当非相邻 member pair 的前向纵向连续性超出平均行高 `[-0.25, 1.25]` 时，把一个 provider
  region 拆成多个 local stream；
- selected-rank displacement 大于 4 的 native cross-region candidate 只保留为 evidence，外部
  semantic edge 不受该 guard 影响；
- 保留每条 selected-native adjacent edge、全部 membership 决策、base/candidate element order，
  并继续保持 `runtime_reorder: false`。

阈值仅在 fit 上选择，随后冻结重放 calibration/test。该设计与 sparse graph reading-order 的
cluster-and-sort 分解一致（[Wang 等，ICDAR 2023](https://arxiv.org/abs/2305.02577)），同时继续
输出 relation DAG，而不是强制生成一个 permutation。

| Provider hierarchy | v5 F1 | Continuity v6 precision / recall / F1 | Flat F1 | Split / nonlocal suppression |
|---|---:|---:|---:|---:|
| 50 页 fit | 0.97433893 | 0.97905759 / 0.97471983 / 0.97688390 | 0.94768195 | 12 / 28 |
| 14 页 calibration | 0.96754386 | 0.97966401 / 0.96768559 / 0.97363796 | 0.97694650 | 8 / 17 |
| 32 页官方 test window | 0.97016660 | 0.97966367 / 0.97093023 / 0.97527740 | 0.96606248 | 18 / 28 |

独立 test 相对 v5 提高 `+0.00511080`，相对 flat 提高 `+0.00921492`。Fit 逐页为 15 页提升、
32 页不变、3 页下降；calibration 为 `7/6/1`，唯一退化 `-0.00063939`；test 为 `9/22/1`，
唯一退化 `-0.00072107`。Oracle-region 32 页 control 的 line/region F1 逐项保持
`0.95571096/0.93055556`，provider-only suppression 全部为 0。

同一份 v5/v6 代码还在真实复杂页上做了无标签 A/B。以下只能作为诊断，不能当作正确率：

| 真实页面/provider | v5 -> v6 streams | Within edges | Relation transitions | Fallback transitions | Split / nonlocal |
|---|---:|---:|---:|---:|---:|
| Attention 第 1 页 / PP-Structure | 11 -> 11 | 45 -> 45 | 3 -> 3 | 4 -> 4 | 0 / 0 |
| 比亚迪年报第 136 页 / PP-Structure | 16 -> 16 | 18 -> 18 | 8 -> 7 | 1 -> 1 | 0 / 1 |
| JD 首页 / Docling | 45 -> 53 | 89 -> 81 | 11 -> 5 | 10 -> 18 | 8 / 6 |
| PUMA 年报第 5 页 / PP-Structure | 10 -> 10 | 15 -> 15 | 6 -> 6 | 1 -> 1 | 0 / 0 |

JD 展示了预期行为：8 条不连续 provider chain 被拆成独立 translation/review stream，6 条缺少
支持的远跳选择 abstain；新增 fallback transition 只恢复至少一个 endpoint 未分配的 native
adjacency。四个页面的 base/candidate order 在 v5/v6 间完全一致。Calibration 仍比 flat 低
`0.00330854`，因此 provider grouping 与不读取答案的 hierarchy/flat selector 仍是下一步。

### Graph-Supported Native Adjacency v7

Calibration 剩余差距以 recall 为主，但仍不能重新打开通用 assigned-to-assigned flat fallback。
一个 geometry-only rescue control 在 fit 选择 5 条 edge、正确 `4/5`，在 calibration 为 `7/8`，
但独立窗口只有 `1/3`，因此被明确否决。

Relation graph 在 max-regret path cover 前已经计算 sparse top-k candidate graph。v7 会从同一次
推理直接暴露这些 candidate（每个 source 最多 6 个 target），不重复构造二次复杂度 graph。
在不同 text region 之间全部可评分 selected-native adjacency 上，冻结的 `score >= 0.95` bucket
correctness 为：

| Raw relation-supported adjacency | Correct / scorable | Precision |
|---|---:|---:|
| 50 页 fit | 460 / 469 | 0.98081023 |
| 14 页 calibration | 94 / 96 | 0.97916667 |
| 32 页官方 test window | 318 / 321 | 0.99065421 |

Rescue 还必须同时满足 selected-native adjacency、不同 provider text region、v6 纵向连续性、
水平 overlap `>= 0.5`、element 与 region degree slot 空闲，以及 element/region graph 均不成环。
通过的 edge 只成为带 candidate score 和 geometry provenance 的 review transition；它不改变
membership，也不会启用 runtime reorder。

| Provider hierarchy | Continuity v6 F1 | Adjacency v7 precision / recall / F1 | Rescue correctness | Flat F1 |
|---|---:|---:|---:|---:|
| 50 页 fit | 0.97688390 | 0.97907403 / 0.97550169 / 0.97728460 | 3 / 3 | 0.94768195 |
| 14 页 calibration | 0.97363796 | 0.97975352 / 0.97205240 / 0.97588777 | 5 / 5 | 0.97694650 |
| 32 页官方 test window | 0.97527740 | 0.97967162 / 0.97131783 / 0.97547684 | 1 / 1 | 0.96606248 |

Calibration 相对 v6 提高 `+0.00224981`，现在只比 flat 低 `0.00105873`；独立 test 提高
`+0.00019944`，并继续比 flat 高 `0.00941436`。Oracle 32 页 line/region control 逐项保持
`0.95571096/0.93055556`。Attention、比亚迪、JD、PUMA 真实页均未发出新 rescue edge，
base/candidate order 不变。这是无标签复杂页上预期的 abstention，但 emitted sample 仍少且
calibration 仍有差距，因此 `runtime_reorder: false` 保持不变。

该设计遵循 relation prediction 中以全局 degree/cycle constraint 约束局部分数的方法
（[Qiao 等，Pattern Recognition 2024](https://doi.org/10.1016/j.patcog.2024.110314)）。下一步
应直接改善 provider split/merge grouping；继续增加 flat rescue edge 已不是最高价值方向。

### Assigned-Stream Grouping 诊断

Provider-region co-membership 本身不能说明 v6 的 discontinuity split 是否改善了真正提供给编辑器
和翻译流程的 stream。Benchmark 现在还会在 provider 派生 reading stream 上计算 oracle
co-membership pair F1。`region_id: null` 的 stream（包括 `unassigned-fallback`）会被排除，避免
fallback 人为制造同组 pair。

| Partition | Provider-region pair F1 | Assigned-stream pair F1 | 差值 |
|---|---:|---:|---:|
| 50 页 fit | 0.67995655 | 0.67895654 | -0.00100001 |
| 14 页 calibration | 0.65042468 | 0.65148234 | +0.00105766 |
| 32 页官方 test window | 0.80643143 | 0.80042627 | -0.00600516 |

Stream split 没有稳定改善 grouping：calibration 的微小增益没有泛化到 fit 和独立 test。因此该
指标保留为 regression gate，更广泛的 split 继续被否决。后续 grouping 方案必须在 held-out
文档上同时改善该诊断和 relation quality。

### 可审计的 128 页训练语料扩充

此前扩大 train-only 重建时，只要 annotation page 超出固定 arXiv v1 PDF 且文本对齐存在歧义，
流程就会停止。对齐阈值没有因此放宽。Fetcher 现在提供显式的整文档恢复模式：

```bash
scriptorium fetch-comphrdoc-provider-calibration \
  --sample-count 128 --document-count 64 \
  --calibration-fraction 0.2 --arxiv-version v1 \
  --annotation-archive /path/to/CompHRDoc.zip \
  --skip-unaligned-documents \
  --out-dir data/external/comphrdoc-provider-calibration-128
```

默认仍为 fail-closed。恢复模式会先对齐一篇文档的全部选中页，之后才写该文档的派生 sample。
失败时整篇排除，记录 source hash、失败页、最佳 candidate F1、margin 和固定阈值，再从同一
确定性 hash partition 补选下一篇；不会静默丢掉单页。

真实运行得到 64 篇唯一文档、128 个唯一页面：

| 语料属性 | 结果 |
|---|---:|
| Fit / calibration 页 | 102 / 26 |
| Graphical-multicolumn / multicolumn / graphical | 64 / 60 / 4 |
| 整篇拒绝并补选的文档 | 2 |
| Oracle membership coverage | 0.99786574 |
| Oracle within-region successor F1 | 0.99211930 |
| Oracle region-transition F1 | 0.92806959 |

两个被拒文档都属于 fit，其 annotation page index 恰好等于 source PDF page count；最佳
alignment F1 均约为 `0.1482`，明显低于未改变的 `0.6` 门槛，并且都没有进入最终 document
或 sample 列表。

Grouping 架构下一步应从“合并 detector rectangle”转向 sparse line graph，并分别预测 paragraph
membership 与 successor。Post-OCR paragraph recognition 的两阶段 line-splitting / line-clustering
结果优于后续统一局部模型，并明确指出 line width/indentation 是有用的段落上下文
（[Wang 等，WACV 2022](https://arxiv.org/abs/2101.12741)，
[Liu 等，DAS 2022](https://arxiv.org/abs/2203.09638)）。这与上面的 assigned-stream 结果一致：
只拆 region 不是通用 grouping 解法。任何 learned line graph 在 document-held-out segmentation
与 relation gate 同时通过前都保持 benchmark-only。

随后在全部 128 个渲染图上重新运行 PP-DocLayoutV3；每个 provider JSON 都绑定新的 corpus
manifest hash。Provider-derived hierarchy 会分别执行 fit 与 calibration prediction，之后才打开
各自 label：

| Provider hierarchy | Relation F1 | Flat F1 | Provider-region pair F1 | Assigned-stream pair F1 | Assignment coverage |
|---|---:|---:|---:|---:|---:|
| 102 页 fit | 0.97747518 | 0.96136149 | 0.71930643 | 0.71988356 | 0.96719334 |
| 26 页 calibration | 0.97717622 | 0.97578947 | 0.62020524 | 0.62210668 | 0.92364898 |
| 32 页官方 test window | 0.97547684 | 0.96606248 | 0.80643143 | 0.80042627 | 0.97687225 |

扩大后的 calibration relation 比 flat 高 `+0.00138675`，fit 高 `+0.01611369`，未改变的独立
test 仍高 `+0.00941436`。这在更广文档集上补上了此前 relation-level calibration 缺口，但
grouping 仍未解决：assigned stream 在扩大 fit/calibration 上只比 provider region 高
`+0.00057713/+0.00190144`，在独立 test 上仍低 `-0.00600516`。

第一次扩大重放还暴露了性能正确性缺陷：当页面 median line height 较大时，短而重叠的 box
可能在 spatial-graph predecessor 中成环；旧 root traversal 没有 cycle guard，导致两个普通规模
页面无法结束。采样定位循环后，现在会把 cycle 归一到确定性的视觉最小根。原慢页面在
`2.48s` 内完成；当前完整 `411 passed`，既有 50/14/32 页 benchmark 值逐位不变。

扩充后还重新评估了严格 safe-merge ranker。Candidate 是打开 label 前生成的相邻 text-region
pair；feature 只包含 region member 数量/order span、局部 boundary geometry、文本 continuation
标记和 relation-graph score。正例要求两个 provider region 各自纯净，并且完整 member union
属于同一个 oracle region。五折 fit OOF 以整篇文档为分组：

| Strict safe-merge split | Candidates / positives | ROC AUC | Average precision |
|---|---:|---:|---:|
| 102 页 fit，document OOF | 1420 / 282 | 0.86211189 | 0.58226815 |
| 26 页 calibration replay | 235 / 38 | 0.78760353 | 0.49479894 |
| 32 页独立 test replay | 483 / 68 | 0.88807583 | 0.61506653 |

Fit-only 阈值中，没有一个能同时达到 precision `>= 0.98` 且至少选择 20 个 candidate。满足
数量要求的最佳 fit bucket 也只有 `19/25 = 0.76`；即使最少 50 个候选，最高也只有
`77/103 = 0.74757282`。因此该 ranker 被否决，不合并任何 provider region。Edge-level
successor correctness 不能被解释为 cluster-level merge safety。

### 来源无关的细粒度段落图

被否决的 region merge 由 fine element 上的来源无关实验替代。它完全忽略 provider rectangle，
从 selected-order adjacency、relation-graph candidate 和三个局部向前 geometry neighbour 构造
sparse candidate pair。23 个无答案 feature 覆盖归一化 pair geometry、overlap、行尺寸、页内位置、
文本长度与 continuation 信号、selected adjacency 和 relation-graph score。等价 element array
会先按 geometry 与稳定 id 规范化；反转全部 160 页的输入数组后，candidate record 差异为 0。

```bash
scriptorium benchmark-paragraph-graph \
  /path/to/comphrdoc-provider-train-128 \
  --test-corpus /path/to/comphrdoc-provider-test-32 \
  --output outputs/paragraph-graph-report.json \
  --proposals-dir outputs/paragraph-graph-proposals \
  --model-output outputs/models/paragraph-graph.joblib
```

五折 OOF 训练以整篇 fit 文档为分组。只有 fit OOF label 能选择 operating point：至少 100 条 edge、
edge precision `>= 0.97`，再最大化完整 co-membership pair F1。由此得到的阈值
`0.94971959` 会在打开 calibration 或独立 test label 前冻结。流程先加载全部 input，再打开 fit
label；先写完 evaluation prediction/proposal，再打开 evaluation label；corpus path 受目录约束并
校验 hash，sample id 也必须全局唯一。

| Partition | Provider-region pair F1 | Assigned-stream pair F1 | Fine-line graph pair F1 | 相对 assigned 差值 | Selected-edge precision |
|---|---:|---:|---:|---:|---:|
| 102 页 fit，document OOF | 0.71930643 | 0.71988356 | 0.81549627 | +0.09561271 | 0.99202393 |
| 26 页 calibration | 0.62020524 | 0.62210668 | 0.83054081 | +0.20843413 | 0.99642147 |
| 32 页独立 test | 0.80643143 | 0.80042627 | 0.85162046 | +0.05119419 | 0.99548736 |

独立结果还比 provider-region grouping 高 `+0.04518903`。其中 graphical-multicolumn test 页为
`0.86536272`，multicolumn 页为 `0.83935335`。唯一 graphical-only 页只有两个非文本对象、没有
labelled text pair，报告会将其标为不可评分，而不会把零值 metric 解释成失败证据。

每个 proposal 只包含阈值化 candidate edge 和要求复核的局部 paragraph stream，不包含 oracle
membership。它继续保持 `runtime_reorder: false`：当前 head 预测 paragraph co-membership，但还
没有预测 paragraph 间 successor，held-out corpus 也仍属于英文科学论文。下节会评估独立
successor head；联合解码仍需要年报、门户、中文文档和 image source 的跨领域标签。
这与 relation-first reading-order 研究（[Qiao 等 2024](https://doi.org/10.1016/j.patcog.2024.110314)、
[ROOR](https://aclanthology.org/2024.emnlp-main.540/)）和多关系 GraphDoc 方向
（[ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/file/cf3d7d8e79703fe947deffb587a83639-Paper-Conference.pdf)）一致。

### 细粒度有向 Successor Graph

Paragraph membership 本身不能决定两行之间的即时边，也不能决定 paragraph 之间的 handoff。因此
第二个来源无关 benchmark 会训练独立的 directed successor head，且不读取 provider rectangle
或 paragraph label：

```bash
scriptorium benchmark-successor-graph \
  /path/to/comphrdoc-provider-train-128 \
  --test-corpus /path/to/comphrdoc-provider-test-32 \
  --output outputs/successor-graph-report.json \
  --proposals-dir outputs/successor-graph-proposals \
  --model-output outputs/models/successor-graph.joblib
```

Candidate 包括双向 selected adjacency、双向 sparse relation candidate，以及每个 source 固定 20
个最近的有向 geometry target。Fit 共得到 175,748 个 candidate，其中 7,858 个为正 edge，fit-only
candidate recall 上限为 `0.99632306`。39 个无答案 feature 包含有向 geometry/text 信号、base-rank
方向、relation score 和粗粒度 source/target role。五折 document-level OOF 在只看 fit 的
precision `>= 0.97` 且至少选择 1,000 条 edge 的约束下冻结阈值 `0.52131309`。每个 source 只提交
top target，再由按 score 排序的 degree-one/cycle guard 生成无环 path cover。

| Partition | Flat F1 | Provider hierarchy F1 | Directed graph F1 | 相对 provider 差值 | Precision / recall | Candidate recall | Cross-region recall |
|---|---:|---:|---:|---:|---:|---:|---:|
| 102 页 fit，document OOF | 0.96136149 | 0.97747518 | 0.98391591 | +0.00644073 | 0.98279570 / 0.98503867 | 0.99632306 | 0.94290375 |
| 26 页 calibration | 0.97578947 | 0.97717622 | 0.98679345 | +0.00961723 | 0.98523207 / 0.98835979 | 0.99682540 | 0.95205479 |
| 32 页独立 test | 0.96606248 | 0.97547684 | 0.98585545 | +0.01037861 | 0.98566447 / 0.98604651 | 0.99496124 | 0.94796380 |

独立 test 的方向和幅度与 fit/calibration 一致。Test graphical-multicolumn F1 为
`0.97319400`，普通 multicolumn 为 `0.99527027`；唯一 graphical-only 页没有 labelled successor，
通过显式 labelled-page count 排除。Path-cover decoder 在 fit/calibration/test 分别拒绝
`1/1/1` 个 cycle 和 `6/6/9` 个 incoming conflict，并将 test top-candidate F1 从
`0.98529412` 提高到 `0.98585545`，不是只声明一个图约束。

全部 160 页的正序/反序 element-array 对照得到完全相同的 candidate。完整运行会在打开 evaluation
label 前写出每个 source 的前三个 alternative、score margin、选中 review edge 和局部 chain；当前
环境实测耗时 `5:16`、峰值 RSS 约 `1.07 GB`。输出继续保持 `runtime_reorder: false`。两个 graph
head 现在都在 held-out 英文论文家族内泛化。独立的 joint decoder 已可消费它们的 review-only
proposal。两个 head 都可通过 `--model-output` 写出带 SHA-256 校验的 `.joblib` 模型，在
evaluation 阶段按页批打分以释放 dense fit matrix，并通过
`predict-paragraph-graph` / `predict-successor-graph` 对单页 hierarchy input 生成
review-only proposal。真实 PDF/图片页可先用 `export-hierarchy-input` 做 fine-only
DocumentIR 导出，无需 provider structure。年报/门户/中文文档/image-source OCR 的
跨域标签仍是 runtime 替换前的开放 gate。

### 联合 Paragraph/Successor 解码

`benchmark-joint-graph` 不会重训任一 head。它只加载已有的 review-only paragraph 与 successor
proposal，写出联合层次化 proposal，再打开 label：

```bash
scriptorium benchmark-joint-graph   /path/to/comphrdoc-provider-train-128   --paragraph-proposals-dir outputs/paragraph-graph-proposals   --successor-proposals-dir outputs/successor-graph-proposals   --test-corpus /path/to/comphrdoc-provider-test-32   --output outputs/joint-graph-report.json   --proposals-dir outputs/joint-graph-proposals
```

解码契约：

1. Paragraph proposal stream 定义 co-membership 组件。
2. Successor 的 rank-1 candidate 分成 within-paragraph 与 cross-paragraph 两个池。
3. Within-paragraph edge 在 degree-one 无环 path cover 中作为 protected edge。
4. Cross-paragraph edge 只能连接 chain tail → chain head，并按 score 优先接受，仍受同一
   path-cover 约束。
5. Joint proposal 保持 `runtime_reorder: false`，且不写入 oracle membership / oracle scope。

合成多栏 fixture 测试覆盖 answer separation、degree-one 冲突、缺失/污染 proposal 以及
schema 卫生。在冻结 train/test 语料上端到端重跑 joint decoder 之前，这里不声明完整
Comp-HRDoc 分区数字。
