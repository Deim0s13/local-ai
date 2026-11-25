# local-ai
Local Claude-style AI setup on Apple silicon, open-source stack

Jan is the desktop chat (Claude Desktop), OpenCode is the coding agent (Claude Code), and oMLX serves the open-weight models on one local endpoint.

## Quick start (Phase 1)

Prerequisites: Homebrew, and oMLX installed at `~/.omlx` with its models downloaded.

```sh
cd ~/Projects/local-ai
./setup.sh          # safe to re-run
# open a new terminal, then:
./scripts/check.sh
```

`setup.sh` does the following:

- creates `~/.local-ai/` for your profile, memory and logs, outside the repo
- starts oMLX and picks the coding and chat models from what it serves
- installs OpenCode and writes `~/.config/opencode/opencode.json` from `config/opencode.template.json`
- installs Jan
- loads a launchd job that runs `brew update` and `brew upgrade --greedy` daily at 09:30 (if the Mac is asleep then, it runs once on wake)

To choose models yourself, run `CODE_MODEL=<id> CHAT_MODEL=<id> ./setup.sh`, using ids from `./scripts/check.sh`.

The oMLX API key is never copied. New shells read it from `~/.omlx/settings.json` into `OMLX_API_KEY`.

### Connect Jan (once, by hand)

1. In Jan, open Settings, then Model Providers, and add an OpenAI-compatible provider.
2. Set the base URL to `http://127.0.0.1:8000/v1`.
3. For the API key, paste the output of `jq -r .auth.api_key ~/.omlx/settings.json`. Jan stores it in its own app data, not here.
4. Add the chat model id that `./scripts/check.sh` lists, then edit that model (pencil icon) and switch on **Tools**. Without it Jan never calls the research or docs tools.
5. Create an assistant for yourself in Jan, then re-run `./setup.sh`. From then on its instructions stay in sync with `~/.local-ai/profile/about-me.md`: every save of that file updates Jan automatically. Quit and reopen Jan to load a change (a notification reminds you). With more than one custom assistant, set `JAN_ASSISTANT` in `~/.local-ai/settings.env`.
6. Until Phase 2, leave Jan's built-in MCP or search tools off, so answers come only from the model.

### Use it

- Chat: open Jan and pick the chat model.
- Code: run `opencode` in any project folder. Edits and shell commands ask first, and `git push`, `rm` and `sudo` are blocked.
- Profile: edit `~/.local-ai/profile/about-me.md`. OpenCode reads it on every session; Jan is synced on every save (log: `~/.local-ai/logs/profile-sync.log`).
- Brew log: `~/.local-ai/logs/brew.log`.

## Research and your documents (Phase 2)

Both Jan and OpenCode get two local MCP tool servers, which `./setup.sh` installs and registers:

| Server | Tools | What it does |
|---|---|---|
| `research` | `web_search`, `fetch_page` | Searches through your own SearXNG (Podman, 127.0.0.1:8888, no API keys), then reads pages or PDFs |
| `docs` | `search_docs`, `list_sources` | Searches by meaning over folders you choose, using on-device MiniLM embeddings |

Index a folder, and re-run the same command to refresh it:

```sh
scripts/rag.sh add ~/Documents/some-folder
scripts/rag.sh status
scripts/rag.sh search what did we decide about failover
```

Supported files: Markdown, text, PDF, Word, PowerPoint, Excel and HTML. The index lives in `~/.local-ai/rag/`, outside the repo. SearXNG's config and secret live in `~/.local-ai/searxng/`.

Then ask Jan or OpenCode things like "search the web for the latest OpenShift AI release and summarise it" or "what do my docs say about ticket enrichment?". Quit and reopen Jan after setup so it loads the new tools.

## Monthly model scout (Phase 3)

Once a month the scout looks for new open-weight MLX models (mlx-community, lmstudio-community and unsloth) that fit in 30 GB. It tests up to two of them against your champions on a 20-task set (`evals/tasks.json`):

- coding: 8 tasks checked by unit tests
- research: 4 tasks that answer from a given passage
- Red Hat and architecture writing: 6 tasks checked by a rubric plus a judge score from the chat champion
- format: 2 tasks checking exact output shape

It records quality, speed and memory in `~/.local-ai/evals/<month>/scorecard.md`. You get a notification only when a challenger clearly wins; it never switches models by itself. Challengers that lose are deleted again, to save disk.

It runs by itself, because it stops oMLX while it tests: overnight (00:00 to 06:00), or otherwise the next time the Mac is on power and idle for 15 minutes. Before it starts, a dialog asks **Run now** or **Not today**. If nobody answers within 10 minutes it runs anyway. If you come back mid-run without having said "Run now", it stops, restarts oMLX, and picks up where it left off at the next idle moment. While it runs it uses `caffeinate` so the Mac does not sleep halfway, and lets go as soon as it finishes.

Optional: to let it run overnight even when the lid is closed on power, wake the Mac at 02:00 daily. This replaces any existing `pmset repeat` schedule; undo with `sudo pmset repeat cancel`.

```sh
sudo pmset repeat wakeorpoweron MTWRFSU 02:00:00
```

```sh
scripts/scout.sh discover   # what's new, no downloads
scripts/scout.sh run        # run now (30-60 min)
scripts/scout.sh last       # latest scorecard
```

It skips models under 8 GB (too small to replace a 30B-class model) and models whose architecture your mlx-lm can't run yet, before downloading anything. Both champions are tested every run, and a general model can win the coding job too. Champions, size limits and the win rules live in `config/scout.json`.

## Morning brief (Phase 4)

At 09:30 every day your Mac builds a short news brief: New Zealand, world, tech and AI, business and sport. Headlines come from RSS feeds (the list from newsbrief) plus your SearXNG for the last day. Stories several outlets carry come first. Your chat model summarises them in the style of your profile. If the brew job left anything that needs your password, it's at the top.

When it's ready, a notification appears; click it to open the page. The page always lives at `~/.local-ai/briefs/latest.html`, with the past week in a sidebar, so you can pin it as a browser tab. It refreshes itself when you come back to it. `scripts/brief.sh open` opens it too. Nothing leaves your Mac apart from fetching the news, and no accounts are needed. If the Mac was asleep at 09:30, it runs when it wakes.

```sh
scripts/brief.sh run      # build today's brief now (2-5 min)
scripts/brief.sh open     # open the latest one
scripts/brief.sh last     # print it in Terminal
```

Briefs are saved as `~/.local-ai/briefs/notes/<date>.md` (kept) and are added to your document search, so you can ask Jan things like "what was in the news about Red Hat last week?". Day pages older than 30 days are deleted; the Markdown copies stay. Edit feeds and sections in `config/brief.json`. Calendar and email come later.



This repo is public. It holds code and config templates only.

- **No secrets.** Keys, tokens and passwords live in Bitwarden and are fetched at run time with the `bw` CLI into environment variables. `.env.example` lists names only.
- **No personal data.** Profile, memory, RAG documents, email and calendar output, and eval results live in `~/.local-ai/`.
- **Guards:** `.gitignore`, a gitleaks pre-commit hook, and GitHub secret scanning with push protection.
- **Local only.** Every service listens on 127.0.0.1.

One-time setup on a new clone:

```sh
brew install pre-commit gitleaks
pre-commit install
```
