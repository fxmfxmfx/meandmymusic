import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    library: Path = field(default_factory=lambda: Path(os.getenv("MUSIC_DIR", "data/music")).resolve())
    inbox: Path = field(default_factory=lambda: Path(os.getenv("INBOX_DIR", "data/inbox")).resolve())
    state: Path = field(default_factory=lambda: Path(os.getenv("STATE_DIR", "data/state")).resolve())
    password: str = field(default_factory=lambda: os.getenv("APP_PASSWORD", ""))
    slskd_url: str = field(default_factory=lambda: os.getenv("SLSKD_URL", "http://localhost:5030").rstrip("/"))
    slskd_key: str = field(default_factory=lambda: os.getenv("SLSKD_API_KEY", ""))
    navidrome_url: str = field(default_factory=lambda: os.getenv("NAVIDROME_URL", ""))
    secure_cookie: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")

    def prepare(self):
        if len(self.password) < 12:
            raise RuntimeError("Set APP_PASSWORD to a unique password of at least 12 characters.")
        roots = [self.library, self.inbox, self.state]
        for i, path in enumerate(roots):
            for other in roots[i + 1:]:
                if path == other or path in other.parents or other in path.parents:
                    raise RuntimeError("MUSIC_DIR, INBOX_DIR and STATE_DIR must be separate, non-nested directories.")
            path.mkdir(parents=True, exist_ok=True)
