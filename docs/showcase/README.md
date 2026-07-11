# Scriptorium Conversion Gallery

<p align="center">
  <a href="README.zh-CN.md"><img alt="简体中文" src="https://img.shields.io/badge/%E7%AE%80%E4%BD%93%E4%B8%AD%E6%96%87-%E9%98%85%E8%AF%BB-blue"></a>
  <img alt="English" src="https://img.shields.io/badge/English-Current-2f855a">
</p>

This gallery renders directly on GitHub, including for a private repository where
GitHub Pages is unavailable. The left side is a tracked source page; the right
side is a fresh Chromium screenshot of the generated HTML in this repository.

The standalone interactive bundle remains at [`index.html`](index.html). A
workflow run also publishes the complete `scriptorium-showcase` artifact. When
GitHub Pages is available, set the repository Actions variable
`PAGES_ENABLED=true` to deploy the same bundle.

## Multi-column paper

Transformer-XL page 1 tests title hierarchy, two-column body flow, equations,
footnotes, and local semantic successor order.

<table>
  <tr>
    <th width="50%">Source PDF page</th>
    <th width="50%">Generated structured HTML</th>
  </tr>
  <tr>
    <td><a href="sources/transformer-xl-acl.pdf"><img src="assets/transformer-xl-page-1.png" alt="Transformer-XL source PDF page" width="100%"></a></td>
    <td><a href="converted/transformer-xl/index.html"><img src="assets/transformer-xl-generated.png" alt="Transformer-XL generated HTML" width="100%"></a></td>
  </tr>
</table>

## Financial report

BYD annual report page 136 tests Chinese type, dense vector rules, table cells,
and translation-local table streams. The full source report is available from
[CNINFO](https://static.cninfo.com.cn/finalpage/2025-03-25/1222881496.PDF).

<table>
  <tr>
    <th width="50%">Source PDF page</th>
    <th width="50%">Generated structured HTML</th>
  </tr>
  <tr>
    <td><img src="assets/byd-financial-page-136.png" alt="BYD annual report source page" width="100%"></td>
    <td><a href="converted/byd-financial/index.html"><img src="assets/byd-financial-generated.png" alt="BYD annual report generated HTML" width="100%"></a></td>
  </tr>
</table>

## Image source

The JD homepage screenshot enters Scriptorium as a first-class image source.
The fidelity HTML preserves the visual layer while exposing 141 OCR anchors and
four local reading streams for editing and translation experiments.

<table>
  <tr>
    <th width="50%">Source image</th>
    <th width="50%">Generated fidelity HTML</th>
  </tr>
  <tr>
    <td><a href="converted/jd-home/assets/page_0001/page_0001.png"><img src="converted/jd-home/assets/page_0001/page_0001.png" alt="JD homepage source image" width="100%"></a></td>
    <td><a href="converted/jd-home/index.html"><img src="assets/jd-home-generated.png" alt="JD homepage generated HTML" width="100%"></a></td>
  </tr>
</table>

The HTML links above point to the versioned generated files. GitHub displays
their source; download the workflow artifact or open `docs/showcase/index.html`
from a checkout to use the live editable DOM without Pages.
