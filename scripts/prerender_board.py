#!/usr/bin/env python3
"""Write the live deals into site/index.html so crawlers that skip JavaScript can read them.

The deploy workflow runs this on every deploy, after the board and sitemap are committed, so the
result is deployed but never committed: the repo's index.html stays the template.

How it works: serve site/ on localhost, open index.html in headless Chromium, let the page's own
script draw the deals from board.json, then copy the finished sections back into the HTML file.
Every request that isn't to localhost (Analytics, AdSense, fonts) is blocked, so a run never counts
as a visit or an ad request. On load, the page's script redraws the same sections from board.json,
so visitors see the same deals. <body> gets data-prerendered="<board date>", which tells the page
script to keep the written deals on screen until board.json has loaded.

Needs: pip install playwright && python -m playwright install chromium
Exits non-zero and leaves index.html untouched if anything looks wrong.
"""
import functools
import http.server
import json
import re
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
PAGE = SITE / "index.html"

# Sections the page script draws from board.json, copied as drawn (inner HTML).
SECTIONS = ["heroStats", "popList", "count", "tickets", "empty", "areaChips",
            "driveGroups", "driveEmpty", "boardFoot", "sources"]
# Elements whose hidden flag the page script sets; copied too.
FLAGS = ["popNav", "empty", "driveEmpty"]


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def render_sections():
    """Open the page in headless Chromium and return what its script drew."""
    from playwright.sync_api import sync_playwright

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(SITE)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    blocked = []

    def route(r):
        if r.request.url.startswith(base):
            r.continue_()
        else:
            blocked.append(r.request.url.split("?")[0])
            r.abort()

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                ctx = browser.new_context(viewport={"width": 1280, "height": 900}, service_workers="block")
                ctx.route("**/*", route)
                page = ctx.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                page.goto(base + "index.html", wait_until="domcontentloaded")
                page.wait_for_function(  # done when the stats line stops saying "Loading this week's deals"
                    "() => { const s = document.getElementById('heroStats'); return s && !/Loading this week/.test(s.textContent); }",
                    timeout=30000)
                got = page.evaluate(
                    """([ids, flags]) => ({
                        ready: !!document.querySelector('#heroStats .live-dot'),
                        html: Object.fromEntries(ids.map(id => [id, document.getElementById(id) ? document.getElementById(id).innerHTML : null])),
                        hidden: Object.fromEntries(flags.map(id => [id, document.getElementById(id) ? document.getElementById(id).hidden : null])),
                        cards: document.querySelectorAll('#tickets article.card').length,
                        drives: document.querySelectorAll('#driveGroups article.gcard').length,
                    })""", [SECTIONS, FLAGS])
            finally:
                browser.close()
    finally:
        server.shutdown()

    if errors:
        sys.exit("page errors while rendering: " + " | ".join(errors[:3]))
    if not got["ready"]:
        sys.exit("the page didn't finish loading the board")
    missing = [k for k, v in list(got["html"].items()) + list(got["hidden"].items()) if v is None]
    if missing:
        sys.exit("missing elements: " + ", ".join(missing))
    print(f"Rendered {got['cards']} packages and {got['drives']} drive-to deals; blocked {len(blocked)} outside requests")
    return got


def element_span(html, el_id):
    """(start_tag_start, start_tag_end, close_tag_start, close_tag_end) of the one element with this id."""
    hits = [m for m in re.finditer(r'<([a-zA-Z][\w-]*)\b[^>]*\sid="%s"[^>]*>' % re.escape(el_id), html)]
    if len(hits) != 1:
        raise ValueError(f'expected one element with id="{el_id}", found {len(hits)}')
    m = hits[0]
    tag = m.group(1).lower()
    depth = 1
    for t in re.finditer(r"<(/?)%s\b[^>]*>" % tag, html[m.end():], flags=re.I):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return m.start(), m.end(), m.end() + t.start(), m.end() + t.end()
    raise ValueError(f'no closing </{tag}> for id="{el_id}"')


def write_into(template, got, stamp):
    html = template
    for el_id in SECTIONS:
        _, open_end, close_start, _ = element_span(html, el_id)
        html = html[:open_end] + got["html"][el_id] + html[close_start:]
    for el_id in FLAGS:
        start, open_end, _, _ = element_span(html, el_id)
        tag = re.sub(r'\shidden(="[^"]*")?(?=[\s>/])', "", html[start:open_end])
        if got["hidden"][el_id]:
            tag = tag[:-1] + " hidden>"
        html = html[:start] + tag + html[open_end:]
    if html.count("<body>") != 1:
        raise ValueError("expected exactly one plain <body> tag")
    return html.replace("<body>", f'<body data-prerendered="{stamp}">')


def main():
    template = PAGE.read_text(encoding="utf-8")
    if re.search(r"<body[^>]*data-prerendered", template):
        sys.exit("index.html already has deals written in; run on the template")
    board = json.loads((SITE / "board.json").read_text(encoding="utf-8"))
    stamp = str((board.get("meta") or {}).get("updatedOn") or str(board.get("generatedAt", ""))[:10])
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
        sys.exit("board.json has no usable date")

    got = render_sections()
    html = write_into(template, got, stamp)

    # Sanity checks before writing anything.
    cards = len(re.findall(r'<article class="card"', html))
    drives = len(re.findall(r'<article class="gcard"', html))
    if cards != got["cards"] or drives != got["drives"]:
        sys.exit(f"card count mismatch: wrote {cards}/{drives}, rendered {got['cards']}/{got['drives']}")
    if cards == 0 and drives == 0:
        sys.exit("no deals rendered; leaving index.html as is")

    tmp = PAGE.with_suffix(".html.tmp")
    tmp.write_text(html, encoding="utf-8")
    tmp.replace(PAGE)
    print(f"Wrote {cards} packages and {drives} drive-to deals into index.html (board {stamp})")


if __name__ == "__main__":
    main()
