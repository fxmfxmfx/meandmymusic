import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    library: Path = field(default_factory=lambda: Path(os.getenv("MUSIC_DIR", "data/music")).resolve())
    inbox: Path = field(default_factory=lambda: Path(os.getenv("INBOX_DIR", "data/inbox")).resolve())
    state: Path = field(default_factory=lambda: Path(os.getenv("STATE_DIR", "data/state")).resolve())
    password: str = field(default_factory=lambda: os.getenv("APP_PASSWORD", ""), repr=False)
    slskd_url: str = field(default_factory=lambda: os.getenv("SLSKD_URL", "http://localhost:5030").rstrip("/"))
    slskd_key: str = field(default_factory=lambda: os.getenv("SLSKD_API_KEY", ""), repr=False)
    navidrome_url: str = field(default_factory=lambda: os.getenv("NAVIDROME_URL", ""))
    music_ai_url: str = field(default_factory=lambda: os.getenv("MUSIC_AI_URL", "").rstrip("/"))
    music_ai_token: str = field(default_factory=lambda: os.getenv("MUSIC_AI_TOKEN", ""), repr=False)
    secure_cookie: bool = field(default_factory=lambda: os.getenv("COOKIE_SECURE", "false").lower() == "true")
    slskd_managed_auth: bool = field(default_factory=lambda: os.getenv("SLSKD_MANAGED_AUTH", "false").lower() == "true")
    slskd_admin_user: str = field(default_factory=lambda: os.getenv("SLSKD_ADMIN_USER", "archive"))
    slskd_admin_password: str = field(default_factory=lambda: os.getenv("SLSKD_ADMIN_PASSWORD", ""), repr=False)
    outbound_proxy: str = field(default_factory=lambda: os.getenv("OUTBOUND_PROXY", ""), repr=False)
    soulseek_login_timeout: int = field(default_factory=lambda: max(5, min(120, int(os.getenv("SOULSEEK_LOGIN_TIMEOUT", "45")))))

    def prepare(self):
        if len(self.password) < 12:
            raise RuntimeError("Set APP_PASSWORD to a unique password of at least 12 characters.")
        roots = [self.library, self.inbox, self.state]
        for i, path in enumerate(roots):
            for other in roots[i + 1:]:
                if path == other or path in other.parents or other in path.parents:
                    raise RuntimeError("MUSIC_DIR, INBOX_DIR and STATE_DIR must be separate, non-nested directories.")
            try:
                path.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryFile(dir=path, prefix=".mmm-check-"):
                    pass
            except OSError as error:
                name = {self.library: "MUSIC_DIR", self.inbox: "INBOX_DIR", self.state: "STATE_DIR"}[path]
                raise RuntimeError(f"Cannot write to {name} ({path}). Check bind mount, PUID/PGID and host directory ownership. No chmod/chown was applied to the music archive.") from error
        if self.slskd_key and not 16 <= len(self.slskd_key) <= 255:
            raise RuntimeError("SLSKD_API_KEY must be the raw 16–255 character key, without role= or semicolon options.")
