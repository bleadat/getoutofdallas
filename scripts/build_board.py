#!/usr/bin/env python3
"""Build the public board.json for GetoutofDallas.com.

Input: an ArtifactData export folder (the `out_dir` layout):
    EXPORT/deals/<id>.json   fly + stay packages
    EXPORT/drives/<id>.json  drive-to deals
    EXPORT/meta/site.json    board metadata (blocked titles, cabinAllow, updatedOn, ...)
The `signups` collection is never read.

Output: OUT_FILE, pure-ASCII minified JSON with only the fields the page renders,
after applying the owner's removals (meta.blocked) and the cabin allow-list
(meta.cabinAllow), so neither list is published.

Prints one line:  name=board-YYYY-MM-DD-HHMM-<sha8>.json deals=N drives=N bytes=N
The <sha8> is the first 8 hex digits of the file's sha256. The GitHub Action
rejects any Drive file whose content doesn't match the sha8 in its name.

Usage: python3 build_board.py EXPORT_DIR OUT_FILE
"""
import datetime
import hashlib
import json
import pathlib
import re
import sys
from zoneinfo import ZoneInfo

DEAL_FIELDS = [
    "title", "kind", "destination", "boardLabel", "country", "region", "tripType", "band",
    "originCode", "destCode", "compareDest", "nights", "price", "priceMax", "priceNote",
    "includes", "perks", "caveat", "stars", "adultsOnly", "travelStart", "travelEnd",
    "fixedDates", "travelLabel", "bookBy", "provider", "url", "photoKey", "foundOn", "checkedOn",
]
DRIVE_FIELDS = [
    "title", "offer", "kind", "area", "areaKey", "destLabel", "location", "nearby",
    "driveMiles", "driveTime", "route", "price", "priceNote", "discount", "includes", "perks",
    "sleeps", "code", "minNights", "checkInDow", "travelStart", "travelEnd", "fixedDates",
    "travelLabel", "bookBy", "provider", "url", "caveat", "photoKey", "expediaHotelId",
    "compareSite", "compareQuery", "vrboRegionId", "foundOn", "checkedOn",
]
META_FIELDS = ["updatedOn", "updatedAt", "nextRefresh", "sources", "dealCount", "driveCount"]
CABIN_KIND = re.compile(r"cabin|cottage|chalet|lodge|vacation rental", re.I)
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,99}$")


def load_dir(path):
    rows = []
    for f in sorted(path.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and ID_RE.match(f.stem):
            rows.append((f.stem, data))
    return rows


def pick(doc_id, data, fields):
    out = {"id": doc_id}
    for key in fields:
        val = data.get(key)
        if val is None or val == "" or val == []:
            continue
        out[key] = val
    return out


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src = pathlib.Path(sys.argv[1])
    dest = pathlib.Path(sys.argv[2])

    meta_raw = {}
    meta_file = src / "meta" / "site.json"
    if meta_file.exists():
        meta_raw = json.loads(meta_file.read_text(encoding="utf-8"))

    blocked = [t.strip().lower() for t in meta_raw.get("blocked") or [] if isinstance(t, str) and len(t.strip()) >= 3]
    allow = meta_raw.get("cabinAllow")
    allow = set(x for x in allow if isinstance(x, str)) if isinstance(allow, list) else None

    def visible(data):
        title = str(data.get("title") or "").lower()
        return bool(title) and not any(b in title for b in blocked)

    def cabin_ok(doc_id, data):
        if allow is None:
            return True
        return doc_id in allow or not (data.get("areaKey") == "broken-bow" or CABIN_KIND.search(str(data.get("kind") or "")))

    deals = [pick(i, d, DEAL_FIELDS) for i, d in load_dir(src / "deals") if visible(d)]
    drives = [pick(i, d, DRIVE_FIELDS) for i, d in load_dir(src / "drives") if visible(d) and cabin_ok(i, d)]
    if not deals and not drives:
        sys.exit("No deals or drives found under " + str(src) + "; nothing written.")

    meta = {k: meta_raw[k] for k in META_FIELDS if k in meta_raw}
    now = datetime.datetime.now(ZoneInfo("America/Chicago"))
    board = {
        "version": 1,
        "generatedAt": now.isoformat(timespec="seconds"),
        "meta": meta,
        "deals": deals,
        "drives": drives,
    }
    raw = json.dumps(board, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")
    dest.write_bytes(raw)
    sha8 = hashlib.sha256(raw).hexdigest()[:8]
    name = "board-" + now.strftime("%Y-%m-%d-%H%M") + "-" + sha8 + ".json"
    print("name=" + name, "deals=" + str(len(deals)), "drives=" + str(len(drives)), "bytes=" + str(len(raw)))


if __name__ == "__main__":
    main()
