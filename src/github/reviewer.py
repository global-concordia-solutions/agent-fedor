import asyncio
import logging
import shutil
from pathlib import Path

from .config import settings

logger = logging.getLogger(__name__)


async def run_review(
    clone_url: str, head_sha: str, token: str
) -> str:
    if settings.dummy_mode:
        logger.info("Dummy mode enabled, skipping real review")
        return "Ponnggg"

    settings.clone_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = settings.clone_dir / head_sha[:12]
    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    try:
        authenticated_url = clone_url.replace(
            "https://", f"https://x-access-token:{token}@"
        )
        await _clone_repo(authenticated_url, head_sha, repo_dir)
        return await _run_claude(repo_dir)
    finally:
        shutil.rmtree(repo_dir, ignore_errors=True)


async def _clone_repo(url: str, sha: str, dest: Path) -> None:
    # Clone without checking out, then checkout the exact commit
    proc = await asyncio.create_subprocess_exec(
        "git", "clone", "--no-checkout", url, str(dest),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git clone failed: {stderr.decode()}")

    proc = await asyncio.create_subprocess_exec(
        "git", "-C", str(dest), "checkout", sha,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"git checkout {sha} failed: {stderr.decode()}")


async def _run_claude(repo_dir: Path) -> str:
    prompt = (
        "Review this repository code as a pull request review. "
        "Provide a concise code review with actionable feedback. "
        "Focus on bugs, security issues, performance problems, and code quality."
    )

    proc = await asyncio.create_subprocess_exec(
        settings.claude_command, "-p", prompt, "--output-format", "text",
        cwd=str(repo_dir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)

    if proc.returncode != 0:
        logger.error("claude failed: %s", stderr.decode())
        raise RuntimeError(f"Claude CLI failed: {stderr.decode()}")

    return stdout.decode()
