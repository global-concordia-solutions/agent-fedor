import time

import httpx
import jwt
from github import Auth, Github

from .config import settings

GITHUB_API_URL = "https://api.github.com"


def _generate_jwt() -> str:
    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + 600,
        "iss": str(settings.github_app_id),
    }
    return jwt.encode(payload, settings.github_private_key, algorithm="RS256")


def get_installation_token(installation_id: int) -> str:
    token = _generate_jwt()
    resp = httpx.post(
        f"{GITHUB_API_URL}/app/installations/{installation_id}/access_tokens",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    return resp.json()["token"]


def get_installation_id(repo_full_name: str) -> int:
    token = _generate_jwt()
    resp = httpx.get(
        f"{GITHUB_API_URL}/repos/{repo_full_name}/installation",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    resp.raise_for_status()
    return resp.json()["id"]


def get_github_client(installation_id: int) -> Github:
    token = get_installation_token(installation_id)
    auth = Auth.Token(token)
    return Github(auth=auth)
