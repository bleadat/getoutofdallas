#!/usr/bin/env python3
"""Pull the newest deal board from the shared Google Drive folder into site/board.json.

The morning price checks (Claude scheduled tasks) save each run's board as
board-YYYY-MM-DD-HHMM-<sha8>.json in a Drive folder shared "Anyone with the link".
This script lists that folder, takes the newest file, checks that its sha256 starts
with <sha8>, validates the shape, and replaces site/board.json only when the new
board is valid and not older than the current one.

It never fails the workflow over a bad or missing file: it keeps the current board
and prints why, so the site always deploys.
"""
import datetime
import hashlib
import html
import json
import os
import pathlib
import re
import sys
import urllib.request

FOLDER_ID = os.environ.get("BOARD_FOLDER_ID", "").strip()
OUT = pathlib.Path(os.environ.get("BOARD_PATH", "site/board.json"))
NAME_RE = re.compile(r"^board-(\d{4}-\d{2}-\d{2}-\d{4})-([0-9a-f]{8})\.json$")
UA = "Mozilla/5.0 (compatible; GetoutofDallas-sync/1.0; +https://getoutofdallas.com)"


def note(message, problem=False):
    """Print a line, flag problems in the Actions UI, and add it to the run summary."""
    print(("::warning::" if problem else "") + message)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(("**Deal sync problem:** " if problem else "") + message + "\n")


def fetch(url, limit=5_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read(limit + 1)
    if len(data) > limit:
        raise ValueError("response too large")
    return data


def list_folder(folder_id):
    """Return [(name, file_id)] from Drive's public embedded folder view."""
    page = fetch("https://drive.google.com/embeddedfolderview?id=" + folder_id).decode("utf-8", "replace")
    files = []
    for block in re.split(r'<div class="flip-entry"', page)[1:]:
        fid = re.search(r'id="entry-([A-Za-z0-9_-]{10,})"', block)
        name = re.search(r'class="flip-entry-title">([^<]+)<', block)
        if fid and name:
            files.append((html.unescape(name.group(1)).strip(), fid.group(1)))
    return files


def valid(board):
    if not isinstance(board, dict) or board.get("version") != 1:
        return "unexpected format"
    deals, drives, meta = board.get("deals"), board.get("drives"), board.get("meta")
    if not isinstance(deals, list) or not isinstance(drives, list) or not isinstance(meta, dict):
        return "missing deals, drives or meta"
    if len(deals) + len(drives) == 0:
        return "empty board"
    for row in deals + drives:
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not isinstance(row.get("title"), str):
            return "a listing has no id or title"
    return None


def main():
    if not FOLDER_ID:
        note("BOARD_FOLDER_ID not set; keeping the current board", problem=True)
        return
    try:
        files = [(n, f) for n, f in list_folder(FOLDER_ID) if NAME_RE.match(n)]
    except Exception as exc:  # network or Drive hiccup
        note("Couldn't list the Drive folder (" + type(exc).__name__ + "); keeping the current board", problem=True)
        return
    if not files:
        note("No board files in the Drive folder yet; keeping the current board", problem=True)
        return
    name, file_id = max(files)
    sha8 = NAME_RE.match(name).group(2)
    try:
        raw = fetch("https://drive.google.com/uc?export=download&id=" + file_id)
    except Exception as exc:
        note("Couldn't download " + name + " (" + type(exc).__name__ + "); keeping the current board", problem=True)
        return
    if hashlib.sha256(raw).hexdigest()[:8] != sha8:
        note(name + " doesn't match its checksum; keeping the current board", problem=True)
        return
    try:
        board = json.loads(raw)
    except ValueError:
        note(name + " isn't valid JSON; keeping the current board", problem=True)
        return
    problem = valid(board)
    if problem:
        note(name + ": " + problem + "; keeping the current board", problem=True)
        return
    if OUT.exists():
        try:
            current = json.loads(OUT.read_bytes())
            new_at = datetime.datetime.fromisoformat(str(board.get("generatedAt")))
            old_at = datetime.datetime.fromisoformat(str(current.get("generatedAt")))
            if new_at < old_at:
                note(name + " is older than the published board; keeping the current board", problem=True)
                return
        except (ValueError, TypeError):
            pass
        if OUT.read_bytes() == raw:
            note("Board unchanged (" + name + ")")
            return
    OUT.write_bytes(raw)
    note("Updated board from " + name + ": " + str(len(board["deals"])) + " packages, " + str(len(board["drives"])) + " drive-to deals")


if __name__ == "__main__":
    sys.exit(main())
