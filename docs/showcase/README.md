# Scriptorium Output Gallery

The static gallery is deployed by `.github/workflows/deploy-showcase.yml` and is intended for GitHub Pages at `https://followcat.github.io/Scriptorium/`.

Each case preserves the actual generated `index.html` and the minimum local assets it needs. Source PDFs that are small and redistributable are bundled; large third-party reports are linked to their public source instead.

| Case | Source | Export mode | Notes |
|---|---|---|---|
| Transformer-XL, p. 1 | ACL 2019 PDF | structured | Multi-column reading order, 99 editable nodes, semantic successor accuracy `1.0` on the tracked labels. |
| BYD annual report, p. 136 | Public CNINFO report | structured | Dense Chinese financial table and local `table-island` structure. |
| JD homepage screenshot | PNG image source | fidelity | First-class image input, 141 OCR anchors, four reading streams, visual similarity `0.99314887`. |

The gallery page only uses relative paths. This is required for a project GitHub Pages URL, where the repository is served below `/Scriptorium/` rather than at the domain root.
