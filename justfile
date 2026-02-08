set dotenv-load

# Run the server
start:
    uv run uvicorn src.github.main:app --host 0.0.0.0 --port 8000

# Run with auto-reload during development
dev:
    uv run uvicorn src.github.main:app --reload

# Install dependencies
install:
    uv sync

# Post a comment on a PR: just comment owner/repo 123 "message"
comment repo pr message:
    uv run python -m src.github.cli comment {{repo}} {{pr}} "{{message}}"

# Run Claude review on a PR: just review owner/repo 123
review repo pr:
    uv run python -m src.github.cli review {{repo}} {{pr}}

# Show PR comments: just comments owner/repo 123
comments repo pr:
    uv run python -m src.github.cli comments {{repo}} {{pr}}

# Approve a PR: just approve owner/repo 123
approve repo pr:
    uv run python -m src.github.cli approve {{repo}} {{pr}}
