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

# Approve a PR: just approve owner/repo 123 "optional message"
approve repo pr message="":
    uv run python -m src.github.cli approve {{repo}} {{pr}} "{{message}}"

# Reply to a review comment: just reply-comment owner/repo 123 456 "message"
reply-comment repo pr comment_id message:
    uv run python -m src.github.cli reply-comment {{repo}} {{pr}} {{comment_id}} "{{message}}"

# Show review threads: just review-comments owner/repo 123
review-comments repo pr:
    uv run python -m src.github.cli review-comments {{repo}} {{pr}}

# Submit a review with inline comments: just submit-review owner/repo 123 COMMENT '{"body":"...","comments":[...]}'
submit-review repo pr event review_json:
    uv run python -m src.github.cli submit-review {{repo}} {{pr}} {{event}} '{{review_json}}'
