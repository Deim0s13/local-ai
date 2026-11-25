"""Docs MCP server: RAG over your own documents, fully local.

Index:   ~/.local-ai/rag/index.sqlite   (outside the repo)
Embeds:  sentence-transformers MiniLM (all-MiniLM-L6-v2 by default), on-device

CLI (used by scripts/rag.sh):
  python docs_server.py add <folder>...   index folders (re-run to refresh)
  python docs_server.py sync              refresh every indexed folder
  python docs_server.py remove <folder>   drop a folder from the index
  python docs_server.py status            folders, files and chunk counts
  python docs_server.py find <name>       is a file indexed, and how many passages
  python docs_server.py                   run as an MCP server (stdio)

Tools: search_docs(query, k), list_sources()
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

import numpy as np

HOME = Path(os.environ.get("LOCAL_AI_HOME", Path.home() / ".local-ai")).expanduser()
DB_PATH = HOME / "rag" / "index.sqlite"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
TEXT_EXT = {".md", ".markdown", ".txt", ".rst", ".adoc", ".yaml", ".yml", ".json", ".csv"}
DOC_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm"}
TEXTUTIL_EXT = {".rtf", ".rtfd", ".doc", ".odt", ".webarchive"}  # macOS textutil
APPLE_EXT = {".pages", ".numbers", ".key"}  # exported via the Apple app (osascript)
SUPPORTED = TEXT_EXT | DOC_EXT | TEXTUTIL_EXT | APPLE_EXT
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", ".cache"}
CHUNK_CHARS, OVERLAP = 1200, 150
MAX_FILE_BYTES = 25 * 1024 * 1024

_model = None


def embed(texts: list[str]) -> np.ndarray:
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer

        _model = SentenceTransformer(EMBED_MODEL, device="cpu")
    v = _model.encode(texts, batch_size=32, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(v, dtype=np.float32)


def db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS roots(path TEXT PRIMARY KEY);
        CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, root TEXT, mtime REAL);
        CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY, path TEXT, idx INTEGER, text TEXT, vec BLOB);
        CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
        """
    )
    return con


def _run(cmd: list[str], timeout: int = 180) -> None:
    import subprocess

    subprocess.run(cmd, check=True, capture_output=True, timeout=timeout)


def _apple_export(p: Path) -> str:
    """Export a Pages/Numbers/Keynote file with its own app, then read the export."""
    import tempfile
    import zipfile

    ext = p.suffix.lower()
    # 1) Older documents carry a full preview PDF inside the bundle.
    try:
        if p.is_file() and zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                if "QuickLook/Preview.pdf" in z.namelist():
                    with tempfile.TemporaryDirectory() as td:
                        out = Path(td) / "preview.pdf"
                        out.write_bytes(z.read("QuickLook/Preview.pdf"))
                        return _markitdown(out)
        elif (p / "QuickLook" / "Preview.pdf").exists():
            return _markitdown(p / "QuickLook" / "Preview.pdf")
    except Exception:
        pass
    # 2) Ask the app to export a copy (needs Pages/Numbers/Keynote installed).
    app, fmt, suffix = {
        ".pages": ("Pages", "unformatted text", ".txt"),
        ".numbers": ("Numbers", "Microsoft Excel", ".xlsx"),
        ".key": ("Keynote", "PDF", ".pdf"),
    }[ext]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / f"export{suffix}"
        script = (
            f'tell application "{app}"\n'
            f'  set d to open (POSIX file "{p}")\n'
            f'  export d to (POSIX file "{out}") as {fmt}\n'
            f"  close d saving no\n"
            f"end tell"
        )
        _run(["/usr/bin/osascript", "-e", script])
        return out.read_text(errors="ignore") if suffix == ".txt" else _markitdown(out)


def _markitdown(p: Path) -> str:
    from markitdown import MarkItDown

    return MarkItDown().convert(str(p)).text_content or ""


def read_text(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in TEXT_EXT:
        return p.read_text(errors="ignore")
    if ext in APPLE_EXT:
        return _apple_export(p)
    if ext in TEXTUTIL_EXT:
        import subprocess

        r = subprocess.run(
            ["/usr/bin/textutil", "-convert", "txt", "-stdout", str(p)],
            check=True, capture_output=True, timeout=120,
        )
        return r.stdout.decode(errors="ignore")
    return _markitdown(p)


def chunk(text: str) -> list[str]:
    paras = [x.strip() for x in text.replace("\r", "").split("\n\n") if x.strip()]
    out, cur = [], ""
    for para in paras:
        while len(para) > CHUNK_CHARS:
            if cur:
                out.append(cur)
                cur = ""
            out.append(para[:CHUNK_CHARS])
            para = para[CHUNK_CHARS - OVERLAP :]
        if len(cur) + len(para) + 2 > CHUNK_CHARS and cur:
            out.append(cur)
            cur = cur[-OVERLAP:] + "\n\n" + para
        else:
            cur = f"{cur}\n\n{para}" if cur else para
    if cur:
        out.append(cur)
    return out


def iter_files(root: Path, unsupported=None):
    """Yield indexable files. Apple package documents (folders) count as files.
    Unsupported extensions are tallied in the `unsupported` Counter if given."""
    for dirpath, dirnames, filenames in os.walk(root):
        keep = []
        for d in dirnames:
            if d in SKIP_DIRS or d.startswith("."):
                continue
            if Path(d).suffix.lower() in APPLE_EXT | {".rtfd"}:
                yield Path(dirpath) / d  # package document, not a folder to walk
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in filenames:
            if name.startswith("."):
                continue
            p = Path(dirpath) / name
            if p.suffix.lower() in SUPPORTED:
                yield p
            elif unsupported is not None:
                unsupported[p.suffix.lower() or "(no extension)"] += 1


def index_root(con: sqlite3.Connection, root: Path, report: dict | None = None) -> tuple[int, int, int]:
    from collections import Counter

    root = root.expanduser().resolve()
    con.execute("INSERT OR IGNORE INTO roots VALUES (?)", (str(root),))
    seen, added, skipped = set(), 0, 0
    unsupported, failed = Counter(), []
    known = dict(con.execute("SELECT path, mtime FROM files WHERE root = ?", (str(root),)).fetchall())
    for p in iter_files(root, unsupported):
        sp = str(p)
        seen.add(sp)
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_size > MAX_FILE_BYTES or known.get(sp) == st.st_mtime:
            continue
        try:
            pieces = chunk(read_text(p))
        except Exception as e:  # unreadable file: note it and move on
            failed.append((sp, f"{type(e).__name__}: {str(e)[:120]}"))
            skipped += 1
            continue
        con.execute("DELETE FROM chunks WHERE path = ?", (sp,))
        if pieces:
            vecs = embed(pieces)
            con.executemany(
                "INSERT INTO chunks(path, idx, text, vec) VALUES (?,?,?,?)",
                [(sp, i, t, v.tobytes()) for i, (t, v) in enumerate(zip(pieces, vecs))],
            )
        con.execute("INSERT OR REPLACE INTO files VALUES (?,?,?)", (sp, str(root), st.st_mtime))
        added += 1
        con.commit()
    gone = [p for p in known if p not in seen]
    for p in gone:
        con.execute("DELETE FROM chunks WHERE path = ?", (p,))
        con.execute("DELETE FROM files WHERE path = ?", (p,))
    con.commit()
    if report is not None:
        report["unsupported"] = unsupported
        report["failed"] = failed
    return added, len(gone), skipped


def print_report(root, added, gone, skipped, report):
    print(f"{root}: {added} files (re)indexed, {gone} removed, {skipped} failed")
    if report.get("failed"):
        print("  could not read:")
        for path, err in report["failed"][:20]:
            print(f"    {path}  ({err})")
        if len(report["failed"]) > 20:
            print(f"    ...and {len(report['failed']) - 20} more")
    if report.get("unsupported"):
        top = ", ".join(f"{ext} x{n}" for ext, n in report["unsupported"].most_common(12))
        print(f"  not indexed (unsupported types): {top}")


# ---------- search ----------
_cache: dict = {"mtime": None, "ids": None, "mat": None}


def _load():
    m = DB_PATH.stat().st_mtime if DB_PATH.exists() else None
    if m != _cache["mtime"]:
        con = db()
        rows = con.execute("SELECT id, vec FROM chunks").fetchall()
        con.close()
        _cache["ids"] = np.array([r[0] for r in rows], dtype=np.int64)
        _cache["mat"] = (
            np.frombuffer(b"".join(r[1] for r in rows), dtype=np.float32).reshape(len(rows), -1)
            if rows
            else np.zeros((0, 384), dtype=np.float32)
        )
        _cache["mtime"] = m
    return _cache["ids"], _cache["mat"]


def search(query: str, k: int = 6) -> list[dict]:
    ids, mat = _load()
    if len(ids) == 0:
        return []
    scores = mat @ embed([query])[0]
    top = np.argsort(-scores)[: max(1, min(k, 20))]
    con = db()
    out = []
    for i in top:
        path, idx, text = con.execute(
            "SELECT path, idx, text FROM chunks WHERE id = ?", (int(ids[i]),)
        ).fetchone()
        out.append({"source": path, "chunk": idx, "score": round(float(scores[i]), 3), "text": text})
    con.close()
    return out


def status() -> dict:
    con = db()
    roots = [r[0] for r in con.execute("SELECT path FROM roots ORDER BY path")]
    res = {
        "folders": [
            {
                "folder": r,
                "files": con.execute("SELECT COUNT(*) FROM files WHERE root = ?", (r,)).fetchone()[0],
            }
            for r in roots
        ],
        "chunks": con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0],
        "index": str(DB_PATH),
    }
    con.close()
    return res


def serve():
    from mcp.server import MCPServer

    server = MCPServer(
        name="docs",
        log_level="WARNING",
        instructions=(
            "Use search_docs to answer from the user's own documents. "
            "Quote the source path of every passage you rely on."
        ),
    )

    @server.tool()
    def search_docs(query: str, k: int = 6) -> list[dict]:
        """Search the user's indexed documents by meaning.

        Args:
            query: what to look for, in natural language.
            k: number of passages to return (1-20).
        """
        return search(query, k)

    @server.tool()
    def list_sources() -> dict:
        """List the indexed folders with file counts, and the total number of passages."""
        return status()

    server.run("stdio")


def main(argv: list[str]) -> int:
    if not argv:
        serve()
        return 0
    cmd, args = argv[0], argv[1:]
    con = db()
    if cmd == "add":
        if not args:
            print("usage: add <folder>...")
            return 2
        for a in args:
            p = Path(a).expanduser()
            if not p.is_dir():
                print(f"not a folder: {a}")
                continue
            rep: dict = {}
            added, gone, skipped = index_root(con, p, rep)
            print_report(p, added, gone, skipped, rep)
    elif cmd == "sync":
        for (r,) in con.execute("SELECT path FROM roots").fetchall():
            if Path(r).is_dir():
                rep: dict = {}
                added, gone, skipped = index_root(con, Path(r), rep)
                print_report(r, added, gone, skipped, rep)
            else:
                print(f"{r}: folder missing, left in index")
    elif cmd == "remove":
        for a in args:
            r = str(Path(a).expanduser().resolve())
            paths = [x[0] for x in con.execute("SELECT path FROM files WHERE root = ?", (r,))]
            con.executemany("DELETE FROM chunks WHERE path = ?", [(x,) for x in paths])
            con.execute("DELETE FROM files WHERE root = ?", (r,))
            con.execute("DELETE FROM roots WHERE path = ?", (r,))
            con.commit()
            print(f"removed {r} ({len(paths)} files)")
    elif cmd == "status":
        import json

        print(json.dumps(status(), indent=2))
    elif cmd == "find":
        pat = f"%{' '.join(args)}%"
        rows = con.execute(
            "SELECT f.path, COUNT(c.id) FROM files f LEFT JOIN chunks c ON c.path = f.path "
            "WHERE f.path LIKE ? GROUP BY f.path", (pat,)).fetchall()
        for path, n in rows:
            print(f"{n:5d} passages  {path}")
        if not rows:
            print("no indexed file matches that name")
    elif cmd == "search":
        for hit in search(" ".join(args)):
            print(f"{hit['score']:.3f}  {hit['source']}#{hit['chunk']}\n    {hit['text'][:160]!r}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
