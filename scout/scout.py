"""Model scout: monthly check for better open-weight MLX models.

  python scout.py discover            list new MLX models that fit, no downloads
  python scout.py run [--force]       full run now: discover, test, score, scorecard
  python scout.py auto                what launchd calls every 30 min: runs once a month,
                                      overnight, or when the Mac is idle on power; pauses
                                      and resumes later if you come back mid-run
  python scout.py gen <repo> <out>    (internal) generate answers for one model
  python scout.py rescore <month>     re-judge and rebuild a scorecard (e.g. 2026-12)

Everything it writes goes to ~/.local-ai/evals/<YYYY-MM>/, outside the repo.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONFIG = json.loads((REPO / "config" / "scout.json").read_text())
TASKS = json.loads((REPO / "evals" / "tasks.json").read_text())["tasks"]
HOME = Path(os.environ.get("LOCAL_AI_HOME", Path.home() / ".local-ai")).expanduser()
EVALS = HOME / "evals"
OMLX = Path.home() / ".omlx" / "bin" / "omlx"
OMLX_SETTINGS = Path.home() / ".omlx" / "settings.json"
OMLX_URL = "http://127.0.0.1:8000"
HF_API = "https://huggingface.co/api"
os.environ.setdefault("HF_HUB_VERBOSITY", "error")  # no "unauthenticated requests" nag; no token needed
SYSTEM = "You are a concise, practical assistant for an enterprise architect. Use plain language."


def log(msg: str) -> None:
    print(f"{dt.datetime.now():%H:%M:%S} {msg}", flush=True)


def notify(msg: str, title: str = "Model scout") -> None:
    if sys.platform == "darwin":
        msg = msg.replace('"', "'")
        subprocess.run(["/usr/bin/osascript", "-e", f'display notification "{msg}" with title "{title}"'],
                       capture_output=True)


def month_dir(month: str | None = None) -> Path:
    return EVALS / (month or f"{dt.date.today():%Y-%m}")


# ---------------------------------------------------------------- discover
def _get(url: str, params: dict | None = None):
    import httpx

    r = httpx.get(url, params=params, timeout=30, follow_redirects=True)
    r.raise_for_status()
    return r.json()


def model_size_gb(repo: str) -> float | None:
    try:
        info = _get(f"{HF_API}/models/{repo}", {"blobs": "true"})
    except Exception:
        return None
    total = sum(s.get("size") or 0 for s in info.get("siblings", [])
                if s.get("rfilename", "").endswith(".safetensors"))
    return round(total / 1e9, 1) if total else None


def arch_supported(repo: str) -> tuple[bool, str]:
    """Does the installed mlx-lm know this model's architecture? Checked before downloading."""
    try:
        import importlib.util

        import mlx_lm  # noqa: F401
    except Exception:
        return True, ""  # can't tell here (not on the Mac); gen will find out
    try:
        import httpx

        r = httpx.get(f"https://huggingface.co/{repo}/resolve/main/config.json", timeout=20,
                      follow_redirects=True)
        r.raise_for_status()
        mt = r.json().get("model_type", "")
    except Exception:
        return True, ""
    try:
        from mlx_lm.utils import MODEL_REMAPPING
        mt = MODEL_REMAPPING.get(mt, mt)
    except Exception:
        pass
    ok = importlib.util.find_spec(f"mlx_lm.models.{mt.replace('-', '_')}") is not None
    return ok, mt


def role_of(repo: str) -> str:
    return "code" if re.search(r"coder|code", repo, re.I) else "chat"


def seen_models() -> set[str]:
    f = EVALS / "seen.json"
    return set(json.loads(f.read_text())) if f.exists() else set()


def discover() -> list[dict]:
    since = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=CONFIG["lookback_days"])
    skip = seen_models() | set(CONFIG["champions"].values())
    found, errors = [], 0
    for author in CONFIG["sources"]:
        try:
            items = _get(f"{HF_API}/models", {"author": author, "sort": "createdAt", "direction": "-1",
                                              "limit": "100"})
        except Exception as e:
            log(f"discover {author}: {e}")
            errors += 1
            continue
        for m in items:
            repo = m.get("id") or m.get("modelId", "")
            name = repo.lower()
            created = m.get("createdAt")
            if not repo or repo in skip or not created:
                continue
            if dt.datetime.fromisoformat(created.replace("Z", "+00:00")) < since:
                continue
            if "mlx" not in name and "mlx" not in [t.lower() for t in m.get("tags", [])]:
                continue
            if any(x in name for x in CONFIG["exclude"]):
                continue
            if m.get("pipeline_tag") not in (None, "text-generation"):
                continue
            found.append({"repo": repo, "created": created[:10], "downloads": m.get("downloads", 0),
                          "likes": m.get("likes", 0), "role": role_of(repo)})
    if errors == len(CONFIG["sources"]):
        raise RuntimeError("Hugging Face unreachable; will try again next night")
    found.sort(key=lambda x: (x["downloads"] + 50 * x["likes"]), reverse=True)
    fits = []
    for m in found[:25]:
        gb = model_size_gb(m["repo"])
        if gb is None or gb > CONFIG["max_model_gb"]:
            continue
        if gb < CONFIG.get("min_model_gb", 0):
            log(f"  skip {m['repo']}: {gb} GB is too small to challenge your champions")
            continue
        ok, arch = arch_supported(m["repo"])
        if not ok:
            log(f"  skip {m['repo']}: mlx-lm can't run '{arch}' models yet (setup.sh upgrades it)")
            continue
        m["size_gb"] = gb
        fits.append(m)
    return fits


# ---------------------------------------------------------------- generate (one model per process)
def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).strip()


def gen(repo: str, out: Path) -> int:
    import mlx.core as mx
    from mlx_lm import load, stream_generate

    t0 = time.time()
    model, tok = load(repo)
    load_s = round(time.time() - t0, 1)
    results, tps = [], []
    for task in TASKS:
        msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": task["prompt"]}]
        try:
            prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False,
                                             enable_thinking=False)
        except TypeError:
            prompt = tok.apply_chat_template(msgs, add_generation_prompt=True, tokenize=False)
        text, last = "", None
        start = time.time()
        for resp in stream_generate(model, tok, prompt, max_tokens=CONFIG["max_tokens"]):
            text += resp.text
            last = resp
        results.append({"id": task["id"], "output": strip_think(text),
                        "seconds": round(time.time() - start, 1),
                        "tps": round(getattr(last, "generation_tps", 0.0), 1)})
        tps.append(results[-1]["tps"])
        log(f"  {task['id']}: {results[-1]['seconds']}s")
    out.write_text(json.dumps({
        "repo": repo, "load_seconds": load_s,
        "peak_memory_gb": round(mx.get_peak_memory() / 1e9, 1),
        "median_tps": sorted(tps)[len(tps) // 2] if tps else 0,
        "results": results}, indent=2))
    return 0


# ---------------------------------------------------------------- scoring
BANNED_NOTE = "buzzwords"


def _norm(s: str) -> str:
    return re.sub(r"[\s,]", "", s.lower())


def _has(text: str, term) -> bool:
    alts = term if isinstance(term, list) else [term]
    return any(_norm(a) in _norm(text) for a in alts)


def extract_code(text: str) -> str:
    m = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, re.S)
    return max(m, key=len) if m else text


def run_tests(code: str, tests: str) -> float:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "t.py"
        f.write_text(code + "\n\n" + tests + "\n")
        try:
            r = subprocess.run([sys.executable, "-I", str(f)], cwd=td, capture_output=True, timeout=20)
            return 1.0 if r.returncode == 0 else 0.0
        except subprocess.TimeoutExpired:
            return 0.0


def parse_score(txt: str) -> int | None:
    """The 1-5 score from a judge reply, whatever wraps it (JSON, prose, fences)."""
    m = (re.search(r'"?score"?\s*[:=]\s*["<]?([1-5])\b(?!\s*-)', txt, re.I)
         or re.search(r"\b([1-5])\s*(?:/|out of)\s*5\b", txt)
         or re.fullmatch(r"\s*([1-5])\s*", txt))
    return int(m.group(1)) if m else None


def judge(task: dict, answer: str, judge_model: str, key: str) -> float | None:
    import httpx

    prompt = (
        "You are grading an answer for an experienced enterprise architect audience.\n"
        "Score it from 1 to 5 for accuracy, practical usefulness, structure and plain, concise language "
        "(5 = excellent, would send as is; 1 = wrong or unusable).\n"
        'Reply with one line of JSON and nothing else, for example: {"score": 4, "reason": "clear but misses costs"}\n\n'
        f"TASK:\n{task['prompt']}\n\nANSWER:\n{answer[:6000]}"
    )
    txt = ""
    for attempt in (1, 2):
        try:
            r = httpx.post(f"{OMLX_URL}/v1/chat/completions", timeout=300,
                           headers={"Authorization": f"Bearer {key}"} if key else {},
                           json={"model": judge_model, "temperature": 0, "max_tokens": 200 * attempt,
                                 "chat_template_kwargs": {"enable_thinking": False},
                                 "messages": [{"role": "user", "content": prompt if attempt == 1 else
                                               prompt + "\n\nAnswer with only the score, a digit from 1 to 5."}]})
            r.raise_for_status()
            txt = strip_think(r.json()["choices"][0]["message"]["content"] or "")
        except Exception as e:
            log(f"judge call failed on {task['id']}: {type(e).__name__}: {e}")
            return None
        s = parse_score(txt)
        if s is not None:
            return (s - 1) / 4
    log(f"judge unreadable on {task['id']}: {txt[:120]!r}")
    return None


def score_task(task: dict, out: str, judge_fn=None) -> float:
    c = task["check"]
    t = c["type"]
    if t == "python_tests":
        return run_tests(extract_code(out), c["tests"])
    if t == "must_include":
        return sum(_has(out, x) for x in c["terms"]) / len(c["terms"])
    if t == "json_equals":
        body = re.sub(r"^```\w*\s*|\s*```$", "", out.strip())
        try:
            return 1.0 if json.loads(body) == c["expected"] else 0.0
        except Exception:
            return 0.0
    if t == "exact_bullets":
        lines = [x for x in out.strip().splitlines() if x.strip()]
        return 1.0 if len(lines) == c["count"] and all(x.startswith("- ") for x in lines) else 0.0
    if t == "rubric":
        words = len(out.split())
        length = 1.0 if words <= c["max_words"] else (0.5 if words <= 1.3 * c["max_words"] else 0.0)
        terms = sum(_has(out, x) for x in c["terms"]) / len(c["terms"])
        clean = 0.0 if any(b in out.lower() for b in c["banned"]) else 1.0
        checks = (length + terms + clean) / 3
        j = judge_fn(task, out) if judge_fn else None
        return checks if j is None else 0.5 * checks + 0.5 * j
    raise ValueError(t)


def score_model(gen_json: dict, judge_fn=None) -> dict:
    by_id = {r["id"]: r for r in gen_json["results"]}
    missed = []
    if judge_fn:
        inner = judge_fn

        def judge_fn(task, out):  # noqa: F811  counts tasks the judge couldn't score
            j = inner(task, out)
            if j is None:
                missed.append(task["id"])
            return j
    cats: dict[str, list[float]] = {}
    per = {}
    for task in TASKS:
        s = score_task(task, by_id.get(task["id"], {}).get("output", ""), judge_fn)
        per[task["id"]] = round(s, 2)
        cats.setdefault(task["category"], []).append(s)
    cat_scores = {k: round(100 * sum(v) / len(v), 1) for k, v in cats.items()}
    overall = round(100 * sum(sum(v) for v in cats.values()) / len(TASKS), 1)
    return {"repo": gen_json["repo"], "overall": overall, "categories": cat_scores, "tasks": per,
            "median_tps": gen_json["median_tps"], "peak_memory_gb": gen_json["peak_memory_gb"],
            "load_seconds": gen_json["load_seconds"], "unjudged": missed}


ROLE_CATEGORY = {"code": "coding", "chat": "architecture"}


def verdict(ch: dict, champ: dict, role: str) -> tuple[bool, str]:
    w = CONFIG["win_rules"]
    cat = ROLE_CATEGORY[role]
    gain = ch["categories"].get(cat, 0) - champ["categories"].get(cat, 0)
    drop = champ["overall"] - ch["overall"]
    speed = ch["median_tps"] / champ["median_tps"] if champ["median_tps"] else 1.0
    if gain >= w["role_score_gain"] and drop <= w["max_overall_drop"] and speed >= w["min_speed_ratio"]:
        return True, f"{cat} +{gain:.0f} points, overall {ch['overall'] - champ['overall']:+.0f}, speed x{speed:.2f}"
    if abs(ch["overall"] - champ["overall"]) <= w["equal_quality_band"] and speed >= w["faster_ratio_if_equal"]:
        return True, f"same quality, {speed:.1f}x faster"
    return False, f"{cat} {gain:+.0f}, overall {ch['overall'] - champ['overall']:+.0f}, speed x{speed:.2f}"


def omlx_id(repo: str) -> str:
    return repo.replace("/", "--")


def omlx_key() -> str:
    try:
        return json.loads(OMLX_SETTINGS.read_text())["auth"]["api_key"]
    except Exception:
        return os.environ.get("OMLX_API_KEY", "")


def omlx_ready() -> bool:
    import httpx

    try:
        httpx.get(f"{OMLX_URL}/v1/models", headers={"Authorization": f"Bearer {omlx_key()}"},
                  timeout=3).raise_for_status()
        return True
    except Exception:
        return False


def write_scorecard(d: Path, candidates: list[dict], scores: dict, judged: bool) -> list[str]:
    champs = CONFIG["champions"]
    lines = [f"# Model scout: {d.name}", ""]
    if not candidates:
        lines += ["No new MLX models that fit this month. Champions stay.", ""]
    wins = []
    lines += ["| Model | Role | Overall | Coding | Research | Architecture | Format | tok/s | Peak GB | Verdict |",
              "|---|---|---|---|---|---|---|---|---|---|"]
    for repo, s in scores.items():
        role = next((r for r, c in champs.items() if c == repo), None)
        tag = f"champion ({role})" if role else ""
        v = ""
        if not role:
            m = next((c for c in candidates if c["repo"] == repo), {"role": role_of(repo)})
            # coder models compete for coding; general models for both jobs
            vs = ["code"] if m["role"] == "code" else ["chat", "code"]
            parts = []
            for r in vs:
                champ = scores.get(champs[r])
                if not champ:
                    continue
                won, why = verdict(s, champ, r)
                parts.append(f"{'WIN ' if won else ''}vs {r} champion: {why}")
                if won:
                    wins.append(f"{repo} beats the {r} champion ({why})")
            v = "; ".join(parts)
            tag = m["role"]
        if s.get("unjudged"):
            v = (v + "; " if v else "") + f"judge could not score {len(s['unjudged'])} task(s)"
        c = s["categories"]
        lines.append(f"| {repo} | {tag} | {s['overall']} | {c.get('coding', '')} | {c.get('research', '')} | "
                     f"{c.get('architecture', '')} | {c.get('format', '')} | {s['median_tps']} | "
                     f"{s['peak_memory_gb']} | {v} |")
    lines += ["", "Scores are 0-100. Coding = unit tests pass; research = facts from a passage; "
              "architecture = rubric checks" + (" + a judge score from the chat champion" if judged else
                                                " only (judge unavailable)") +
              "; format = exact output shape.", ""]
    if wins:
        lines += ["## Recommended switch", ""] + [f"- {w}" for w in wins] + [
            "", "To switch, download it in oMLX, then run `CODE_MODEL=<id> ./setup.sh` or "
                "`CHAT_MODEL=<id> ./setup.sh` with the id oMLX shows, and update `config/scout.json`."]
    (d / "scorecard.md").write_text("\n".join(lines) + "\n")
    return wins


IDLE_START_S = 15 * 60   # start when idle this long (outside the night window)
IDLE_BACK_S = 60          # treat activity in the last minute as "you're back"
LOCK = EVALS / ".scout.lock"


def in_night() -> bool:
    return dt.datetime.now().hour < 6


def idle_seconds() -> float:
    """Seconds since the last keyboard/mouse input (macOS). 0 if unknown."""
    try:
        out = subprocess.run(["/usr/sbin/ioreg", "-c", "IOHIDSystem", "-d", "4"], capture_output=True,
                             text=True, timeout=10).stdout
        m = re.search(r'"HIDIdleTime" = (\d+)', out)
        return int(m.group(1)) / 1e9 if m else 0.0
    except Exception:
        return 0.0


def on_ac_power() -> bool:
    try:
        out = subprocess.run(["/usr/bin/pmset", "-g", "batt"], capture_output=True, text=True,
                             timeout=10).stdout
        return "AC Power" in out
    except Exception:
        return False


def user_back(watch: bool) -> bool:
    return watch and not in_night() and idle_seconds() < IDLE_BACK_S


def run(force_now: bool = False, watch: bool = False) -> int:
    """watch=True: started because the Mac was idle; stop cleanly if the user returns."""
    d = month_dir()
    if (d / "scorecard.md").exists() and not force_now:
        log(f"Already done this month: {d / 'scorecard.md'}")
        return 0
    d.mkdir(parents=True, exist_ok=True)
    if (d / "candidates.json").exists() and not force_now:
        cands = json.loads((d / "candidates.json").read_text())
        log("Resuming this month's run")
    else:
        log("Discovering new MLX models")
        try:
            cands = discover()[: CONFIG["max_challengers"]]
        except RuntimeError as e:
            log(str(e))
            return 1
        (d / "candidates.json").write_text(json.dumps(cands, indent=2))
    for c in cands:
        log(f"  candidate {c['repo']} ({c['role']}, {c['size_gb']} GB)")
    if not cands:
        write_scorecard(d, [], {}, False)
        log("No new candidates")
        return 0

    # both champions every time: a general model can also beat the coder at coding
    models = list(dict.fromkeys(CONFIG["champions"].values())) + [c["repo"] for c in cands]

    from huggingface_hub import scan_cache_dir

    cached_before = {r.repo_id for r in scan_cache_dir().repos}
    was_running = omlx_ready()
    if was_running:
        log("Stopping oMLX to free memory")
        subprocess.run([str(OMLX), "stop"], capture_output=True)
    interrupted = False
    try:
        for repo in models:
            out = d / f"gen-{omlx_id(repo)}.json"
            if out.exists():
                continue
            if user_back(watch):
                interrupted = True
                break
            log(f"Generating with {repo}")
            proc = subprocess.Popen([sys.executable, __file__, "gen", repo, str(out)])
            while proc.poll() is None:
                time.sleep(20)
                if user_back(watch):
                    log("You're back: pausing the scout, it will resume when the Mac is idle again")
                    proc.terminate()
                    try:
                        proc.wait(30)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    out.unlink(missing_ok=True)
                    interrupted = True
                    break
            if interrupted:
                break
            if proc.returncode != 0:
                log(f"  failed: {repo}")
    finally:
        if was_running:
            subprocess.run([str(OMLX), "start"], capture_output=True)
            for _ in range(120):
                if omlx_ready():
                    break
                time.sleep(2)
    if interrupted:
        return 3
    wins = rescore(d.name)
    # free disk: remove challengers we downloaded that did not win
    winners = {w.split(" ")[0] for w in wins}
    try:
        cache = scan_cache_dir()
        drop = [rev.commit_hash for repo in cache.repos
                if repo.repo_id in {c["repo"] for c in cands} - cached_before - winners
                for rev in repo.revisions]
        if drop:
            cache.delete_revisions(*drop).execute()
            log(f"Removed {len(drop)} downloaded challenger(s) that did not win")
    except Exception as e:
        log(f"cleanup skipped: {e}")
    seen = seen_models() | {c["repo"] for c in cands}
    (EVALS / "seen.json").write_text(json.dumps(sorted(seen), indent=2))
    return 0


def rescore(month: str) -> list[str]:
    d = month_dir(month)
    cands = json.loads((d / "candidates.json").read_text()) if (d / "candidates.json").exists() else []
    key = omlx_key()
    judged = omlx_ready()
    jmodel = omlx_id(CONFIG["champions"]["chat"])
    jfn = (lambda task, ans: judge(task, ans, jmodel, key)) if judged else None
    scores = {}
    for f in sorted(d.glob("gen-*.json")):
        g = json.loads(f.read_text())
        log(f"Scoring {g['repo']}")
        scores[g["repo"]] = score_model(g, jfn)
    (d / "scores.json").write_text(json.dumps(scores, indent=2))
    wins = write_scorecard(d, cands, scores, judged)
    log(f"Scorecard: {d / 'scorecard.md'}")
    if wins:
        notify(f"A new model beats your champion. See ~/.local-ai/evals/{month}/scorecard.md")
    return wins


ASK_TIMEOUT_S = 600  # no answer in 10 minutes means "go ahead"
DEFER = EVALS / ".deferred"


def ask() -> str:
    """Ask before starting. Returns "run", "skip" or "timeout"."""
    script = (
        'display dialog "The monthly model scout wants to run now. It stops your '
        'local models for 30-60 minutes. If you don\'t answer within 10 minutes '
        'it runs anyway." with title "Model scout" buttons {"Not today", "Run now"} '
        f'default button "Run now" giving up after {ASK_TIMEOUT_S}'
    )
    try:
        out = subprocess.run(["osascript", "-e", script], capture_output=True,
                             text=True, timeout=ASK_TIMEOUT_S + 60).stdout
    except Exception:
        return "timeout"
    if "gave up:true" in out:
        return "timeout"
    if "Run now" in out:
        return "run"
    return "skip"  # "Not today" (osascript also exits non-zero on cancel)


def auto() -> int:
    if (month_dir() / "scorecard.md").exists():
        return 0
    if DEFER.exists() and DEFER.read_text().strip() == f"{dt.date.today()}":
        return 0  # you said "Not today"
    night = in_night()
    if not night and not (on_ac_power() and idle_seconds() >= IDLE_START_S):
        return 0  # not a good moment; launchd checks again in 30 minutes
    EVALS.mkdir(parents=True, exist_ok=True)
    # a failed attempt waits 6 hours before retrying (e.g. Hugging Face down)
    fail = EVALS / ".last-failure"
    if fail.exists() and time.time() - fail.stat().st_mtime < 6 * 3600:
        return 0
    try:
        fd = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            os.kill(int(LOCK.read_text() or 0), 0)
            return 0  # another run is in progress
        except (ProcessLookupError, ValueError):
            LOCK.unlink(missing_ok=True)
            return auto()
    # keep the Mac awake (idle and system sleep) from the prompt until the run
    # ends; -w ties it to this process, so it can never outlive us
    awake = None
    try:
        awake = subprocess.Popen(["caffeinate", "-i", "-s", "-w", str(os.getpid())])
    except OSError:
        pass
    try:
        answer = ask()
        log(f"asked before running: {answer}")
        if answer == "skip":
            DEFER.write_text(f"{dt.date.today()}")
            return 0
        # if you chose "Run now", don't stop just because you're at the keyboard
        rc = run(watch=(answer == "timeout" and not night))
        if rc == 1:
            fail.touch()
        return 0 if rc in (0, 3) else rc
    finally:
        if awake:
            awake.terminate()
        LOCK.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "help"
    if cmd == "discover":
        for m in discover():
            print(f"{m['repo']:70} {m['role']:5} {m['size_gb']:5} GB  {m['created']}  dl={m['downloads']}")
        return 0
    if cmd == "run":
        return run(force_now="--force" in argv)
    if cmd == "auto":
        return auto()
    if cmd == "gen":
        return gen(argv[1], Path(argv[2]))
    if cmd == "rescore":
        rescore(argv[1] if len(argv) > 1 else f"{dt.date.today():%Y-%m}")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
