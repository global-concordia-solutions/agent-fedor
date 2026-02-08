# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

Agent Fedor is a GitHub App webhook server that automatically reviews pull requests using the Claude CLI. Built with Python 3.12+ and FastAPI.

## Commands

```bash
# Install dependencies
uv sync

# Run the server
uvicorn src.github.main:app --host 0.0.0.0 --port 8000

# Run with auto-reload during development
uvicorn src.github.main:app --reload
```

No test suite or linter is configured yet.

## Architecture

The app follows a simple webhook-to-review pipeline:

1. **`src/github/main.py`** — FastAPI app with two endpoints: `POST /webhook` (GitHub events) and `GET /health`. Verifies HMAC-SHA256 signatures, filters for `pull_request` events (`opened`/`synchronize`), and dispatches reviews as background tasks.

2. **`src/github/github_app.py`** — GitHub App authentication. Generates RS256 JWTs from the app's private key, exchanges them for per-installation access tokens via GitHub API.

3. **`src/github/reviewer.py`** — Clones the PR branch (authenticated via `x-access-token`), runs `claude -p` with a review prompt in the cloned repo, returns the output. Supports `dummy_mode` for testing without real reviews.

4. **`src/github/config.py`** — Dataclass-based settings loaded from environment variables. Singleton `settings` instance used throughout.

**Flow:** GitHub webhook → signature check → background task → get installation token → clone repo → run Claude CLI → post review comment on PR.

## Configuration

Environment variables (see `.env.example`):
- `GITHUB_WEBHOOK_SECRET` (required) — webhook signature verification
- `GITHUB_APP_ID` — defaults to 2822626
- `GITHUB_PRIVATE_KEY_PATH` — path to `.pem` file, defaults to `./gcs-fedor.pem`
- `CLAUDE_COMMAND` — Claude CLI binary, defaults to `claude`
- `CLONE_DIR` — temp directory for cloned repos
- `DUMMY_MODE` — set `true`/`1`/`yes` to skip real reviews (returns stub response)
