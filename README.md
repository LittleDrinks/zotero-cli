# zotero-cli

Manage a local Zotero library from the command line. SQLite direct read/write — no Zotero process needed for reads; writes require Zotero to be closed.

## Why

Zotero's own APIs are either read-only (local HTTP API) or need the desktop app running (connector). This tool talks to `zotero.sqlite` directly: search, dedup, import arXiv papers with API-verified metadata, and file PDFs into the right storage directory.

## Install

```bash
git clone https://github.com/LittleDrinks/zotero-cli.git
cd zotero-cli
# optional: symlink for PATH access
ln -s "$PWD/zotero.py" ~/.local/bin/zotero-cli
```

## Requirements

- Python 3.10+
- `pdftotext` (poppler-utils) for title extraction from PDFs
- Zotero closed when importing (SQLite write lock)

## Usage

```bash
# library health
zotero-cli status

# search (dedup pre-check)
zotero-cli search "attention is all you need"

# list collections
zotero-cli collections

# import a local PDF (auto title-extract, dedup, metadata)
zotero-cli import pdf paper.pdf --collection "AI4Science"

# import an arXiv paper (API-verified title, downloads PDF)
zotero-cli import arxiv 2608.04003 --collection "AI4Science"

# scan items missing date metadata
zotero-cli meta-check

# export library as BibTeX
zotero-cli export-bibtex --out references.bib
```

## How import works

1. `pdftotext` reads the first page — the real title, not the filename (filenames lie)
2. Dedup by a distinctive 2-3 word title phrase against the library
3. arXiv ID found in filename/page text → metadata verified via arXiv API (never trusts remembered IDs)
4. Item + attachment rows written to SQLite; storage dir named after the attachment key (Zotero convention)
5. Optional collection attach (created if missing)

## Configuration

| Env var | Default | Meaning |
|---|---|---|
| `ZOTERO_DB_PATH` | `/mnt/e/LittleDrinks/zotero/zotero.sqlite` | Path to zotero.sqlite |
| `ZOTERO_STORAGE` | `/mnt/e/LittleDrinks/zotero/storage` | Zotero storage dir |

Or pass `--db` / `--storage` per command.

## Safety

- Writes refuse to run while Zotero is open (`tasklist.exe` check)
- Imported files validated: `%PDF-` magic, size > 50KB (HTML error pages rejected)
- Existing items are skipped, never duplicated

## License

MIT
