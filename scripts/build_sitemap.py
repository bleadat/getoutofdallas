#!/usr/bin/env python3
"""Rebuild site/sitemap.xml from the pages in site/.

Every public page is listed at its canonical URL with a <lastmod> date:
- the home page takes the deal board's date (board.json "generatedAt"), because
  the deals are what change on it every morning;
- any other page takes its "Last updated <Month D, YYYY>" line when it has one
  (Terms, Privacy), otherwise the date already in the sitemap.

Pages marked noindex, 404.html and Search Console verification files
(google*.html) are left out.

.github/workflows/deploy.yml runs this after it pulls the morning board and commits
the result with the board. It never fails the deploy: on any problem it keeps the
current sitemap and says why.
"""
import datetime
import html.parser
import json
import os
import pathlib
import re
import sys
from xml.sax.saxutils import escape

SITE = pathlib.Path(os.environ.get("SITE_DIR", "site"))
OUT = SITE / "sitemap.xml"
BASE = "https://getoutofdallas.com/"
SKIP = re.compile(r"^(404|google[0-9a-f]+)\.html$")
UPDATED = re.compile(r"Last updated\s+([A-Z][a-z]+)\s+(\d{1,2}),\s+(\d{4})")
MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
ENTRY = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>\s*(?:<lastmod>\s*([^<\s]+)\s*</lastmod>)?")


class Head(html.parser.HTMLParser):
    """Picks up a page's canonical URL and robots meta tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.canonical = None
        self.robots = ""

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link" and "canonical" in a.get("rel", "").lower().split() and self.canonical is None:
            self.canonical = a.get("href", "").strip()
        elif tag == "meta" and a.get("name", "").lower() in ("robots", "googlebot"):
            self.robots += " " + a.get("content", "").lower()


def page_url(rel, canonical):
    if canonical and canonical.startswith(BASE):
        return canonical
    if rel == "index.html":
        return BASE
    if rel.endswith("/index.html"):
        return BASE + rel[: -len("index.html")]
    return BASE + rel


def updated_date(text):
    """The page's "Last updated October 28, 2026" line as 2026-10-28, or None."""
    m = UPDATED.search(text)
    if not m or m.group(1) not in MONTHS:
        return None
    return datetime.date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2))).isoformat()


def board_date():
    board = json.loads((SITE / "board.json").read_text(encoding="utf-8"))
    return datetime.datetime.fromisoformat(str(board["generatedAt"])).date().isoformat()


def current_dates():
    """{loc: lastmod} from the sitemap as it is now, used when a page has no date of its own."""
    try:
        text = OUT.read_text(encoding="utf-8")
    except OSError:
        return {}
    return {loc: mod for loc, mod in ENTRY.findall(text) if mod}


def build():
    old = current_dates()
    pages = {}
    for path in sorted(SITE.rglob("*.html")):
        rel = path.relative_to(SITE).as_posix()
        if SKIP.match(path.name) or any(part.startswith(".") for part in rel.split("/")):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        head = Head()
        head.feed(text)
        if "noindex" in head.robots:
            continue
        url = page_url(rel, head.canonical)
        if url in pages:
            continue
        date = None
        if url == BASE:
            try:
                date = board_date()
            except Exception as exc:
                print("::warning::Couldn't read the board's date (" + type(exc).__name__ + "); keeping the home page's current date")
        else:
            date = updated_date(text)
        pages[url] = date or old.get(url)
    if BASE not in pages:
        print("::warning::No home page found in " + str(SITE) + "; keeping the current sitemap")
        return None
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in sorted(pages, key=lambda u: (u != BASE, u)):
        lines.append("  <url>")
        lines.append("    <loc>" + escape(url) + "</loc>")
        if pages[url]:
            lines.append("    <lastmod>" + pages[url] + "</lastmod>")
        lines.append("  </url>")
    lines.append("</urlset>")
    return "\n".join(lines) + "\n", pages


def main():
    try:
        result = build()
        if result is None:
            return 0
        new, pages = result
        if OUT.exists() and OUT.read_bytes() == new.encode("utf-8"):
            print("Sitemap unchanged (" + str(len(pages)) + " pages)")
            return 0
        OUT.write_bytes(new.encode("utf-8"))
        print("Sitemap updated:")
        for url in sorted(pages, key=lambda u: (u != BASE, u)):
            print("  " + url + "  " + (pages[url] or "(no date)"))
    except Exception as exc:
        print("::warning::Couldn't rebuild the sitemap (" + type(exc).__name__ + ": " + str(exc) + "); keeping the current one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
