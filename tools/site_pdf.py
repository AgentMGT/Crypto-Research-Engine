"""Print the built site's research pages into one PDF: site/research-engine.pdf.

Runs after the build in the refresh workflow, so the PDF always matches the
pages deployed with it. Tabs and collapsed sections are opened first so
nothing is hidden on paper. Usage: python tools/site_pdf.py [site_dir]
"""

import functools
import http.server
import io
import sys
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfWriter

SITE = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "site")
PAGES = [("index.html", "Crypto & markets"), ("bittensor.html", "Bittensor"), ("altcoins.html", "ALTCOINS")]
OUT = SITE / "research-engine.pdf"

OPEN_ALL = """() => {
  document.querySelectorAll('[hidden]').forEach(el => { if (!el.closest('.chart-modal')) el.hidden = false; });
  document.querySelectorAll('details').forEach(d => d.open = true);
  // Tab bars don't print, so title each panel with its tab's name.
  document.querySelectorAll('[data-tab]').forEach(b => {
    const panel = document.getElementById('tab-' + b.getAttribute('data-tab'));
    if (!panel || panel.querySelector('.print-title')) return;
    const h = document.createElement('h2'); h.className = 'print-title'; h.textContent = b.textContent.trim();
    panel.prepend(h);
  });
}"""


def main():
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a):
            pass

    handler = functools.partial(Quiet, directory=str(SITE))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}/"
    writer = PdfWriter()
    with sync_playwright() as p:
        kw = {"executable_path": sys_chromium()} if sys_chromium() else {}
        browser = p.chromium.launch(**kw)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.emulate_media(media="print", color_scheme="light")
        for path, title in PAGES:
            if not (SITE / path).exists():
                continue
            page.goto(base + path, wait_until="load")
            page.wait_for_timeout(800)
            page.evaluate(OPEN_ALL)
            pdf = page.pdf(format="Letter", landscape=True, print_background=True,
                           margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
                           display_header_footer=True, header_template="<span></span>",
                           footer_template=f'<div style="font-size:8px;width:100%;text-align:center;color:#888">{title} · page <span class="pageNumber"></span> of <span class="totalPages"></span></div>')
            start = len(writer.pages)
            writer.append(io.BytesIO(pdf))
            writer.add_outline_item(title, start)
            print(f"  pdf: {path} -> {len(writer.pages) - start} pages")
        browser.close()
    srv.shutdown()
    writer.add_metadata({"/Title": "Crypto Research Engine"})
    with open(OUT, "wb") as f:
        writer.write(f)
    print(f"wrote {OUT} ({OUT.stat().st_size // 1024} KB, {len(writer.pages)} pages)")


def sys_chromium():
    p = Path("/opt/pw-browsers/chromium")
    return str(p) if p.exists() else None


if __name__ == "__main__":
    main()
