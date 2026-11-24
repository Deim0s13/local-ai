"""Docs MCP server: RAG over your own documents, fully local.

Index:   ~/.local-ai/rag/index.sqlite   (outside the repo)
Embeds:  sentence-transformers MiniLM (all-MiniLM-L6-v2 by default), on-device

CLI (used by scripts/rag.sh):
  python docs_server.py add <folder>...   index folders (re-run to refresh)
  python docs_server.py sync              refresh every indexed folder
  python docs_server.py remove <folder>   drop a folder from the index
  python docs_server.py status            folders, files and chunk counts
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


def read_text(p: Path) -> str:
    ext = p.suffix.lower()
    if ext in TEXT_EXT:
        return p.read_text(errors="ignore")
    from markitdown import MarkItDown

    return MarkItDown().convert(str(p)).text_content or ""


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


def iter_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            p = Path(dirpath) / name
            if p.suffix.lower() in TEXT_EXT | DOC_EXT and not name.startswith("."):
                yield p


def index_root(con: sqlite3.Connection, root: Path) -> tuple[int, int, int]:
    root = root.expanduser().resolve()
    con.execute("INSERT OR IGNORE INTO roots VALUES (?)", (str(root),))
    seen, added, skipped = set(), 0, 0
    known = dict(con.execute("SELECT path, mtime FROM files WHERE root = ?", (str(root),)).fetchall())
    for p in iter_files(root):
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
            print(f"  skip {p}: {e}", file=sys.stderr)
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
    return added, len(gone), skipped


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
            added, gone, skipped = index_root(con, p)
            print(f"{p}: {added} files (re)indexed, {gone} removed, {skipped} skipped")
    elif cmd == "sync":
        for (r,) in con.execute("SELECT path FROM roots").fetchall():
            if Path(r).is_dir():
                added, gone, skipped = index_root(con, Path(r))
                print(f"{r}: {added} files (re)indexed, {gone} removed, {skipped} skipped")
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
    elif cmd == "search":
        for hit in search(" ".join(args)):
            print(f"{hit['score']:.3f}  {hit['source']}#{hit['chunk']}\n    {hit['text'][:160]!r}")
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
