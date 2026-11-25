"""Caminhos e configuracoes. Valores sensiveis vem de variaveis de ambiente / arquivo .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = Path(os.environ.get("ECONOMIX_DATA", ROOT / "data"))
    markets_file: Path = ROOT / "config" / "markets.yaml"
    flows_dir: Path = ROOT / "config" / "flows"
    android_home: Path = Path(os.environ.get("ANDROID_HOME", r"C:\Android\Sdk"))
    device_serial: str | None = os.environ.get("ECONOMIX_DEVICE") or None
    telegram_token: str | None = os.environ.get("TELEGRAM_BOT_TOKEN") or None
    telegram_chat_id: str | None = os.environ.get("TELEGRAM_CHAT_ID") or None

    @property
    def db_path(self) -> Path:
        return self.data_dir / "economix.db"

    @property
    def evidence_dir(self) -> Path:
        return self.data_dir / "evidence"

    @property
    def exploration_dir(self) -> Path:
        return self.data_dir / "exploration"

    @property
    def adb(self) -> str:
        return str(self.android_home / "platform-tools" / "adb.exe")


settings = Settings()
