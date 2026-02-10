import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


@dataclass
class ThreadComment:
    database_id: int
    author: str
    body: str


@dataclass
class ReviewThread:
    id: str
    is_resolved: bool
    is_outdated: bool
    path: str
    line: int | None
    comments: list[ThreadComment]


def _graphql(token: str, query: str, variables: dict | None = None) -> dict:
    resp = httpx.post(
        GITHUB_GRAPHQL_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
        json={"query": query, "variables": variables or {}},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if "errors" in data:
        raise RuntimeError(f"GraphQL errors: {data['errors']}")
    return data["data"]


REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100) {
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          comments(first: 100) {
            nodes {
              databaseId
              author { login }
              body
            }
          }
        }
      }
    }
  }
}
"""


def get_review_threads(
    token: str, owner: str, repo: str, pr: int
) -> list[ReviewThread]:
    data = _graphql(token, REVIEW_THREADS_QUERY, {
        "owner": owner, "repo": repo, "pr": pr,
    })
    threads = []
    for node in data["repository"]["pullRequest"]["reviewThreads"]["nodes"]:
        comments = [
            ThreadComment(
                database_id=c["databaseId"],
                author=c["author"]["login"] if c["author"] else "ghost",
                body=c["body"],
            )
            for c in node["comments"]["nodes"]
        ]
        threads.append(ReviewThread(
            id=node["id"],
            is_resolved=node["isResolved"],
            is_outdated=node["isOutdated"],
            path=node["path"],
            line=node["line"],
            comments=comments,
        ))
    return threads


RESOLVE_THREAD_MUTATION = """
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread { isResolved }
  }
}
"""


def resolve_all_threads(token: str, owner: str, repo: str, pr: int) -> int:
    threads = get_review_threads(token, owner, repo, pr)
    resolved = 0
    for t in threads:
        if not t.is_resolved:
            _graphql(token, RESOLVE_THREAD_MUTATION, {"threadId": t.id})
            resolved += 1
    if resolved:
        logger.info("Resolved %d review threads on %s/%s#%d", resolved, owner, repo, pr)
    return resolved
