#!/usr/bin/env python3
"""zotero-cli: manage a local Zotero library from the command line.

SQLite direct read/write. No Zotero process needed for reads; writes require
Zotero to be closed (SQLite lock safety).

Commands:
  status              Library health: db readable, Zotero running, counts
  search <query>      Search items by title (dedup pre-check)
  collections         List collections
  import pdf <files>  Import PDFs: title -> dedup -> metadata -> storage
  import arxiv <id>   Fetch + import an arXiv paper (API-verified title)
  meta-check          Scan items missing date/creators/url
  export-bibtex       Export library as BibTeX
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

def load_dotenv(path: str | None = None) -> None:
    """Load KEY=VALUE pairs from .env (next to this script) into os.environ."""
    dotenv = path or str(Path(__file__).resolve().parent / ".env")
    if not os.path.exists(dotenv):
        return
    for line in open(dotenv, encoding="utf-8"):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


load_dotenv()

DEFAULT_DB = os.environ.get("ZOTERO_DB_PATH", str(Path.home() / "Zotero" / "zotero.sqlite"))
DEFAULT_STORAGE = os.environ.get("ZOTERO_STORAGE", str(Path.home() / "Zotero" / "storage"))
PROXY = os.environ.get("HTTPS_PROXY", "http://127.0.0.1:7897")

FIELD_IDS: dict[str, int] = {}
TYPE_IDS: dict[str, int] = {}
CREATOR_AUTHOR: int | None = None
MAIN_LIBRARY = 1


# ---------------------------------------------------------------- db helpers

def connect(db: str):
    import sqlite3
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def field_id(conn, name: str) -> int:
    if name not in FIELD_IDS:
        row = conn.execute("SELECT fieldID FROM fields WHERE fieldName=?", (name,)).fetchone()
        if row is None:
            raise RuntimeError(f"unknown field {name}")
        FIELD_IDS[name] = row["fieldID"]
    return FIELD_IDS[name]


def type_id(conn, name: str) -> int:
    if name not in TYPE_IDS:
        row = conn.execute("SELECT itemTypeID FROM itemTypes WHERE typeName=?", (name,)).fetchone()
        if row is None:
            raise RuntimeError(f"unknown item type {name}")
        TYPE_IDS[name] = row["itemTypeID"]
    return TYPE_IDS[name]


def get_value(conn, item_id: int, fname: str) -> str | None:
    row = conn.execute(
        """SELECT iv.value FROM itemData id
           JOIN itemDataValues iv ON iv.valueID = id.valueID
           WHERE id.itemID=? AND id.fieldID=?""",
        (item_id, field_id(conn, fname)),
    ).fetchone()
    return row["value"] if row else None


def set_value(conn, item_id: int, fname: str, value: str) -> None:
    cur = conn.execute("INSERT INTO itemDataValues (value) VALUES (?)", (value,))
    conn.execute(
        "INSERT OR REPLACE INTO itemData (itemID, fieldID, valueID) VALUES (?,?,?)",
        (item_id, field_id(conn, fname), cur.lastrowid),
    )


def random_key() -> str:
    import random
    import string
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=8))


# ---------------------------------------------------------------- zotero run

def zotero_running() -> bool:
    """Check whether Zotero Desktop is running. Platform-aware:
    Windows → tasklist.exe (GBK output); macOS/Linux → pgrep.
    """
    system = platform.system()
    try:
        if system == "Windows":
            out = subprocess.run(
                ["tasklist.exe", "/FO", "CSV", "/NH"],
                capture_output=True, timeout=15,
            ).stdout
            return b"zotero.exe" in out.lower() or "zotero.exe" in out.decode("gbk", errors="replace").lower()
        # macOS / Linux: pgrep, matching only the desktop app
        names = ["Zotero", "zotero"] if system == "Darwin" else ["zotero"]
        for name in names:
            r = subprocess.run(
                ["pgrep", "-x", name], capture_output=True, timeout=10,
            )
            if r.returncode == 0:
                return True
        return False
    except Exception:
        return False


# ---------------------------------------------------------------- pdf title

def _run_odl(pdf: Path) -> str:
    """Extract first-page markdown via opendataloader-pdf (global CLI)."""
    r = subprocess.run(
        ["opendataloader-pdf", str(pdf), "--to-stdout", "-f", "markdown",
         "--pages", "1", "--threads", "1"],
        capture_output=True, timeout=120,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.decode("utf-8", errors="replace")[:300])
    return r.stdout.decode("utf-8", errors="replace")


def pdf_title(pdf: Path) -> tuple[str, str | None]:
    """Extract (title, arxiv_id) from a PDF.

    Prefers opendataloader-pdf: structured markdown where the first
    `# ` heading is the title and a `## arXiv:...` line carries the ID.
    Falls back to pdftotext first-page text when odl is unavailable.
    """
    try:
        md = _run_odl(pdf)
        title = ""
        arxiv_id = None
        for line in md.splitlines():
            line = line.strip()
            m = re.match(r"^#{1,3}\s+arXiv:(\d{4}\.\d{4,5}(?:v\d+)?)", line)
            if m and not arxiv_id:
                arxiv_id = m.group(1)
                continue
            if line.startswith("# ") and not title:
                title = line[2:].strip()
            if title:
                break
        if title:
            return title[:200], arxiv_id
    except Exception:
        pass  # fall through to pdftotext

    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "p1.txt"
        try:
            subprocess.run(
                ["pdftotext", "-f", "1", "-l", "1", str(pdf), str(out)],
                capture_output=True, check=True, timeout=60,
            )
            text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
        except Exception:
            return "", None
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return "", None
    title = " ".join(lines[:3]).strip()[:200]
    m = ARXIV_ID_RE.search(" ".join(lines[:5]))
    return title, (m.group(1) if m else None)


# ---------------------------------------------------------------- dedup

def dedup_query(title: str) -> str:
    """Build a 2-3 word LIKE query from the title. Short words get dropped."""
    words = [w for w in re.split(r"\W+", title) if len(w) >= 5]
    if len(words) < 3:
        words = [w for w in re.split(r"\W+", title) if len(w) >= 3]
    return "%" + "%".join(words[:3]) + "%" if words else "%" + title[:20] + "%"


def find_existing(conn, title: str) -> list[dict]:
    """Dedup by distinctive title phrase; verify exact item + pdf attachment."""
    q = dedup_query(title)
    rows = conn.execute(
        """SELECT DISTINCT i.itemID, i.key, iv.value AS title
           FROM items i
           JOIN itemData id ON id.itemID = i.itemID
           JOIN itemDataValues iv ON iv.valueID = id.valueID
           WHERE i.libraryID=? AND id.fieldID=? AND iv.value LIKE ? COLLATE NOCASE""",
        (MAIN_LIBRARY, field_id(conn, "title"), q),
    ).fetchall()
    hits = []
    for r in rows:
        exact = r["title"].strip().lower() == title.strip().lower()
        has_pdf = conn.execute(
            "SELECT COUNT(*) c FROM itemAttachments WHERE parentItemID=? AND contentType='application/pdf'",
            (r["itemID"],),
        ).fetchone()["c"]
        hits.append({"key": r["key"], "title": r["title"], "exact": exact, "pdf": has_pdf})
    return hits


# ---------------------------------------------------------------- metadata

ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5}(?:v\d+)?)")


def _direct_opener():
    """arXiv must be accessed DIRECT (proxy rules truncate it, SSL EOF)."""
    proxy_handler = urllib.request.ProxyHandler({})
    return urllib.request.build_opener(proxy_handler)


def arxiv_metadata(arxiv_id: str) -> dict:
    """Verify title/authors via arXiv API. Never trust remembered IDs."""
    url = f"https://export.arxiv.org/api/query?id_list={arxiv_id}"
    try:
        with _direct_opener().open(url, timeout=30) as resp:  # direct, no proxy
            xml = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return {}
    m_title = re.search(r"<entry>.*?<title>(.*?)</title>", xml, re.S)
    authors = re.findall(r"<name>(.*?)</name>", xml)
    m_pdf = re.search(r'<link[^>]*title="pdf"[^>]*href="([^"]+)"', xml)
    title = re.sub(r"\s+", " ", m_title.group(1)).strip() if m_title else ""
    return {
        "title": title,
        "authors": [a.strip() for a in authors] if authors else [],
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": m_pdf.group(1) if m_pdf else f"https://arxiv.org/pdf/{arxiv_id}",
        "extra": f"arXiv:{arxiv_id}",
    }


def find_arxiv_id_in_pdf(pdf: Path) -> str | None:
    """Look for an arXiv ID in filename or first-page text."""
    m = ARXIV_ID_RE.search(pdf.name)
    if m:
        return m.group(1)
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "p1.txt"
        subprocess.run(
            ["pdftotext", "-f", "1", "-l", "2", str(pdf), str(out)],
            capture_output=True, check=True, timeout=60,
        )
        text = out.read_text(encoding="utf-8", errors="replace") if out.exists() else ""
    m = ARXIV_ID_RE.search(text)
    return m.group(1) if m else None


# ---------------------------------------------------------------- import

def validate_pdf(pdf: Path) -> str | None:
    """Return error message if the file is not a real PDF."""
    if not pdf.exists():
        return f"not found: {pdf}"
    size = pdf.stat().st_size
    if size < 50 * 1024:
        return f"too small ({size} B) — likely error page: {pdf.name}"
    with open(pdf, "rb") as f:
        head = f.read(8)
    if not head.startswith(b"%PDF-"):
        return f"not a PDF (magic {head[:5]!r}): {pdf.name}"
    return None


def import_item(conn, *, title: str, authors: list[str], date: str | None,
                url: str | None, extra: str | None, pdf_path: Path | None,
                collection: str | None, storage_dir: Path | None = None) -> dict:
    """Insert one item (+attachment +collection) into the DB."""
    storage_root = storage_dir or Path(DEFAULT_STORAGE)
    now = int(__import__("time").time() * 1000)
    key = random_key()
    cur = conn.execute(
        """INSERT INTO items (key, libraryID, itemTypeID, dateAdded, dateModified, clientDateModified)
           VALUES (?,?,?,?,?,?)""",
        (key, MAIN_LIBRARY, type_id(conn, "journalArticle"), now, now, now),
    )
    item_id = cur.lastrowid
    set_value(conn, item_id, "title", title)
    if date:
        set_value(conn, item_id, "date", date)
    if url:
        set_value(conn, item_id, "url", url)
    if extra:
        set_value(conn, item_id, "extra", extra)

    author_type = get_author_type_id(conn)
    for i, name in enumerate(authors):
        parts = name.rsplit(" ", 1)  # correct split: last = parts[1]
        last, first = (parts[1], parts[0]) if len(parts) == 2 else (parts[0], "")
        c = conn.execute(
            "SELECT creatorID FROM creators WHERE firstName=? AND lastName=? AND fieldMode=0",
            (first, last),
        ).fetchone()
        if c is None:
            c2 = conn.execute(
                "INSERT INTO creators (firstName, lastName, fieldMode) VALUES (?,?,0)",
                (first, last),
            )
            creator_id = c2.lastrowid
        else:
            creator_id = c["creatorID"]
        conn.execute(
            "INSERT INTO itemCreators (itemID, creatorID, creatorTypeID, orderIndex) VALUES (?,?,?,?)",
            (item_id, creator_id, author_type, i),
        )

    attach_key = None
    if pdf_path is not None:
        # attachment is itself an items row; storage dir name == attachment key
        attach_key = random_key()
        cur = conn.execute(
            """INSERT INTO items (key, libraryID, itemTypeID, dateAdded, dateModified, clientDateModified)
               VALUES (?,?,?,?,?,?)""",
            (attach_key, MAIN_LIBRARY, type_id(conn, "attachment"), now, now, now),
        )
        attach_item_id = cur.lastrowid
        conn.execute(
            """INSERT INTO itemAttachments
               (itemID, parentItemID, linkMode, contentType, charsetID, path)
               VALUES (?,?,?,?,?,?)""",
            (attach_item_id, item_id, 1, "application/pdf", 1, f"storage:{pdf_path.name}"),
        )
        # copy to storage dir named after the attachment key
        storage_dir_path = storage_root / attach_key
        storage_dir_path.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pdf_path, storage_dir_path / pdf_path.name)
        # verify write landed (some filesystems report stale stat; retry once)
        for _ in range(2):
            dest = storage_dir_path / pdf_path.name
            if dest.exists() and dest.stat().st_size == pdf_path.stat().st_size:
                break
            time.sleep(0.5)
            shutil.copy2(pdf_path, storage_dir_path / pdf_path.name)

    if collection:
        col = conn.execute(
            "SELECT collectionID FROM collections WHERE collectionName=? AND libraryID=?",
            (collection, MAIN_LIBRARY),
        ).fetchone()
        if col is None:
            c = conn.execute(
                "INSERT INTO collections (collectionName, libraryID, parentCollectionID, key) "
                "VALUES (?,?,?,?)",
                (collection, MAIN_LIBRARY, None, random_key()),
            )
            col_id = c.lastrowid
        else:
            col_id = col["collectionID"]
        conn.execute("INSERT OR IGNORE INTO collectionItems (collectionID, itemID) VALUES (?,?)",
                     (col_id, item_id))

    return {"key": key, "item_id": item_id, "attachment_key": attach_key, "title": title}


def get_author_type_id(conn) -> int:
    global CREATOR_AUTHOR
    if CREATOR_AUTHOR is None:
        row = conn.execute("SELECT creatorTypeID FROM creatorTypes WHERE creatorType=?", ("author",)).fetchone()
        CREATOR_AUTHOR = row["creatorTypeID"] if row else 8
    return CREATOR_AUTHOR


# ---------------------------------------------------------------- commands

def cmd_status(args) -> None:
    import sqlite3
    print(f"db: {args.db}")
    print(f"db exists: {os.path.exists(args.db)}")
    print(f"zotero running: {zotero_running()}")
    if not os.path.exists(args.db):
        return
    conn = connect(args.db)
    total = conn.execute("SELECT COUNT(*) c FROM items WHERE libraryID=?", (MAIN_LIBRARY,)).fetchone()["c"]
    top = conn.execute(
        "SELECT COUNT(*) c FROM items WHERE libraryID=? AND itemTypeID NOT IN (14,1)",
        (MAIN_LIBRARY,),
    ).fetchone()["c"]
    pdfs = conn.execute(
        """SELECT COUNT(*) c FROM itemAttachments
           WHERE contentType='application/pdf' AND parentItemID IN
           (SELECT itemID FROM items WHERE libraryID=?)""",
        (MAIN_LIBRARY,),
    ).fetchone()["c"]
    cols = conn.execute("SELECT COUNT(*) c FROM collections WHERE libraryID=?", (MAIN_LIBRARY,)).fetchone()["c"]
    print(f"items (all incl. children): {total}")
    print(f"top-level items: {top}")
    print(f"pdf attachments: {pdfs}")
    print(f"collections: {cols}")
    conn.close()


def cmd_search(args) -> None:
    conn = connect(args.db)
    q = dedup_query(args.query)
    rows = conn.execute(
        """SELECT i.key, iv.value AS title,
                  (SELECT iv2.value FROM itemData id2
                   JOIN itemDataValues iv2 ON iv2.valueID=id2.valueID
                   WHERE id2.itemID=i.itemID AND id2.fieldID=(SELECT fieldID FROM fields WHERE fieldName='date'))
                  AS year
           FROM items i
           JOIN itemData id ON id.itemID=i.itemID
           JOIN itemDataValues iv ON iv.valueID=id.valueID
           WHERE i.libraryID=? AND id.fieldID=(SELECT fieldID FROM fields WHERE fieldName='title')
             AND iv.value LIKE ? COLLATE NOCASE
           ORDER BY year DESC LIMIT 20""",
        (MAIN_LIBRARY, q),
    ).fetchall()
    for r in rows:
        print(f"{r['key']}  {r['year'] or '????'}  {r['title']}")
    conn.close()


def cmd_collections(args) -> None:
    conn = connect(args.db)
    rows = conn.execute(
        "SELECT collectionName, key FROM collections WHERE libraryID=? ORDER BY collectionName",
        (MAIN_LIBRARY,),
    ).fetchall()
    for r in rows:
        print(f"{r['collectionName']}  ({r['key']})")
    conn.close()


def cmd_import_pdf(args) -> None:
    if zotero_running():
        print("ERROR: Zotero is running. Close it first (SQLite write lock).", file=sys.stderr)
        sys.exit(1)
    conn = connect(args.db)
    for pdf in args.files:
        pdf = Path(pdf)
        err = validate_pdf(pdf)
        if err:
            print(f"SKIP {pdf.name}: {err}")
            continue
        title = pdf_first_page_title(pdf)
        if not title:
            print(f"SKIP {pdf.name}: no text layer (scanned PDF?)")
            continue
        existing = find_existing(conn, title)
        if any(h["exact"] and h["pdf"] for h in existing):
            print(f"SKIP {pdf.name}: already in library ({existing[0]['key']})")
            continue
        arxiv_id = find_arxiv_id_in_pdf(pdf)
        meta = arxiv_metadata(arxiv_id) if arxiv_id else {}
        if meta.get("title"):
            title = meta["title"]  # API title beats first-page guess
        result = import_item(
            conn, title=title, authors=meta.get("authors", []),
            date=args.date, url=meta.get("url"), extra=meta.get("extra"),
            pdf_path=pdf, collection=args.collection,
            storage_dir=Path(args.storage) if args.storage else None,
        )
        print(f"IMPORTED {pdf.name} -> {result['key']} | {title[:80]}")
    conn.commit()
    conn.close()


def cmd_import_arxiv(args) -> None:
    if zotero_running():
        print("ERROR: Zotero is running. Close it first (SQLite write lock).", file=sys.stderr)
        sys.exit(1)
    meta = arxiv_metadata(args.id)
    if not meta.get("title"):
        print(f"ERROR: arXiv API returned nothing for {args.id} (network? bad id?)", file=sys.stderr)
        sys.exit(1)
    conn = connect(args.db)
    existing = find_existing(conn, meta["title"])
    if any(h["exact"] and h["pdf"] for h in existing):
        print(f"SKIP: already in library ({existing[0]['key']})")
        conn.close()
        return
    # download pdf
    pdf_path = Path(tempfile.mkdtemp()) / f"arxiv_{args.id.replace('.', '_')}.pdf"
    req = urllib.request.Request(meta["pdf_url"], headers={"User-Agent": "curl/8"})
    try:
        with _direct_opener().open(req, timeout=120) as resp, open(pdf_path, "wb") as f:
            shutil.copyfileobj(resp, f)
    except Exception as e:
        print(f"ERROR: pdf download failed: {e}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    err = validate_pdf(pdf_path)
    if err:
        print(f"ERROR: {err}", file=sys.stderr)
        conn.close()
        sys.exit(1)
    result = import_item(
        conn, title=meta["title"], authors=meta["authors"],
        date=args.date or meta.get("date"), url=meta["url"],
        extra=meta.get("extra"), pdf_path=pdf_path, collection=args.collection,
        storage_dir=Path(args.storage) if args.storage else None,
    )
    print(f"IMPORTED arXiv:{args.id} -> {result['key']} | {meta['title'][:80]}")
    conn.commit()
    conn.close()


def cmd_meta_check(args) -> None:
    conn = connect(args.db)
    rows = conn.execute(
        """SELECT i.key, iv.value AS title FROM items i
           JOIN itemData id ON id.itemID=i.itemID
           JOIN itemDataValues iv ON iv.valueID=id.valueID
           WHERE i.libraryID=? AND id.fieldID=(SELECT fieldID FROM fields WHERE fieldName='title')
             AND i.itemTypeID NOT IN (14,1)
             AND NOT EXISTS (SELECT 1 FROM itemData id2 WHERE id2.itemID=i.itemID
                             AND id2.fieldID=(SELECT fieldID FROM fields WHERE fieldName='date'))
           ORDER BY iv.value LIMIT 50""",
        (MAIN_LIBRARY,),
    ).fetchall()
    print(f"items missing date: {len(rows)}")
    for r in rows:
        print(f"{r['key']}  {r['title'][:90]}")
    conn.close()


def cmd_export_bibtex(args) -> None:
    conn = connect(args.db)
    rows = conn.execute(
        """SELECT i.key, iv.value AS title FROM items i
           JOIN itemData id ON id.itemID=i.itemID
           JOIN itemDataValues iv ON iv.valueID=id.valueID
           WHERE i.libraryID=? AND id.fieldID=(SELECT fieldID FROM fields WHERE fieldName='title')
             AND i.itemTypeID NOT IN (14,1)
           ORDER BY iv.value""",
        (MAIN_LIBRARY,),
    ).fetchall()
    lines = []
    for r in rows:
        key = r["key"]
        title = r["title"].replace("&", r"\\&").replace("%", r"\\%")
        lines.append(f"@misc{{{key},")
        lines.append(f"  title = {{{title}}},")
        lines.append("}")
        lines.append("")
    text = "\n".join(lines)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"wrote {len(rows)} entries -> {args.out}")
    else:
        print(text)
    conn.close()


# ---------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="zotero-cli", description=__doc__)
    p.add_argument("--db", default=DEFAULT_DB, help=f"zotero.sqlite path (default: {DEFAULT_DB})")
    p.add_argument("--storage", default=DEFAULT_STORAGE, help="storage dir")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="library health").set_defaults(func=cmd_status)
    s = sub.add_parser("search", help="search by title")
    s.add_argument("query")
    s.set_defaults(func=cmd_search)
    sub.add_parser("collections", help="list collections").set_defaults(func=cmd_collections)

    ip = sub.add_parser("import", help="import")
    ip_sub = ip.add_subparsers(dest="kind", required=True)
    pdf = ip_sub.add_parser("pdf", help="import PDF files")
    pdf.add_argument("files", nargs="+")
    pdf.add_argument("--collection", help="collection name")
    pdf.add_argument("--date", help="date override (YYYY-MM-DD)")
    pdf.set_defaults(func=cmd_import_pdf)
    arx = ip_sub.add_parser("arxiv", help="import arXiv paper by ID")
    arx.add_argument("id")
    arx.add_argument("--collection")
    arx.add_argument("--date")
    arx.set_defaults(func=cmd_import_arxiv)

    sub.add_parser("meta-check", help="scan items missing date").set_defaults(func=cmd_meta_check)
    e = sub.add_parser("export-bibtex", help="export as bibtex")
    e.add_argument("--out")
    e.set_defaults(func=cmd_export_bibtex)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.func(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
