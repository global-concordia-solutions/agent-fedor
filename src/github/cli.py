import argparse
import asyncio
import json
import logging
import sys

from .github_app import get_github_client, get_installation_id, get_installation_token
from .graphql import get_review_threads, resolve_all_threads
from .reviewer import run_review

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _get_client(repo: str):
    installation_id = get_installation_id(repo)
    return get_github_client(installation_id), installation_id


def cmd_comment(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)
    pr.create_issue_comment(args.message)
    logger.info("Commented on %s#%d", args.repo, args.pr)


def cmd_review(args: argparse.Namespace) -> None:
    gh, installation_id = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)

    token = get_installation_token(installation_id)
    clone_url = repo.clone_url
    head_sha = pr.head.sha

    review_text = asyncio.run(run_review(clone_url, head_sha, token))
    print(review_text)


def cmd_comments(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)
    for c in pr.get_issue_comments():
        print(f"--- {c.user.login} ({c.created_at}) ---")
        print(c.body)
        print()


def cmd_approve(args: argparse.Namespace) -> None:
    _, installation_id = _get_client(args.repo)
    token = get_installation_token(installation_id)
    owner, repo_name = args.repo.split("/")

    resolved = resolve_all_threads(token, owner, repo_name, args.pr)
    if resolved:
        logger.info("Resolved %d threads before approving", resolved)

    gh = get_github_client(installation_id)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)

    kwargs = {"event": "APPROVE"}
    if args.message:
        kwargs["body"] = args.message
    pr.create_review(**kwargs)
    logger.info("Approved %s#%d", args.repo, args.pr)


def cmd_reply_comment(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)
    pr.create_review_comment_reply(args.comment_id, args.message)
    logger.info("Replied to comment %d on %s#%d", args.comment_id, args.repo, args.pr)


def cmd_review_comments(args: argparse.Namespace) -> None:
    _, installation_id = _get_client(args.repo)
    token = get_installation_token(installation_id)
    owner, repo_name = args.repo.split("/")

    threads = get_review_threads(token, owner, repo_name, args.pr)
    if not threads:
        print("No review threads found.")
        return

    for t in threads:
        status = "RESOLVED" if t.is_resolved else "UNRESOLVED"
        outdated = " (outdated)" if t.is_outdated else ""
        line_info = f":{t.line}" if t.line else ""
        print(f"=== {status}{outdated} {t.path}{line_info} ===")
        print(f"    Thread ID: {t.id}")
        for c in t.comments:
            print(f"  [{c.database_id}] @{c.author}:")
            for line in c.body.splitlines():
                print(f"    {line}")
        print()


def cmd_submit_review(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)

    review_data = json.loads(args.review_json)
    body = review_data.get("body", "")
    comments = review_data.get("comments", [])

    kwargs = {"event": args.event}
    if body:
        kwargs["body"] = body
    if comments:
        kwargs["comments"] = comments
    pr.create_review(**kwargs)
    logger.info("Submitted %s review on %s#%d", args.event, args.repo, args.pr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Fedor CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p_comment = sub.add_parser("comment", help="Post a comment on a PR")
    p_comment.add_argument("repo", help="Repository full name (owner/repo)")
    p_comment.add_argument("pr", type=int, help="PR number")
    p_comment.add_argument("message", help="Comment text")

    p_review = sub.add_parser("review", help="Run Claude review on a PR")
    p_review.add_argument("repo", help="Repository full name (owner/repo)")
    p_review.add_argument("pr", type=int, help="PR number")

    p_comments = sub.add_parser("comments", help="Show PR issue comments")
    p_comments.add_argument("repo", help="Repository full name (owner/repo)")
    p_comments.add_argument("pr", type=int, help="PR number")

    p_approve = sub.add_parser("approve", help="Approve a PR (resolves all threads first)")
    p_approve.add_argument("repo", help="Repository full name (owner/repo)")
    p_approve.add_argument("pr", type=int, help="PR number")
    p_approve.add_argument("message", nargs="?", default="", help="Optional approval message")

    p_reply = sub.add_parser("reply-comment", help="Reply to a review comment")
    p_reply.add_argument("repo", help="Repository full name (owner/repo)")
    p_reply.add_argument("pr", type=int, help="PR number")
    p_reply.add_argument("comment_id", type=int, help="Review comment database ID")
    p_reply.add_argument("message", help="Reply text")

    p_review_comments = sub.add_parser("review-comments", help="Show review threads")
    p_review_comments.add_argument("repo", help="Repository full name (owner/repo)")
    p_review_comments.add_argument("pr", type=int, help="PR number")

    p_submit = sub.add_parser("submit-review", help="Submit a review with inline comments")
    p_submit.add_argument("repo", help="Repository full name (owner/repo)")
    p_submit.add_argument("pr", type=int, help="PR number")
    p_submit.add_argument("event", choices=["COMMENT", "REQUEST_CHANGES"], help="Review event type")
    p_submit.add_argument("review_json", help='JSON: {"body": "...", "comments": [{"path": "...", "line": N, "body": "..."}]}')

    args = parser.parse_args()
    commands = {
        "comment": cmd_comment,
        "review": cmd_review,
        "comments": cmd_comments,
        "approve": cmd_approve,
        "reply-comment": cmd_reply_comment,
        "review-comments": cmd_review_comments,
        "submit-review": cmd_submit_review,
    }
    try:
        commands[args.command](args)
    except Exception:
        logger.exception("Command failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
