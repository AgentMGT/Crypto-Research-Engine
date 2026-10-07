"""Flatten site/index.html into site/preview.html for publishing as an Artifact page.

The Artifact runtime wraps the page it publishes in its own document skeleton, so
the page file must carry only the title, styles and body content. Supporting files
(bittensor.html, style.css, app.js) are published alongside and keep their own.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"

html = (SITE / "index.html").read_text()
css = (SITE / "style.css").read_text()
js = (SITE / "app.js").read_text()

title = re.search(r"<title>(.*?)</title>", html, re.S).group(1).strip()
body = re.search(r"<body>(.*?)</body>", html, re.S).group(1)
body = body.replace('<script src="app.js"></script>', f"<script>{js}</script>")

(SITE / "preview.html").write_text(
    f"<title>{title}</title>\n<style>\n{css}</style>\n{body}"
)
print(f"wrote {SITE / 'preview.html'}")
