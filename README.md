# local-ai
Local Claude-style AI setup on Apple silicon, open-source stack

## Security rules

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
