# Scriptorium 转换对照

<p align="center">
  <img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E5%BD%93%E5%89%8D-blue">
  <a href="README.md"><img alt="English" src="https://img.shields.io/badge/English-Read-2f855a"></a>
</p>

可以在[在线交互展示](https://followcat.github.io/Scriptorium/)中直接检查生成
HTML 的真实 DOM。本页是 GitHub 原生 fallback：左侧是纳入版本管理的 source
页面，右侧是本仓库中生成 HTML 经 Chromium 实际渲染后的截图。

独立交互版保留在 [`index.html`](index.html)。每次工作流运行也会上传完整的
`scriptorium-showcase` artifact，并将同一份内容部署到 GitHub Pages。

## 多栏论文

Transformer-XL 第 1 页用于检验标题层次、双栏正文、公式、脚注和局部语义
successor 顺序。

<table>
  <tr>
    <th width="50%">源 PDF 页面</th>
    <th width="50%">生成的 structured HTML</th>
  </tr>
  <tr>
    <td><a href="sources/transformer-xl-acl.pdf"><img src="assets/transformer-xl-page-1.png" alt="Transformer-XL 源 PDF 页面" width="100%"></a></td>
    <td><a href="converted/transformer-xl/index.html"><img src="assets/transformer-xl-generated.png" alt="Transformer-XL 生成 HTML" width="100%"></a></td>
  </tr>
</table>

## 上市公司财报

比亚迪年报第 136 页用于检验中文字体、密集矢量线、表格单元格和翻译局部
table stream。完整来源可从
[巨潮资讯](https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF)获取。

<table>
  <tr>
    <th width="50%">源 PDF 页面</th>
    <th width="50%">生成的 structured HTML</th>
  </tr>
  <tr>
    <td><img src="assets/byd-financial-page-136.png" alt="比亚迪年报源页面" width="100%"></td>
    <td><a href="converted/byd-financial/index.html"><img src="assets/byd-financial-generated.png" alt="比亚迪年报生成 HTML" width="100%"></a></td>
  </tr>
</table>

## 图片输入

JD 首页截图作为一等 image source 进入 Scriptorium。fidelity HTML 保留源视觉
层，同时提供 141 个 OCR 锚点和 4 个局部 reading stream，用于编辑与翻译实验。

<table>
  <tr>
    <th width="50%">源图片</th>
    <th width="50%">生成的 fidelity HTML</th>
  </tr>
  <tr>
    <td><a href="converted/jd-home/assets/page_0001/page_0001.png"><img src="converted/jd-home/assets/page_0001/page_0001.png" alt="JD 首页源图片" width="100%"></a></td>
    <td><a href="converted/jd-home/index.html"><img src="assets/jd-home-generated.png" alt="JD 首页生成 HTML" width="100%"></a></td>
  </tr>
</table>

## 三栏杂志

Hello World Magazine #22 第 5 页是真·三栏目录页，包含图文混排、图注和页脚
artifact，也是当前外部 benchmark 集中 candidate 顺序分歧最大的样本，并直接推动了
带门控的杂志栏目流修复（带标签 successor accuracy `0.22 -> 0.78`、pair accuracy
`0.74 -> 0.96`）。完整期刊可在
[Raspberry Pi 基金会免费下载](https://www.raspberrypi.org/hello-world/issues/22)。

<table>
  <tr>
    <th width="50%">源 PDF 页面</th>
    <th width="50%">生成的 fidelity HTML</th>
  </tr>
  <tr>
    <td><a href="converted/hello-world-magazine/assets/page_0005/page_0005.png"><img src="assets/hello-world-page-5.png" alt="Hello World 杂志第 5 页源图" width="100%"></a></td>
    <td><a href="converted/hello-world-magazine/index.html"><img src="assets/hello-world-generated.png" alt="Hello World 杂志生成 HTML" width="100%"></a></td>
  </tr>
</table>

## 浮动体密集双栏论文

Segment Anything 第 5 页把整宽 Figure 4 图注、跨栏浮动体与双栏正文组合在一起。
被跟踪的 relation-style semantic sidecar 在该页给出 pair / successor / relation
accuracy 全部 `1.0`。来源：[arXiv:2304.02643](https://arxiv.org/abs/2304.02643)。

<table>
  <tr>
    <th width="50%">源 PDF 页面</th>
    <th width="50%">生成的 fidelity HTML</th>
  </tr>
  <tr>
    <td><a href="converted/segment-anything/assets/page_0005/page_0005.png"><img src="assets/segment-anything-page-5.png" alt="Segment Anything 第 5 页源图" width="100%"></a></td>
    <td><a href="converted/segment-anything/index.html"><img src="assets/segment-anything-generated.png" alt="Segment Anything 生成 HTML" width="100%"></a></td>
  </tr>
</table>

## 公式密集双栏论文

Mamba 第 4 页是数学与算法密集的双栏页面，整宽公式与算法框频繁打断段落流，也是
当前论文集中阅读顺序置信度最低的样本。来源：
[arXiv:2312.00752](https://arxiv.org/abs/2312.00752)（CC BY 4.0）。

<table>
  <tr>
    <th width="50%">源 PDF 页面</th>
    <th width="50%">生成的 fidelity HTML</th>
  </tr>
  <tr>
    <td><a href="converted/mamba/assets/page_0004/page_0004.png"><img src="assets/mamba-page-4.png" alt="Mamba 第 4 页源图" width="100%"></a></td>
    <td><a href="converted/mamba/index.html"><img src="assets/mamba-generated.png" alt="Mamba 生成 HTML" width="100%"></a></td>
  </tr>
</table>

上面的 HTML 链接指向纳入版本管理的真实生成文件，因此 GitHub 会显示其源码。
若要使用可编辑 DOM，可打开[在线交互展示](https://followcat.github.io/Scriptorium/)、
下载工作流 artifact，或在本地 checkout 后打开 `docs/showcase/index.html`。
