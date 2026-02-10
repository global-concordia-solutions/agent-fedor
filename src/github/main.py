import asyncio
import hashlib
import hmac
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request

from .config import settings

PROJECT_DIR = Path(__file__).resolve().parent.parent.parent

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Fedor — PR Reviewer")

AGENT_PROMPT = """\
You are Agent Fedor (gcs-fedor[bot]), an automated code reviewer.

Repository: {repo}
Pull Request: #{pr}
Trigger: {trigger}

Clone directory: {clone_dir}
After `just review` runs, the cloned code is available at {clone_dir}/<sha>. You can read files there directly.

Available commands:

  just comments {repo} {pr}                                            — show existing PR issue comments
  just review-comments {repo} {pr}                                     — show review threads (inline code comments) with status, paths, and comment IDs
  just review {repo} {pr}                                              — clone the PR, run code review, return the result (does NOT post anything)
  just comment {repo} {pr} "<message>"                                 — post an issue comment on the PR
  just reply-comment {repo} {pr} <comment_id> "<message>"              — reply to a specific review thread (use comment IDs from `review-comments`)
  just submit-review {repo} {pr} <EVENT> '<json>'                      — submit a review with inline comments; EVENT is COMMENT or REQUEST_CHANGES
                                                                         json format: {{"body":"summary","comments":[{{"path":"file.py","line":10,"body":"issue"}}]}}
  just approve {repo} {pr} "<message>"                                 — approve the PR (auto-resolves all review threads first)

Rules:
- Comments from "gcs-fedor[bot]" are YOUR OWN previous comments — do not treat them as developer feedback.
- Comments from anyone else are developer feedback — respect their decisions.
- If the developer dismissed a concern as intentional or out-of-scope, do NOT repeat it.
- You are FULLY autonomous — make decisions and act on them immediately.
- NEVER ask for permission, confirmation, or present options like "Would you like me to...". There is NO human on the other end — you are running as a background service.
- You MUST always post your results to the PR using the `just` commands. Printing text to stdout does nothing — nobody reads it. The ONLY way to communicate is through the PR.
- Every run MUST end with either `just submit-review`, `just approve`, or `just comment` — never with plain text output.
- CRITICAL: All `just` commands MUST be a SINGLE LINE. Never use literal newlines inside command arguments. Use \\n for line breaks in messages (e.g., `just comment repo 123 "Line one\\nLine two"`). Multi-line commands will be silently rejected.

Your task:
1. Run `just comments {repo} {pr}` and `just review-comments {repo} {pr}` to read existing comments, review threads, and developer feedback.
2. Decide what to do based on the trigger and context:
   - If this is a new PR or new commits:
     a. Run `just review {repo} {pr}` to get the code review.
     b. If there are issues, submit a review with inline comments:
        `just submit-review {repo} {pr} COMMENT '<json>'`
        or `just submit-review {repo} {pr} REQUEST_CHANGES '<json>'` for blocking issues.
     c. If the code is clean, approve with a summary message:
        `just approve {repo} {pr} "LGTM — summary of what looks good"`
   - If this is a developer comment in a review thread:
     a. Read the thread context via `just review-comments {repo} {pr}`.
     b. Reply directly in the thread using `just reply-comment {repo} {pr} <comment_id> "<response>"`.
     c. If all concerns are addressed, approve with `just approve {repo} {pr} "All concerns resolved"`.
   - If this is a developer issue comment — read what they asked for and respond appropriately.
3. Prefer inline review comments (`submit-review`) over issue comments (`comment`) for code-specific feedback.
4. If the code is ready — run `just approve {repo} {pr} "<summary>"` immediately. This resolves all open threads.
5. If there are real blocking issues — do NOT approve; your review comments are enough.
"""


def _extract_tool_result_text(content: object) -> str:
    """Extract text from a tool_result content field (string or list of blocks)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") for b in content if isinstance(b, dict)
        )
    return str(content) if content else ""


def _log_stream_event(event: dict, bash_commands: list[str]) -> None:
    """Parse and log a stream-json event from Claude CLI."""
    etype = event.get("type", "")

    if etype == "assistant":
        for block in event.get("message", {}).get("content", []):
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name == "Bash" and isinstance(inp, dict):
                    cmd = inp.get("command", "")
                    if cmd:
                        bash_commands.append(cmd)
                        logger.info("[bash] %s", cmd.replace("\n", "\\n")[:500])
                else:
                    logger.info("[tool] %s", name)
            elif btype == "text":
                text = block.get("text", "").strip()
                if text:
                    logger.info("[text] %s", text[:300])

    elif etype == "user":
        for block in event.get("message", {}).get("content", []):
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            text = _extract_tool_result_text(block.get("content", ""))
            if block.get("is_error"):
                logger.warning("[tool_error] %s", text[:500])
            elif text:
                logger.debug("[tool_ok] %s", text[:200])

    elif etype == "result":
        cost = event.get("total_cost_usd")
        turns = event.get("num_turns")
        duration = event.get("duration_ms")
        is_error = event.get("is_error", False)
        logger.info(
            "[done] cost=$%.4f turns=%s duration=%.1fs error=%s",
            cost or 0, turns, (duration or 0) / 1000, is_error,
        )
        if is_error:
            logger.error("[error] %s", event.get("result", "")[:500])
        for denial in event.get("permission_denials", []):
            logger.warning("[denied] %s", denial)


async def _handle_pr(repo_full_name: str, pr_number: int, trigger: str) -> None:
    try:
        logger.info("Handling %s for %s#%d", trigger, repo_full_name, pr_number)

        prompt = AGENT_PROMPT.format(
            repo=repo_full_name, pr=pr_number, trigger=trigger,
            clone_dir=settings.clone_dir,
        )
        cmd = [
            settings.claude_command,
            "-p", prompt,
            "--output-format", "stream-json",
            "--verbose",
            "--allowedTools", "Bash(just *)",
            "--allowedTools", f"Read({settings.clone_dir}/*)",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=PROJECT_DIR,
        )

        bash_commands: list[str] = []

        async def _stream_stdout():
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").rstrip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    logger.info("[claude] %s", line[:500])
                    continue
                _log_stream_event(event, bash_commands)

        async def _stream_stderr():
            assert proc.stderr
            async for raw in proc.stderr:
                line = raw.decode(errors="replace").rstrip()
                if line:
                    logger.warning("[claude:stderr] %s", line[:500])

        await asyncio.wait_for(
            asyncio.gather(_stream_stdout(), _stream_stderr(), proc.wait()),
            timeout=600,
        )

        if proc.returncode != 0:
            logger.error("Claude exited with code %d for %s#%d", proc.returncode, repo_full_name, pr_number)
        elif not bash_commands:
            logger.warning("Claude ran no commands for %s#%d — review likely not posted", repo_full_name, pr_number)
        else:
            logger.info("Review completed for %s#%d — ran %d commands", repo_full_name, pr_number, len(bash_commands))
    except asyncio.TimeoutError:
        logger.error("Claude timed out for %s#%d", repo_full_name, pr_number)
        proc.kill()
    except Exception:
        logger.exception("Review failed for %s#%d", repo_full_name, pr_number)


BOT_LOGIN = "gcs-fedor[bot]"


def _has_required_label(labels: list[dict]) -> bool:
    """Check if PR has the required label. If no label configured, allow all."""
    if not settings.github_pr_label:
        return True
    return any(l["name"] == settings.github_pr_label for l in labels)

# key: "owner/repo#123" → pending asyncio.Task
_pending: dict[str, asyncio.Task] = {}


def _schedule_pr(repo_full_name: str, pr_number: int, trigger: str) -> None:
    """Debounce: cancel previous timer for this PR, start a new one."""
    key = f"{repo_full_name}#{pr_number}"

    old = _pending.pop(key, None)
    if old and not old.done():
        old.cancel()
        logger.info("Debounce: reset timer for %s", key)

    async def _delayed():
        delay = settings.webhook_delay
        logger.info("Waiting %ds before handling %s (%s)", delay, key, trigger)
        await asyncio.sleep(delay)
        _pending.pop(key, None)
        await _handle_pr(repo_full_name, pr_number, trigger)

    _pending[key] = asyncio.create_task(_delayed())


@app.post("/webhook")
async def webhook(
    request: Request,
    x_hub_signature_256: str = Header(...),
    x_github_event: str = Header(...),
) -> dict:
    payload = await request.body()
    _verify_signature(payload, x_hub_signature_256)

    data = await request.json()
    action = data.get("action")

    if x_github_event == "pull_request" and action in ("opened", "synchronize", "labeled"):
        repo = data["repository"]
        pr = data["pull_request"]

        if not _has_required_label(pr.get("labels", [])):
            logger.info("Ignored PR #%d: missing required label %r", pr["number"], settings.github_pr_label)
            return {"status": "ignored", "reason": "missing required label"}

        if action == "opened":
            trigger = "new PR"
        elif action == "synchronize":
            trigger = "new commits pushed"
        else:
            trigger = "label added"

        _schedule_pr(repo["full_name"], pr["number"], trigger)
        return {"status": "ok", "pr": pr["number"]}

    if x_github_event == "pull_request_review_comment" and action == "created":
        sender = data["comment"]["user"]["login"]
        if sender == BOT_LOGIN:
            logger.info("Ignored own review comment on PR")
            return {"status": "ignored", "reason": "own review comment"}

        if not _has_required_label(data["pull_request"].get("labels", [])):
            logger.info("Ignored review comment: PR missing required label %r", settings.github_pr_label)
            return {"status": "ignored", "reason": "missing required label"}

        repo = data["repository"]
        pr_number = data["pull_request"]["number"]
        _schedule_pr(repo["full_name"], pr_number, f"review comment from @{sender}")
        return {"status": "ok", "pr": pr_number}

    if x_github_event == "issue_comment" and action == "created":
        # Only handle comments on PRs (they have a pull_request key)
        issue = data["issue"]
        if "pull_request" not in issue:
            logger.info("Ignored issue_comment on non-PR issue #%d", issue["number"])
            return {"status": "ignored", "reason": "not a PR comment"}

        # Ignore our own comments to prevent infinite loops
        sender = data["comment"]["user"]["login"]
        if sender == BOT_LOGIN:
            logger.info("Ignored own comment on #%d", issue["number"])
            return {"status": "ignored", "reason": "own comment"}

        if not _has_required_label(issue.get("labels", [])):
            logger.info("Ignored issue comment: PR #%d missing required label %r", issue["number"], settings.github_pr_label)
            return {"status": "ignored", "reason": "missing required label"}

        repo = data["repository"]
        _schedule_pr(repo["full_name"], issue["number"], f"comment from @{sender}")
        return {"status": "ok", "pr": issue["number"]}

    logger.info("Ignored event: %s/%s", x_github_event, action)
    return {"status": "ignored", "reason": f"{x_github_event}/{action}"}


def _verify_signature(payload: bytes, signature: str) -> None:
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
