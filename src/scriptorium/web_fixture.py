from __future__ import annotations

from pathlib import Path


def create_web_fixture(out_dir: str | Path) -> Path:
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    html_path = target / "structured-page.html"
    html_path.write_text(
        """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Scriptorium Structured Fixture</title>
  <style>
    @page { size: A4; margin: 24mm; }
    body {
      margin: 0;
      color: #17202a;
      font-family: Arial, sans-serif;
      font-size: 12pt;
      line-height: 1.35;
    }
    h1 {
      margin: 0 0 14pt;
      font-size: 26pt;
      color: #203864;
    }
    p { margin: 0 0 10pt; }
    table {
      margin-top: 18pt;
      width: 100%;
      border-collapse: collapse;
      font-size: 10.5pt;
    }
    th, td {
      border: 1pt solid #6b7a90;
      padding: 6pt 8pt;
      text-align: left;
    }
    th { background: #e8eef7; }
  </style>
</head>
<body>
  <h1>Scriptorium Native PDF</h1>
  <p>This PDF is printed by Playwright from structured HTML.</p>
  <p>The result must become editable text nodes, not one page screenshot.</p>
  <table>
    <tr><th>Layer</th><th>Purpose</th><th>Status</th></tr>
    <tr><td>PDF text</td><td>Native extraction</td><td>Required</td></tr>
    <tr><td>HTML node</td><td>Local editing</td><td>Required</td></tr>
  </table>
</body>
</html>
""",
        encoding="utf-8",
    )
    return html_path
