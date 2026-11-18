#!/usr/bin/env python3
"""Build the hub and place pages from site/board.json.

Writes, on every deploy, before the sitemap step (deployed, never committed):
  site/packages/index.html    "Vacation packages from Dallas": every live package, grouped by region
  site/drive-to/index.html    "Weekend getaways from Dallas": every live drive-to deal, grouped by area
  site/packages/<slug>/index.html and site/drive-to/<slug>/index.html, one per content/<folder>/<slug>.md

site/packages/ and site/drive-to/ are generated folders: this script replaces them on every run.

A place page file (content/packages/cancun.md, content/drive-to/broken-bow.md) is front matter plus Markdown:
  ---
  title: Cancun Vacation Packages from Dallas      <title>; " · GetoutofDallas.com" is added
  h1: Cancun vacation packages from Dallas
  description: One or two sentences for search results.
  intro: The 2-3 sentence answer shown under the H1.
  match: destination~=Cancun,Isla Mujeres; destCode=CUN
                                                  any clause can match: field=a,b (exact) or field~=a,b (contains);
                                                  packages use destination/country/destCode/region/title,
                                                  drive-to pages areaKey/area/title
  pinned: tz-some-deal-id, exp-another-id          shown first, in this order, while live on the board
  photo: cancun-beach-palapas                      a key in index.html's PHOTOS (the share image)
  ---
  ## Getting there from Dallas
  Paragraphs, ## / ### headings, - bullets, 1. lists, **bold**, [links](/packages/).

Standard library only. Exits non-zero, leaving the old pages in place, if board.json or index.html can't be read.
"""
import datetime
import hashlib
import html
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
CONTENT = ROOT / "content"
BASE = "https://getoutofdallas.com"
GENERATED = ["packages", "drive-to"]

GA = """<!-- Google tag (gtag.js) -->
<script async src="https://www.googletagmanager.com/gtag/js?id=G-MBB366Q730"></script>
<script>
  window.dataLayer = window.dataLayer || [];
  function gtag(){dataLayer.push(arguments);}
  gtag('js', new Date());

  gtag('config', 'G-MBB366Q730');
</script>
<!-- Google AdSense -->
<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-2936333423174343"
     crossorigin="anonymous"></script>"""

# Sends the same GA4 events as the home page: deal_click for "View deal", link_click for everything else.
TRACK = """<script>
(function () {
  function send(e) {
    if (e.type === 'auxclick' && e.button !== 1) return;
    var a = e.target.closest && e.target.closest('a[href]');
    if (!a || typeof gtag !== 'function') return;
    var u; try { u = new URL(a.href, location.href); } catch (_) { return; }
    var card = a.closest('[data-deal]'), sec = a.closest('[data-section]');
    var clip = function (s) { return String(s || '').trim().slice(0, 100); };
    var p = { link_url: clip(a.href), link_domain: u.hostname, link_text: clip(a.textContent), outbound: u.hostname !== location.hostname,
      section: sec ? sec.getAttribute('data-section') : 'page', page_type: document.body.getAttribute('data-page') };
    if (card && a.hasAttribute('data-view')) {
      p.link_type = 'deal'; p.deal_id = card.getAttribute('data-deal'); p.deal_name = clip(card.getAttribute('data-name'));
      p.deal_type = card.getAttribute('data-type'); p.destination = clip(card.getAttribute('data-dest'));
      p.seller = clip(card.getAttribute('data-seller')); p.deal_price = Number(card.getAttribute('data-price')) || undefined;
      gtag('event', 'deal_click', p);
    } else {
      p.link_type = p.outbound ? 'outbound' : 'internal';
      gtag('event', 'link_click', p);
    }
  }
  document.addEventListener('click', send, true);
  document.addEventListener('auxclick', send, true);
})();
</script>"""

PLANE = "M21 16v-2l-8-5V3.5c0-.83-.67-1.5-1.5-1.5S10 2.67 10 3.5V9l-8 5v2l8-2.5V19l-2 1.5V22l3.5-1 3.5 1v-1.5L13 19v-5.5l8 2.5z"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]

# Package regions, in page order. A deal goes in the first bucket whose words appear in its region.
BUCKETS = [
    ("mexico", "Mexico", ("mexic",)),
    ("caribbean", "Caribbean & Bermuda", ("caribbean", "atlantic", "bahamas", "bermuda")),
    ("central-america", "Central America", ("central america",)),
    ("hawaii", "Hawaii", ("hawaii",)),
    ("europe", "Europe", ("europe",)),
    ("asia-pacific", "Asia & the Pacific", ("asia", "pacific", "oceania", "australia")),
    ("usa", "Around the U.S.", ("midwest", "northeast", "southwest", "southeast", "west coast", "east coast", "united states")),
]
OTHER = ("more", "More trips")


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def chicago_today():
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo("America/Chicago")).date()
    except Exception:
        return (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=6)).date()


def ymd(s):
    try:
        return datetime.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


def md(d):
    return f"{MONTHS[d.month - 1][:3]} {d.day}"


def long_date(d):
    return f"{MONTHS[d.month - 1]} {d.day}, {d.year}"


def money(n):
    return "$" + f"{round(float(n)):,}"


# ---------- data from the home page: photos and icons ----------

def page_assets():
    src = (SITE / "index.html").read_text(encoding="utf-8")
    m = re.search(r"const PHOTOS = (\{.*?\});\s*$", src, re.M)
    photos = json.loads(m.group(1)) if m else {}

    def obj(name):
        m = re.search(r"const %s = \{([^}]*)\}" % name, src)
        return dict(re.findall(r"'?([\w-]+)'?\s*:\s*'([^']+)'", m.group(1))) if m else {}

    def path(name, fallback):
        m = re.search(r"const %s = '([^']+)'" % name, src)
        return m.group(1) if m else fallback

    return {
        "photos": photos,
        "by_code": obj("PHOTO_BY_CODE"),
        "by_area": obj("PHOTO_BY_AREA"),
        "by_band": obj("PHOTO_BY_BAND"),
        "cal": path("P_CAL", "M19 4h-1V2h-2v2H8V2H6v2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2zm0 16H5V10h14v10z"),
        "car": path("P_CAR", "M5 11l1.5-4.5h11L19 11H5zm13.92-4.99C18.72 5.42 18.16 5 17.5 5h-11c-.66 0-1.21.42-1.42 1.01L3 12v8c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-1h12v1c0 .55.45 1 1 1h1c.55 0 1-.45 1-1v-8l-2.08-5.99z"),
    }


class Photos:
    def __init__(self, assets):
        self.a = assets
        self.used = {}

    def pick(self, key="", code="", area="", band=""):
        p = self.a["photos"]
        for k in (key, self.a["by_code"].get(code), self.a["by_area"].get(area), self.a["by_band"].get(band), "pc-palms-beach"):
            if k and k in p:
                self.used[k] = p[k]
                return k, p[k]
        return None, None

    def img(self, key="", code="", area="", band=""):
        slug, info = self.pick(key, code, area, band)
        if not slug:
            return ""
        alt, place, creator, lic, lic_url, source = (info + [""] * 6)[:6]
        credit = (f'<a class="credit" href="{esc(source)}" target="_blank" rel="noopener noreferrer" title="{esc(place)} · {esc(lic)}">Photo: {esc(creator)}</a>'
                  if str(source).startswith("http") else f'<span class="credit">Photo: {esc(creator)}</span>')
        return (f'<img src="/photos/{esc(slug)}.jpg" alt="{esc(alt)}" width="960" height="480" loading="lazy" decoding="async">'
                + credit)

    def credits_html(self):
        items = []
        for slug, info in self.used.items():
            alt, place, creator, lic, lic_url, source, title = (info + [""] * 7)[:7]
            items.append(f'<li>{esc(place)}: <a href="{esc(source)}" target="_blank" rel="noopener noreferrer">“{esc(title or alt)}”</a>'
                         f' by {esc(creator)}, <a href="{esc(lic_url)}" target="_blank" rel="noopener noreferrer">{esc(lic)}</a></li>')
        return "\n".join(items)


# ---------- deals ----------

def text(v):
    return str(v).strip() if isinstance(v, (str, int, float)) and str(v).strip() else ""


def items(v):
    return [text(x) for x in v if text(x)] if isinstance(v, list) else []


def num(v):
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def is_live(d, today):
    book, end, start = ymd(d.get("bookBy")), ymd(d.get("travelEnd")), ymd(d.get("travelStart"))
    if book and book < today:
        return False
    if end and end < today:
        return False
    if d.get("fixedDates") is True and start and start <= today:
        return False
    return True


def status(d, today, drive=False):
    book = ymd(d.get("bookBy"))
    days = (book - today).days if book else None
    if days is not None and days <= 7:
        return "last", "Last call · book today" if days <= 0 else f"Last call · book by {md(book)}"
    start = ymd(d.get("travelStart"))
    if d.get("fixedDates") is True and start and (start - today).days <= 21:
        lead = (start - today).days
        return "boarding", f"{'Check-in in' if drive else 'Leaves in'} {lead} day{'' if lead == 1 else 's'}"
    if book:
        return "book", f"Book by {md(book)}"
    end = ymd(d.get("travelEnd"))
    if drive and end and (end - today).days <= 75:
        return "book", f"Through {md(end)}"
    found = ymd(d.get("foundOn"))
    if found and 0 <= (today - found).days <= 3:
        return "new", "New"
    return "sale", "On sale"


def stamp(d):
    checked, found = ymd(d.get("checkedOn")), ymd(d.get("foundOn"))
    if checked and (not found or checked > found):
        line = f"Price checked {md(checked)}"
    elif found:
        line = f"Found {md(found)}"
    else:
        return ""
    return line + (f" · sold by {text(d.get('provider'))}" if text(d.get("provider")) else "")


def view_button(d):
    url = text(d.get("url"))
    if not url.startswith("https://") and not url.startswith("http://"):
        return '<span class="btn-dark is-off" aria-disabled="true">Link unavailable</span>'
    seller = text(d.get("provider")) or "the seller’s site"
    return (f'<a class="btn-dark" href="{esc(url)}" target="_blank" rel="noopener noreferrer" data-view '
            f'aria-label="View deal on {esc(seller)}: {esc(text(d.get("title")))}">View deal</a>')


def details(rows):
    rows = [r for r in rows if r]
    return f'<details class="more"><summary>Details</summary>{"".join(rows)}</details>' if rows else ""


def labeled(label, value):
    return f"<p><b>{esc(label)}</b> {esc(value)}</p>" if value else ""


def perks_list(d):
    perks = items(d.get("perks"))
    return "<ul>" + "".join(f"<li>{esc(p)}</li>" for p in perks) + "</ul>" if perks else ""


def package_card(d, today, photos, icons):
    key, pill = status(d, today)
    price, top, nights = num(d.get("price")), num(d.get("priceMax")), num(d.get("nights"))
    if price is None:
        return ""
    amount = f"{money(price)}–{money(top)}" if top and top > price else money(price)
    per = "per person" + (f" · {money(price / nights)}/night" if nights else "")
    origin = text(d.get("originCode")) or "DFW"
    origin = "Dallas" if len(origin) > 3 else origin
    stars = num(d.get("stars"))
    title, dest = text(d.get("title")), text(d.get("destination")) or text(d.get("title"))
    sub = " · ".join(x for x in [title + (f" · {stars:g}★" if stars else ""), f"from {origin}"] if x)
    incl = " · ".join(items(d.get("includes")))
    when = text(d.get("travelLabel"))
    nights_txt = f"{nights:g} night{'' if nights == 1 else 's'}" if nights else ""
    return f"""<article class="card" id="deal-{esc(d['id'])}" data-deal="{esc(d['id'])}" data-type="package" data-name="{esc(title)}" data-dest="{esc(dest)}" data-seller="{esc(text(d.get('provider')))}" data-price="{price:g}">
  <div class="card-img">{photos.img(text(d.get('photoKey')), text(d.get('destCode')), '', text(d.get('band')))}<span class="badge st-{key}">{esc(pill)}</span></div>
  <div class="card-body">
    <div class="card-meta"><span>{esc(text(d.get('provider')) or 'Seller')}</span>{f'<span>{esc(nights_txt)}</span>' if nights_txt else ''}</div>
    <h3>Trip to {esc(dest)}</h3>
    <p class="card-sub">{esc(sub)}</p>
    {f'<p class="when"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="{icons["cal"]}"></path></svg><span>{esc(when)}</span></p>' if when else ''}
    {f'<p class="card-incl">{esc(incl)}</p>' if incl else ''}
    <div class="card-foot"><div class="price"><span class="price-amt">{amount}</span><span class="price-per">{esc(per)}</span></div>{view_button(d)}</div>
    {details([labeled('Includes:', incl) if len(incl) > 90 else '', perks_list(d), labeled('Trip:', text(d.get('kind'))), labeled('Price:', text(d.get('priceNote'))),
              f'<p>{esc(text(d.get("caveat")))}</p>' if text(d.get('caveat')) else '', f'<p>{esc(stamp(d))}</p>' if stamp(d) else ''])}
  </div>
</article>"""


def drive_card(d, today, photos, icons):
    key, pill = status(d, today, drive=True)
    price = num(d.get("price"))
    miles, sleeps = num(d.get("driveMiles")), num(d.get("sleeps"))
    line = " · ".join(x for x in [text(d.get("driveTime")) or "From Dallas", f"{round(miles)} mi" if miles else "", f"sleeps {sleeps:g}" if sleeps else ""] if x)
    title, area = text(d.get("title")), text(d.get("area"))
    where = text(d.get("nearby")) or text(d.get("location"))
    when, code = text(d.get("travelLabel")), text(d.get("code"))
    amount = f"from {money(price)}" if price is not None else (text(d.get("discount")) or "Special offer")
    per = "per night" + (f" · {text(d.get('discount'))}" if price is not None and text(d.get("discount")) else "") if price is not None else "see the seller for dates"
    return f"""<article class="card" id="deal-{esc(d['id'])}" data-deal="{esc(d['id'])}" data-type="drive_to" data-name="{esc(title)}" data-dest="{esc(area)}" data-seller="{esc(text(d.get('provider')))}" data-price="{'' if price is None else f'{price:g}'}">
  <div class="card-img">{photos.img(text(d.get('photoKey')), '', text(d.get('areaKey')), 'road')}<span class="badge st-{key}">{esc(pill)}</span></div>
  <div class="card-body">
    <p class="drive-line"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="{icons['car']}"></path></svg><span>{esc(line)}</span></p>
    <h3>{esc(title)}</h3>
    {f'<p class="card-sub">{esc(text(d.get("offer")))}</p>' if text(d.get('offer')) else ''}
    {f'<p class="card-where">{esc(where)}</p>' if where and where not in area else ''}
    {f'<p class="when"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path d="{icons["cal"]}"></path></svg><span>{esc(when)}</span></p>' if when else ''}
    {f'<p class="code">Promo code <b>{esc(code)}</b></p>' if code else ''}
    <div class="card-foot"><div class="price"><span class="price-amt">{esc(amount)}</span><span class="price-per">{esc(per)}</span></div>{view_button(d)}</div>
    {details([labeled('Includes:', ' · '.join(items(d.get('includes')))), perks_list(d), labeled('Stay:', text(d.get('kind'))), labeled('Price:', text(d.get('priceNote'))),
              f'<p>{esc(text(d.get("caveat")))}</p>' if text(d.get('caveat')) else '', f'<p>{esc(stamp(d))}</p>' if stamp(d) else ''])}
  </div>
</article>"""


def bucket_of(d):
    region = text(d.get("region")).lower()
    for key, name, words in BUCKETS:
        if any(w in region for w in words):
            return key, name
    return OTHER


# ---------- place-page files ----------

def read_place(path):
    raw = path.read_text(encoding="utf-8")
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", raw, re.S)
    if not m:
        raise ValueError(f"{path.name}: no front matter")
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            k, v = line.split(":", 1)
            meta[k.strip().lower()] = v.strip()
    for need in ("title", "h1", "description", "intro"):
        if not meta.get(need):
            raise ValueError(f"{path.name}: missing {need}")
    meta["slug"] = path.stem
    meta["body"] = m.group(2)
    return meta


def matcher(spec):
    clauses = []
    for part in [p.strip() for p in spec.split(";") if p.strip()]:
        m = re.match(r"(\w+)\s*(~?=)\s*(.+)", part)
        if m:
            clauses.append((m.group(1), m.group(2), [v.strip().lower() for v in m.group(3).split(",") if v.strip()]))

    def ok(d):
        for field, op, values in clauses:
            have = text(d.get(field)).lower()
            if have and (have in values if op == "=" else any(v in have for v in values)):
                return True
        return False
    return ok


def inline(s):
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
    return re.sub(r"\[([^\]]+)\]\(((?:https?://|/)[^)\s]*)\)", lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)


def markdown(src):
    out, para, lst = [], [], None

    def flush():
        nonlocal para, lst
        if para:
            out.append(f"<p>{inline(' '.join(para))}</p>")
            para = []
        if lst:
            out.append(f"<{lst[0]}>" + "".join(f"<li>{inline(i)}</li>" for i in lst[1]) + f"</{lst[0]}>")
            lst = None

    for line in src.splitlines():
        s = line.strip()
        h = re.match(r"(#{2,3})\s+(.*)", s)
        b = re.match(r"[-*]\s+(.*)", s)
        n = re.match(r"\d+\.\s+(.*)", s)
        if not s:
            flush()
        elif h:
            flush()
            out.append(f"<h{len(h.group(1))}>{inline(h.group(2))}</h{len(h.group(1))}>")
        elif b or n:
            kind = "ul" if b else "ol"
            if para or (lst and lst[0] != kind):
                flush()
            lst = lst or (kind, [])
            lst[1].append((b or n).group(1))
        else:
            if lst:
                flush()
            para.append(s)
    flush()
    return "\n".join(out)


# ---------- page shell ----------

def shell(*, path, title, description, h1, lede, updated, crumbs, jump, main, notes, photos, page_type, og_image, css_v, extra_head=""):
    url = BASE + path
    crumb_ld = {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i + 1, "name": name, "item": BASE + href} for i, (name, href) in enumerate(crumbs)]}
    crumb_html = '<span class="sep" aria-hidden="true">›</span>'.join(
        f'<a href="{esc(href)}">{esc(name)}</a>' if i < len(crumbs) - 1 else f'<span aria-current="page">{esc(name)}</span>'
        for i, (name, href) in enumerate(crumbs))
    nav_on = lambda p: ' aria-current="page"' if path.startswith(p) else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
{GA}
<title>{esc(title)} · GetoutofDallas.com</title>
<meta name="description" content="{esc(description)}">
<link rel="canonical" href="{url}">
<link rel="icon" href="/favicon.svg" type="image/svg+xml">
<link rel="icon" href="/favicon-96.png" sizes="96x96" type="image/png">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<meta name="theme-color" content="#0D3B45">
<meta property="og:type" content="website">
<meta property="og:site_name" content="GetoutofDallas.com">
<meta property="og:title" content="{esc(title)}">
<meta property="og:description" content="{esc(description)}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{BASE}{og_image}">
<meta name="twitter:card" content="summary_large_image">
<meta name="generator" content="build_pages.py">
<script type="application/ld+json">
{json.dumps(crumb_ld, ensure_ascii=False, indent=2)}
</script>{extra_head}
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Poppins:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="/pages.css?v={css_v}">
</head>
<body data-page="{esc(page_type)}">
<div class="page">
  <header class="panel mast">
    <div class="top">
      <a class="brand" href="/"><span class="brand-mark"><svg class="ico" viewBox="0 0 24 24" aria-hidden="true"><path transform="rotate(90 12 12)" d="{PLANE}"></path></svg></span><span>getoutofdallas</span></a>
      <nav class="topnav" aria-label="Sections">
        <a href="/packages/"{nav_on('/packages/')}>Fly + stay</a>
        <a href="/drive-to/"{nav_on('/drive-to/')}>Drive-to</a>
        <a href="/#kiosk">Price-check</a>
        <a href="/#how">How it works</a>
      </nav>
    </div>
    <nav class="crumbs" aria-label="Breadcrumb">{crumb_html}</nav>
    <h1>{esc(h1)}</h1>
    <p class="lede">{esc(lede)}</p>
    <p class="stamp-line"><span class="live-dot" aria-hidden="true"></span><span>Last updated {long_date(updated)} · prices re-checked every morning</span></p>
    {jump}
  </header>
{main}
{notes}
  <footer class="panel deep">
    <div class="foot">
      <span class="foot-brand">getoutofdallas</span>
      <div class="foot-copy">
        <p>An independent deal board, not affiliated with any airline, airport, hotel or travel seller. Prices change constantly; the seller’s page has the final word. Destination photos come from photographers on Flickr under Creative Commons licenses or the public domain, cropped and resized; they show the destination, not the specific hotel, resort or cabin, unless the photo names the building.</p>
        <details class="credits"><summary>Photo credits</summary><ul>
{photos.credits_html()}
        </ul></details>
        <p class="foot-links"><a href="/">All deals</a><a href="/packages/">Vacation packages from Dallas</a><a href="/drive-to/">Weekend getaways from Dallas</a><a href="/terms.html">Terms of Service</a><a href="/privacy.html">Privacy Policy</a><a href="mailto:hello@getoutofdallas.com">Contact</a></p>
        <p>© 2026 Viz Studio LLC</p>
      </div>
    </div>
  </footer>
</div>
{TRACK}
</body>
</html>
"""


def jump_links(links):
    if not links:
        return ""
    return '<nav class="jump" aria-label="On this page">' + "".join(
        f'<a href="{esc(href)}">{esc(label)}<span class="n">{esc(n)}</span></a>' for label, href, n in links) + "</nav>"


def empty_box(title, body_html):
    return f'<div class="empty-box"><h3>{esc(title)}</h3><p>{body_html}</p></div>'


PACKAGE_NOTES = """  <section class="panel notes" aria-labelledby="howTitle">
    <h2 id="howTitle">How these prices work</h2>
    <div class="fine-grid">
      <div><h3>A Dallas price, or it doesn’t post</h3><p>Every trip shows a price for flying out of DFW or Love Field, or the seller’s stated Dallas add-on. Headline prices from other cities don’t count.</p></div>
      <div><h3>Per person, two sharing</h3><p>Package prices are per traveler for two adults in one room, as advertised the day the deal was found.</p></div>
      <div><h3>Checked every morning</h3><p>A daily check re-confirms prices and retires sold-out deals; a full search for new ones runs every Monday.</p></div>
      <div><h3>Book with the seller</h3><p>Links open the seller’s own page; confirm the final price there. Refunds, changes and cancellations go through the seller. Some Travelzoo offers need a Travelzoo Club membership.</p></div>
    </div>
    <div class="next-links"><a href="/drive-to/">Weekend getaways you can drive to</a><a href="/#kiosk">Price-check your own trip</a><a href="/">All of this week’s deals</a></div>
  </section>"""

DRIVE_NOTES = """  <section class="panel notes" aria-labelledby="howTitle">
    <h2 id="howTitle">How these prices work</h2>
    <div class="fine-grid">
      <div><h3>Prices are per night</h3><p>The seller’s starting nightly rate for the whole cabin or room, or the discount when no rate is posted.</p></div>
      <div><h3>Promo codes</h3><p>When a card shows a code, enter it on the seller’s booking page to get that offer.</p></div>
      <div><h3>Checked every morning</h3><p>A daily check re-confirms offers and retires expired ones; a full search for new ones runs every Monday.</p></div>
      <div><h3>Book with the seller</h3><p>Links open the hotel’s or cabin company’s own page; confirm the final price there. Changes and cancellations go through them.</p></div>
    </div>
    <div class="next-links"><a href="/packages/">Flight + hotel packages from Dallas</a><a href="/#kiosk">Price-check a trip</a><a href="/">All of this week’s deals</a></div>
  </section>"""


def place_notes(body_html):
    return f'  <section class="panel notes" data-section="place_notes">\n{body_html}\n  </section>' if body_html.strip() else ""


# ---------- build ----------

def build():
    board = json.loads((SITE / "board.json").read_text(encoding="utf-8"))
    assets = page_assets()
    if not assets["photos"]:
        raise ValueError("couldn't read PHOTOS from index.html")
    today = chicago_today()
    meta = board.get("meta") or {}
    updated = ymd(meta.get("updatedOn")) or ymd(board.get("generatedAt")) or today
    css = (SITE / "pages.css").read_bytes()
    css_v = hashlib.sha256(css).hexdigest()[:8]

    deals = [d for d in board.get("deals", []) if isinstance(d, dict) and text(d.get("id")) and is_live(d, today) and num(d.get("price")) is not None]
    drives = [d for d in board.get("drives", []) if isinstance(d, dict) and text(d.get("id")) and is_live(d, today)]
    deals.sort(key=lambda d: num(d["price"]))
    pages = {}

    # Place pages first, so the hubs can link to them.
    places = {"packages": [], "drive-to": []}
    for folder in places:
        for f in sorted((CONTENT / folder).glob("*.md")) if (CONTENT / folder).is_dir() else []:
            places[folder].append(read_place(f))

    # /packages/
    photos = Photos(assets)
    icons = assets
    groups = {}
    for d in deals:
        groups.setdefault(bucket_of(d), []).append(d)
    order = [(k, n) for k, n, _ in BUCKETS] + [OTHER]
    sections, jump = [], []
    for key, name in order:
        group = groups.get((key, name))
        if not group:
            continue
        cheapest = min(num(d["price"]) for d in group)
        jump.append((name, f"#{key}", str(len(group))))
        sections.append(f"""    <section class="group" id="{key}" aria-labelledby="{key}-h">
      <div class="group-head"><h2 id="{key}-h">{esc(name)}</h2><p class="group-meta">{len(group)} trip{'' if len(group) == 1 else 's'} · from {money(cheapest)} per person</p></div>
      <div class="cards">
{chr(10).join(package_card(d, today, photos, icons) for d in group)}
      </div>
    </section>""")
    place_jump = [(p["h1"].replace(" vacation packages from Dallas", "").replace(" from Dallas", ""), f"/packages/{p['slug']}/", "→") for p in places["packages"]]
    lowest = money(deals[0]["price"]) if deals else None
    main = (f'  <main class="panel list" data-section="packages_hub">\n' + ("\n".join(sections) if sections else
            empty_box("No live packages right now", 'Everything on the last board sold out or passed its dates. <a href="/">See this week’s deals</a> or check back after the morning price check.'))
            + "\n  </main>")
    top_places = []
    for d in deals:
        name = text(d.get("destination"))
        if name and name not in top_places:
            top_places.append(name)
    desc = (f"{len(deals)} flight + hotel packages from DFW and Love Field, re-checked every morning. From {lowest} per person: "
            + ", ".join(top_places[:4]) + " and more.") if deals else "Flight + hotel packages from DFW and Love Field, re-checked every morning."
    pages["packages/index.html"] = shell(
        path="/packages/", title=f"Vacation Packages from Dallas: {MONTHS[today.month - 1]} {today.year} Deals",
        description=desc, h1="Vacation packages from Dallas",
        lede=(f"{len(deals)} flight + hotel trips with a real Dallas price, cheapest first by region. "
              "Prices are per person for two travelers sharing a room."),
        updated=updated, crumbs=[("Home", "/"), ("Vacation packages from Dallas", "/packages/")],
        jump=jump_links(place_jump + jump), main=main, notes=PACKAGE_NOTES, photos=photos, page_type="packages_hub",
        og_image="/photos/hero-beach-boat.jpg", css_v=css_v)

    # /drive-to/
    photos = Photos(assets)
    areas = {}
    for d in drives:
        areas.setdefault(text(d.get("areaKey")) or "more", []).append(d)
    area_list = sorted(areas.items(), key=lambda kv: (num(kv[1][0].get("driveMiles")) or 9999))
    sections, jump = [], []
    for key, group in area_list:
        group.sort(key=lambda d: (num(d.get("price")) is None, num(d.get("price")) or 0))
        first = group[0]
        name = text(first.get("area")) or "More drives"
        miles = num(first.get("driveMiles"))
        meta_line = " · ".join(x for x in [text(first.get("driveTime")), f"{round(miles)} mi from Dallas" if miles else "", text(first.get("route"))] if x)
        jump.append((name.split(",")[0], f"#{key}", str(len(group))))
        sections.append(f"""    <section class="group" id="{esc(key)}" aria-labelledby="{esc(key)}-h">
      <div class="group-head"><h2 id="{esc(key)}-h">{esc(name)}</h2><p class="group-meta">{esc(meta_line)}</p></div>
      <div class="cards">
{chr(10).join(drive_card(d, today, photos, icons) for d in group)}
      </div>
    </section>""")
    place_jump = [(p["h1"].split(",")[0].replace(" getaways from Dallas", ""), f"/drive-to/{p['slug']}/", "→") for p in places["drive-to"]]
    main = (f'  <main class="panel list" data-section="drive_to_hub">\n' + ("\n".join(sections) if sections else
            empty_box("No drive-to deals right now", 'The Monday check adds cabin and hotel deals within a few hours of Dallas. <a href="/">See this week’s deals</a>.'))
            + "\n  </main>")
    area_names = [text(g[0].get("area")).split(",")[0] for _, g in area_list if text(g[0].get("area"))]
    pages["drive-to/index.html"] = shell(
        path="/drive-to/", title=f"Weekend Getaways from Dallas: {MONTHS[today.month - 1]} Cabin & Hotel Deals",
        description=(f"{len(drives)} cabin and hotel deals a few hours’ drive from Dallas, re-checked every morning: "
                     + ", ".join(area_names[:4]) + "." if drives else "Cabin and hotel deals a few hours’ drive from Dallas, re-checked every morning."),
        h1="Weekend getaways from Dallas",
        lede=(f"{len(drives)} cabin and hotel deals within a few hours’ drive, nearest first. Prices are the seller’s starting rate per night."),
        updated=updated, crumbs=[("Home", "/"), ("Weekend getaways from Dallas", "/drive-to/")],
        jump=jump_links(place_jump + jump), main=main, notes=DRIVE_NOTES, photos=photos, page_type="drive_to_hub",
        og_image="/photos/broken-bow-lake.jpg", css_v=css_v)

    # Place pages
    for folder, plist in places.items():
        pool = deals if folder == "packages" else drives
        card = package_card if folder == "packages" else drive_card
        hub = ("Vacation packages from Dallas", "/packages/") if folder == "packages" else ("Weekend getaways from Dallas", "/drive-to/")
        for p in plist:
            photos = Photos(assets)
            ok = matcher(p.get("match", ""))
            pinned = [x.strip() for x in p.get("pinned", "").split(",") if x.strip()]
            by_id = {d["id"]: d for d in pool}
            shown = [by_id[i] for i in pinned if i in by_id]
            shown += [d for d in pool if ok(d) and d not in shown]
            cards = "\n".join(card(d, today, photos, icons) for d in shown)
            main = (f'  <main class="panel list" data-section="place">\n'
                    + (f'    <section class="group" id="deals"><div class="group-head"><h2>This week’s deals</h2><p class="group-meta">{len(shown)} live</p></div>\n      <div class="cards">\n{cards}\n      </div>\n    </section>'
                       if shown else empty_box("No live deal today", f'Deals come and go; this page shows the next one the morning it’s found. Meanwhile, see <a href="{hub[1]}">{esc(hub[0].lower())}</a> or <a href="/">all of this week’s deals</a>.'))
                    + "\n  </main>")
            share = photos.pick(p.get("photo", ""))[0] or "hero-beach-boat"
            pages[f"{folder}/{p['slug']}/index.html"] = shell(
                path=f"/{folder}/{p['slug']}/", title=p["title"], description=p["description"], h1=p["h1"], lede=p["intro"],
                updated=updated, crumbs=[("Home", "/"), hub, (p["h1"], f"/{folder}/{p['slug']}/")],
                jump="", main=main, notes=place_notes(markdown(p["body"])), photos=photos, page_type="place_" + folder.replace("-", "_"),
                og_image=f"/photos/{share}.jpg", css_v=css_v)
    return pages, len(deals), len(drives), sum(len(v) for v in places.values())


def main():
    try:
        pages, n_deals, n_drives, n_places = build()
    except Exception as e:  # leave the old pages alone
        sys.exit(f"build_pages: {e}")
    for folder in GENERATED:
        shutil.rmtree(SITE / folder, ignore_errors=True)
    for rel, page in pages.items():
        out = SITE / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page, encoding="utf-8")
    print(f"Built {len(pages)} pages: 2 hubs ({n_deals} packages, {n_drives} drive-to deals) and {n_places} place pages")


if __name__ == "__main__":
    main()
