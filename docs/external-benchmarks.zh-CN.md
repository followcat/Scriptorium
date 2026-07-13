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
