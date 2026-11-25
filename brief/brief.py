"""Morning brief: news, summarised by your local chat model, plus brew items.

  python brief.py run [--force]   build today's brief now (--force rebuilds it)
  python brief.py auto            what launchd calls at 09:30 (skips if today's exists)

Writes, outside the repo:
  ~/.local-ai/briefs/<YYYY-MM-DD>.md         Markdown, indexed for search_docs
  ~/.local-ai/briefs/.web/<YYYY-MM-DD>.html  the page the notification opens
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import subprocess
import sys
import time
from email.utils import parsedate_to_datetime
from pathlib import Path
import warnings

# urllib3 warns about SOCKS when a proxy variable is set; it doesn't matter here
warnings.filterwarnings("ignore", message=".*SOCKS support.*")

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "servers"))
CONFIG = json.loads((REPO / "config" / "brief.json").read_text())
HOME = Path(os.environ.get("LOCAL_AI_HOME", Path.home() / ".local-ai")).expanduser()
BRIEFS = HOME / "briefs"
WEB = BRIEFS / ".web"  # hidden, so the docs index only picks up the Markdown
PROFILE = HOME / "profile" / "about-me.md"
BREW_SUMMARY = HOME / "logs" / "brew-summary.md"
SCOUT_LOCK = HOME / "evals" / ".scout.lock"
OMLX = Path.home() / ".omlx" / "bin" / "omlx"
OMLX_SETTINGS = Path.home() / ".omlx" / "settings.json"
OMLX_URL = "http://127.0.0.1:8000"
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://127.0.0.1:8888")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
      "(KHTML, like Gecko) Version/18.0 Safari/605.1.15")


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


# ---------------------------------------------------------------- model
def omlx_key() -> str:
    try:
        return json.loads(OMLX_SETTINGS.read_text())["auth"]["api_key"]
    except Exception:
        return os.environ.get("OMLX_API_KEY", "")


def auth() -> dict:
    k = omlx_key()
    return {"Authorization": f"Bearer {k}"} if k else {}


def chat_model() -> str:
    if CONFIG.get("model"):
        return CONFIG["model"]
    champ = json.loads((REPO / "config" / "scout.json").read_text())["champions"]["chat"]
    return champ.replace("/", "--")


def omlx_ready() -> bool:
    import httpx

    try:
        httpx.get(f"{OMLX_URL}/v1/models", headers=auth(), timeout=3).raise_for_status()
        return True
    except Exception:
        return False


def scout_running() -> bool:
    try:
        os.kill(int(SCOUT_LOCK.read_text() or 0), 0)
        return True
    except Exception:
        return False


def ensure_model(wait_s: int = 3600) -> bool:
    """oMLX up? Waits while the model scout has it stopped; starts it otherwise."""
    end = time.time() + wait_s
    started = False
    while time.time() < end:
        if omlx_ready():
            return True
        if scout_running():
            log("model scout is running; waiting for it to hand oMLX back")
            time.sleep(60)
            continue
        if not started:
            if OMLX.exists():
                subprocess.run([str(OMLX), "start"], capture_output=True)
            started = True
            end = min(end, time.time() + 180)
        time.sleep(5)
    return False


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).strip()


def ask_model(prompt: str, max_tokens: int = 1200) -> str:
    import httpx

    system = "You write a short, accurate morning news brief. Use only the facts you are given."
    if PROFILE.exists():
        system += ("\n\nThe reader (match their language and interests, keep it plain):\n"
                   + PROFILE.read_text()[:3000])
    r = httpx.post(f"{OMLX_URL}/v1/chat/completions", timeout=600,
                   headers=auth(),
                   json={"model": chat_model(), "temperature": 0.2, "max_tokens": max_tokens,
                         "chat_template_kwargs": {"enable_thinking": False},
                         "messages": [{"role": "system", "content": system},
                                      {"role": "user", "content": prompt}]})
    r.raise_for_status()
    return strip_think(r.json()["choices"][0]["message"]["content"])


# ---------------------------------------------------------------- news
def _when(entry) -> dt.datetime | None:
    for key in ("published_parsed", "updated_parsed"):
        t = entry.get(key)
        if t:
            return dt.datetime(*t[:6], tzinfo=dt.timezone.utc)
    for key in ("published", "updated"):
        try:
            return parsedate_to_datetime(entry[key])
        except Exception:
            pass
    return None


def _clean(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    return re.sub(r"\s+", " ", html.unescape(s)).strip()


def read_feed(url: str, since: dt.datetime) -> list[dict]:
    import feedparser
    import httpx

    try:
        r = httpx.get(url, headers={"User-Agent": UA}, timeout=20, follow_redirects=True)
        r.raise_for_status()
    except Exception as e:
        code = getattr(getattr(e, "response", None), "status_code", "")
        log(f"feed failed {url}: {type(e).__name__} {code}".rstrip())
        return []
    feed = feedparser.parse(r.content)
    source = _clean(feed.feed.get("title", "")) or re.sub(r"^www\.", "", httpx.URL(url).host)
    items = []
    for e in feed.entries:
        when = _when(e)
        if when is None or when < since or not e.get("link"):
            continue
        items.append({"title": _clean(e.get("title", "")), "url": e["link"], "source": source,
                      "when": when, "snippet": _clean(e.get("summary", ""))[:500]})
    return items


def search_news(query: str) -> list[dict]:
    """SearXNG news results from the last day (your local instance)."""
    import httpx

    try:
        r = httpx.get(f"{SEARXNG_URL}/search", timeout=20,
                      params={"q": query, "format": "json", "categories": "news", "time_range": "day"})
        r.raise_for_status()
        hits = r.json().get("results", [])[:8]
    except Exception as e:
        log(f"search failed '{query}': {type(e).__name__}")
        return []
    now = dt.datetime.now(dt.timezone.utc)
    return [{"title": _clean(h.get("title", "")), "url": h["url"], "when": now,
             "source": re.sub(r"^https?://(www\.)?([^/]+).*", r"\2", h["url"]),
             "snippet": _clean(h.get("content", ""))[:500]}
            for h in hits if h.get("url")]


def _words(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", title.lower()) if len(w) > 3}


def pick(items: list[dict], n: int) -> list[dict]:
    """Group near-duplicate headlines; stories more outlets carry come first."""
    groups: list[dict] = []
    for it in sorted(items, key=lambda x: x["when"], reverse=True):
        w = _words(it["title"])
        for g in groups:
            inter = len(w & g["words"])
            if w and inter / max(1, min(len(w), len(g["words"]))) >= 0.6:
                if it["source"] not in g["sources"]:
                    g["sources"].append(it["source"])
                    g["also"].append(it)
                break
        else:
            groups.append({"item": it, "words": w, "sources": [it["source"]], "also": []})
    groups.sort(key=lambda g: (len(g["sources"]), g["item"]["when"]), reverse=True)
    out = []
    for g in groups[:n]:
        it = dict(g["item"])
        it["sources"] = g["sources"]
        it["links"] = [(x["source"], x["url"]) for x in [g["item"]] + g["also"]]
        out.append(it)
    return out


def add_text(items: list[dict], top: int) -> None:
    try:
        from research_server import fetch_page
    except Exception:
        return
    for it in items[:top]:
        try:
            it["text"] = (fetch_page(it["url"], max_chars=1800) or {}).get("text", "")
        except Exception:
            it["text"] = ""


def parse_points(txt: str) -> list[dict]:
    """Pull [{headline, summary, refs}] out of a model reply, tolerating code fences,
    a wrapping object ({"stories": [...]}), extra prose or a cut-off ending."""
    txt = re.sub(r"```(?:json)?", "", txt or "")
    candidates = []
    for m in re.finditer(r"[\[{]", txt):
        try:
            candidates.append(json.JSONDecoder().raw_decode(txt[m.start():])[0])
            break
        except ValueError:
            continue
    data = candidates[0] if candidates else None
    if isinstance(data, dict):
        data = next((v for v in data.values()
                     if isinstance(v, list) and v and isinstance(v[0], dict)), [data])
    if not isinstance(data, list):  # cut off: salvage the complete objects
        data = []
        for m in re.finditer(r"\{[^{}]*\}", txt):
            try:
                data.append(json.loads(m.group(0)))
            except ValueError:
                pass
    out = []
    for d in data:
        if isinstance(d, dict) and d.get("headline"):
            refs = d.get("refs") or []
            refs = [int(r) for r in (refs if isinstance(refs, list) else [refs])
                    if str(r).strip("[] ").isdigit()]
            out.append({"headline": str(d["headline"]), "summary": str(d.get("summary", "")), "refs": refs})
    return out


def summarise(section: str, items: list[dict]) -> list[dict]:
    """Returns [{headline, summary, refs:[n]}]; falls back to plain headlines."""
    lines = []
    for i, it in enumerate(items, 1):
        body = it.get("text") or it["snippet"]
        lines.append(f"[{i}] {it['title']} ({', '.join(it['sources'])})\n{body[:1800]}")
    prompt = (
        f"Section: {section}. Below are today's stories, numbered.\n"
        "Pick the 3 to 5 that matter most to this reader. For each, write a short headline and "
        "one or two plain sentences on what happened and why it matters. Use only the text given; "
        "if something is unclear, leave it out.\n"
        'Reply with only JSON: [{"headline": "...", "summary": "...", "refs": [1, 2]}]\n\n'
        + "\n\n".join(lines)
    )
    txt = ""
    for attempt in (1, 2):  # one retry with a firmer nudge and more room
        try:
            txt = ask_model(prompt if attempt == 1 else
                            prompt + "\n\nStart your reply with [ and end it with ]. No other text.",
                            max_tokens=1200 * attempt)
        except Exception as e:
            log(f"model call failed for {section}: {type(e).__name__}: {e}")
            break
        out = parse_points(txt)
        if out:
            return out[:5]
    log(f"summary failed for {section}; using headlines. Model said: {txt[:200]!r}")
    return [{"headline": it["title"], "summary": "", "refs": [i]} for i, it in enumerate(items[:5], 1)]


def overview(sections: list[dict]) -> str:
    bullets = "\n".join(f"- {s['name']}: {p['headline']}. {p.get('summary', '')}"
                        for s in sections for p in s["points"])
    try:
        return ask_model("From these points, write 2 or 3 sentences: what is worth knowing first "
                         "this morning. Plain text, no list.\n\n" + bullets, max_tokens=300)
    except Exception:
        return ""


# ---------------------------------------------------------------- brew
def brew_running() -> bool:
    return subprocess.run(["pgrep", "-f", "brew-daily.sh"], capture_output=True).returncode == 0


def brew_needs_you(wait_s: int = 1200) -> str:
    """Today's 'Needs you' part of brew-summary.md, or '' if nothing needs you."""
    end = time.time() + wait_s
    while brew_running() and time.time() < end:
        time.sleep(20)
    if not BREW_SUMMARY.exists():
        return ""
    if dt.date.fromtimestamp(BREW_SUMMARY.stat().st_mtime) != dt.date.today():
        return ""
    m = re.search(r"## Needs you.*?\n(.*?)(?=\nbrew upgrade exit code|\Z)", BREW_SUMMARY.read_text(), re.S)
    body = (m.group(1).strip() if m else "")
    return "" if body in ("", "- nothing") else body


# ---------------------------------------------------------------- output
def render_md(day: dt.date, top: str, sections: list[dict], brew: str, model_ok: bool = True) -> str:
    out = [f"# Morning brief: {day:%A %d %B %Y}", ""]
    if top:
        out += [top, ""]
    if brew:
        out += ["## Needs you: Homebrew", "", brew, ""]
    for s in sections:
        out += [f"## {s['name']}", ""]
        for p in s["points"]:
            refs = [r for r in p.get("refs", []) if isinstance(r, int) and 0 < r <= len(s["items"])]
            pairs = dict(pair for r in refs for pair in s["items"][r - 1]["links"])
            links = ", ".join(f"[{src}]({url})" for src, url in pairs.items())
            line = f"- **{p['headline']}**"
            if p.get("summary"):
                line += f" {p['summary']}"
            if links:
                line += f" ({links})"
            out.append(line)
        out.append("")
    out.append(f"_Made locally at {dt.datetime.now():%H:%M} by {chat_model()}._" if model_ok else
               f"_Headlines only at {dt.datetime.now():%H:%M}: the local model was not available._")
    return "\n".join(out) + "\n"


def md_to_html(md: str, title: str) -> str:
    def inline(s: str) -> str:
        s = html.escape(s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        return re.sub(r"(?<!\w)_(.+?)_(?!\w)", r"<em>\1</em>", s)

    body, in_list, in_code = [], False, False
    for line in md.splitlines():
        if line.startswith("```"):
            body.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            body.append(html.escape(line))
            continue
        if line.startswith("- ") and not in_list:
            body.append("<ul>")
            in_list = True
        elif not line.startswith("- ") and in_list:
            body.append("</ul>")
            in_list = False
        if line.startswith("# "):
            body.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("## "):
            body.append(f"<h2>{inline(line[3:])}</h2>")
        elif line.startswith("- "):
            body.append(f"<li>{inline(line[2:])}</li>")
        elif line.strip():
            body.append(f"<p>{inline(line)}</p>")
    if in_list:
        body.append("</ul>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root {{ --bg:#fbfaf7; --fg:#1d1d1f; --muted:#6e6e73; --accent:#b4231f; --line:#e5e3dd; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#17171a; --fg:#ececf0; --muted:#9a9aa2; --accent:#ff6b5e; --line:#2c2c31; }} }}
body {{ background:var(--bg); color:var(--fg); font:17px/1.55 -apple-system, system-ui, sans-serif; margin:0; }}
main {{ max-width:720px; margin:0 auto; padding:32px 16px 64px; }}
h1 {{ font-size:28px; margin:0 0 12px; }}
h2 {{ font-size:15px; text-transform:uppercase; letter-spacing:.06em; color:var(--accent);
      border-top:1px solid var(--line); padding-top:20px; margin-top:28px; }}
ul {{ padding-left:20px; }} li {{ margin:0 0 12px; }}
a {{ color:var(--muted); }} em {{ color:var(--muted); font-size:14px; }}
pre {{ background:var(--line); padding:10px 12px; border-radius:6px; overflow-x:auto; }}
</style></head><body><main>
{chr(10).join(body)}
</main></body></html>
"""


def notify(page: Path, text: str) -> None:
    if sys.platform != "darwin":
        return
    tn = next((p for p in ("/opt/homebrew/bin/terminal-notifier", "/usr/local/bin/terminal-notifier")
               if Path(p).exists()), None)
    if tn:  # clicking the notification opens the page
        subprocess.run([tn, "-title", "Morning brief", "-message", text, "-open", page.as_uri(),
                        "-group", "local-ai-brief"], capture_output=True)
    else:  # a plain notification can't open anything on click, so open the page itself
        text = text.replace('"', "'")
        subprocess.run(["/usr/bin/osascript", "-e",
                        f'display notification "{text}" with title "Morning brief"'], capture_output=True)
        subprocess.run(["/usr/bin/open", str(page)], capture_output=True)


def index_briefs() -> None:
    try:
        subprocess.run([sys.executable, str(REPO / "servers" / "docs_server.py"), "add", str(BRIEFS)],
                       capture_output=True, timeout=600)
    except Exception as e:
        log(f"indexing failed: {e}")


# ---------------------------------------------------------------- main
def run(force: bool = False) -> int:
    day = dt.date.today()
    md_path, page = BRIEFS / f"{day}.md", WEB / f"{day}.html"
    if md_path.exists() and not force:
        log(f"today's brief already exists: {md_path}")
        return 0
    WEB.mkdir(parents=True, exist_ok=True)
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=CONFIG.get("hours", 30))
    model_ok = ensure_model()
    if not model_ok:
        log("oMLX not available: writing headlines only")
    sections = []
    for sec in CONFIG["sections"]:
        items = [it for url in sec.get("feeds", []) for it in read_feed(url, since)]
        if sec.get("search"):
            items += search_news(sec["search"])
        items = pick(items, CONFIG.get("items_per_section", 6))
        log(f"{sec['name']}: {len(items)} stories")
        if not items:
            continue
        if model_ok:
            add_text(items, CONFIG.get("fetch_top", 3))
            points = summarise(sec["name"], items)
        else:
            points = [{"headline": it["title"], "summary": "", "refs": [i]} for i, it in enumerate(items[:5], 1)]
        sections.append({"name": sec["name"], "items": items, "points": points})
    if not sections:
        log("no news fetched (offline?)")
        return 1
    top = overview(sections) if model_ok else ""
    brew = brew_needs_you()
    md = render_md(day, top, sections, brew, model_ok)
    md_path.write_text(md)
    page.write_text(md_to_html(md, f"Morning brief {day}"))
    log(f"wrote {md_path} and {page}")
    index_briefs()
    msg = (top.split(". ")[0][:150] if top else "Your news is ready.")
    if brew:
        msg = "Brew needs you. " + msg
    notify(page, msg)
    return 0


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "run":
        return run(force="--force" in argv)
    if cmd == "auto":
        return run(force=False)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
