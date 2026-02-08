import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    github_app_id: int = field(
        default_factory=lambda: int(os.environ.get("GITHUB_APP_ID", "2822626"))
    )
    github_private_key_path: Path = field(
        default_factory=lambda: Path(
            os.environ.get("GITHUB_PRIVATE_KEY_PATH", "./gcs-fedor.pem")
        )
    )
    github_webhook_secret: str = field(
        default_factory=lambda: os.environ.get("GITHUB_WEBHOOK_SECRET", "")
    )
    claude_command: str = field(
        default_factory=lambda: os.environ.get("CLAUDE_COMMAND", "claude")
    )
    clone_dir: Path = field(
        default_factory=lambda: Path(
            os.environ.get("CLONE_DIR", "/tmp/agent-fedor-repos")
        )
    )
    dummy_mode: bool = field(
        default_factory=lambda: os.environ.get("DUMMY_MODE", "").lower() in ("1", "true", "yes")
    )
    webhook_delay: int = field(
        default_factory=lambda: int(os.environ.get("WEBHOOK_DELAY", "300"))
    )

    @property
    def github_private_key(self) -> str:
        return self.github_private_key_path.read_text()


settings = Settings()
