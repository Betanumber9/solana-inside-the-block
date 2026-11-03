"""Settings from the environment (.env via docker compose). Nothing secret lives here."""
import os
import pathlib
from dataclasses import dataclass, field


def _load_dotenv(path=".env"):
    """Outside Docker, read KEY=VALUE lines from .env (compose does this itself). Real env vars win."""
    p = pathlib.Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"'))


_load_dotenv()


def _env(name, default=None):
    v = os.environ.get(name)
    return v if v not in (None, "") else default


@dataclass
class Config:
    rpc_url: str = field(default_factory=lambda: _env("ITB_RPC_URL", "https://api.mainnet-beta.solana.com"))
    ws_url: str = field(default_factory=lambda: _env("ITB_WS_URL", ""))
    # where sendTransaction goes; defaults to the read RPC. Recorded with every transaction.
    send_url: str = field(default_factory=lambda: _env("ITB_SEND_URL", ""))
    # seconds between read calls (public mainnet RPC allows ~10/s per IP)
    rpc_interval: float = field(default_factory=lambda: float(_env("ITB_RPC_INTERVAL", "0.12")))
    # seconds between getBlock calls (public RPC: ~1.5; a keyed provider: 0.2)
    block_interval: float = field(default_factory=lambda: float(_env("ITB_BLOCK_INTERVAL", "1.5")))
    data_dir: str = field(default_factory=lambda: _env("ITB_DATA_DIR", "data"))
    secrets_dir: str = field(default_factory=lambda: _env("ITB_SECRETS_DIR", "secrets"))

    def __post_init__(self):
        if not self.ws_url:
            self.ws_url = self.rpc_url.replace("https://", "wss://").replace("http://", "ws://")
        if not self.send_url:
            self.send_url = self.rpc_url

    def public(self):
        """Endpoints for logs, with any api-key query string removed."""
        strip = lambda u: u.split("?")[0]
        return {"rpc": strip(self.rpc_url), "ws": strip(self.ws_url), "send": strip(self.send_url)}
