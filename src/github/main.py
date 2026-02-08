import asyncio
import hashlib
import hmac
import logging

from fastapi import FastAPI, Header, HTTPException, Request

from .config import settings

logging.basicConfig(level=logging.INFO)
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

  just comments {repo} {pr}            — show existing PR comments
  just review {repo} {pr}              — clone the PR, run code review, return the result (does NOT post anything)
  just comment {repo} {pr} "<message>" — post a comment on the PR
  just approve {repo} {pr}             — approve the PR

Rules:
- Comments from "gcs-fedor[bot]" are YOUR OWN previous comments — do not treat them as developer feedback.
- Comments from anyone else are developer feedback — respect their decisions.
- If the developer dismissed a concern as intentional or out-of-scope, do NOT repeat it.
- You are autonomous — make decisions and act on them. NEVER ask for permission or confirmation.

Your task:
1. Run `just comments {repo} {pr}` to read existing comments and developer feedback.
2. Decide what to do based on the trigger and context:
   - If this is a new PR or new commits — run `just review {repo} {pr}` to get the code review.
   - If this is a developer comment — read what they asked for and respond appropriately.
3. Analyze the review output, cross-check it with developer feedback, and post your own summary
   via `just comment` — short verdict: what's good, what real issues remain (if any), and your decision.
4. If the code is ready — run `just approve {repo} {pr}` immediately.
5. If there are real blocking issues — do NOT approve, your summary comment is enough.
"""


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
            "--output-format", "text",
            "--allowedTools", "Bash(just *)",
            "--allowedTools", f"Read({settings.clone_dir}/*)",
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )

        async def _stream():
            assert proc.stdout
            async for line in proc.stdout:
                logger.info("[claude] %s", line.decode(errors="replace").rstrip())

        await asyncio.wait_for(
            asyncio.gather(_stream(), proc.wait()),
            timeout=600,
        )

        if proc.returncode != 0:
            logger.error("Claude exited with %d for %s#%d", proc.returncode, repo_full_name, pr_number)
        else:
            logger.info("Review completed for %s#%d", repo_full_name, pr_number)
    except asyncio.TimeoutError:
        logger.error("Claude timed out for %s#%d", repo_full_name, pr_number)
        proc.kill()
    except Exception:
        logger.exception("Review failed for %s#%d", repo_full_name, pr_number)


BOT_LOGIN = "gcs-fedor[bot]"

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

    if x_github_event == "pull_request" and action in ("opened", "synchronize"):
        repo = data["repository"]
        pr = data["pull_request"]
        trigger = "new PR" if action == "opened" else "new commits pushed"

        _schedule_pr(repo["full_name"], pr["number"], trigger)
        return {"status": "ok", "pr": pr["number"]}

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
