import hashlib
import hmac
import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request

from .config import settings
from .github_app import get_github_client, get_installation_token
from .reviewer import run_review

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Agent Fedor — PR Reviewer")


def _verify_signature(payload: bytes, signature: str) -> None:
    expected = "sha256=" + hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")


async def _handle_pr_review(
    repo_full_name: str,
    pr_number: int,
    clone_url: str,
    head_ref: str,
    installation_id: int,
) -> None:
    try:
        logger.info("Starting review for %s#%d", repo_full_name, pr_number)
        token = get_installation_token(installation_id)
        review_text = await run_review(clone_url, head_ref, token)

        gh = get_github_client(installation_id)
        repo = gh.get_repo(repo_full_name)
        pr = repo.get_pull(pr_number)
        pr.create_issue_comment(f"## Agent Fedor — Code Review\n\n{review_text}")
        logger.info("Posted review for %s#%d", repo_full_name, pr_number)
    except Exception:
        logger.exception("Review failed for %s#%d", repo_full_name, pr_number)


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: str = Header(...),
    x_github_event: str = Header(...),
) -> dict:
    payload = await request.body()
    _verify_signature(payload, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"status": "ignored", "reason": f"event: {x_github_event}"}

    data = await request.json()
    action = data.get("action")

    if action not in ("opened", "synchronize"):
        return {"status": "ignored", "reason": f"action: {action}"}

    pr = data["pull_request"]
    repo = data["repository"]
    installation_id = data["installation"]["id"]

    background_tasks.add_task(
        _handle_pr_review,
        repo_full_name=repo["full_name"],
        pr_number=pr["number"],
        clone_url=repo["clone_url"],
        head_ref=pr["head"]["ref"],
        installation_id=installation_id,
    )

    return {"status": "ok", "pr": pr["number"]}


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
