import asyncio
import hashlib
import hmac
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

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
  just review {repo} {pr}              — clone the PR, run code review, and post it as a comment
  just comment {repo} {pr} "<message>" — post a comment on the PR
  just approve {repo} {pr}             — approve the PR

Rules:
- Comments from "gcs-fedor[bot]" are YOUR OWN previous comments — do not treat them as developer feedback.
- Comments from anyone else are developer feedback — respect their decisions.
- If the developer dismissed a concern as intentional or out-of-scope, do NOT repeat it.

Your task:
1. Run `just comments {repo} {pr}` to read existing comments and developer feedback.
2. Decide what to do based on the trigger and context:
   - If this is a new PR or new commits — run `just review {repo} {pr}`, then check the result.
   - If this is a developer comment — read what they asked for and respond appropriately
     (answer questions, re-review if asked, approve if they resolved issues, etc.)
3. If the code looks good and no real issues remain — run `just approve {repo} {pr}`.
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
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)

        if proc.returncode != 0:
            logger.error(
                "Claude exited with %d for %s#%d: %s",
                proc.returncode, repo_full_name, pr_number,
                stderr.decode(errors="replace"),
            )
        else:
            logger.info(
                "Review completed for %s#%d: %s",
                repo_full_name, pr_number,
                stdout.decode(errors="replace")[:200],
            )
    except asyncio.TimeoutError:
        logger.error("Claude timed out for %s#%d", repo_full_name, pr_number)
        proc.kill()
    except Exception:
        logger.exception("Review failed for %s#%d", repo_full_name, pr_number)


BOT_LOGIN = "gcs-fedor[bot]"


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
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

        background_tasks.add_task(
            _handle_pr,
            repo_full_name=repo["full_name"],
            pr_number=pr["number"],
            trigger=trigger,
        )
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
        background_tasks.add_task(
            _handle_pr,
            repo_full_name=repo["full_name"],
            pr_number=issue["number"],
            trigger=f"comment from @{sender}",
        )
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
