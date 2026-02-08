import argparse
import asyncio
import logging
import sys

from .github_app import get_github_client, get_installation_id, get_installation_token
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
    pr.create_issue_comment(f"## Agent Fedor — Code Review\n\n{review_text}")
    logger.info("Posted review for %s#%d", args.repo, args.pr)


def cmd_comments(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)
    for c in pr.get_issue_comments():
        print(f"--- {c.user.login} ({c.created_at}) ---")
        print(c.body)
        print()


def cmd_approve(args: argparse.Namespace) -> None:
    gh, _ = _get_client(args.repo)
    repo = gh.get_repo(args.repo)
    pr = repo.get_pull(args.pr)
    pr.create_review(event="APPROVE")
    logger.info("Approved %s#%d", args.repo, args.pr)


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

    p_comments = sub.add_parser("comments", help="Show PR comments")
    p_comments.add_argument("repo", help="Repository full name (owner/repo)")
    p_comments.add_argument("pr", type=int, help="PR number")

    p_approve = sub.add_parser("approve", help="Approve a PR")
    p_approve.add_argument("repo", help="Repository full name (owner/repo)")
    p_approve.add_argument("pr", type=int, help="PR number")

    args = parser.parse_args()
    commands = {
        "comment": cmd_comment,
        "review": cmd_review,
        "comments": cmd_comments,
        "approve": cmd_approve,
    }
    try:
        commands[args.command](args)
    except Exception:
        logger.exception("Command failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
