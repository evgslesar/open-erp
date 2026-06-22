from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_name: str = "Open ERP"
    data_dir: Path = Path("data")
    backup_dir: Path = Path("backups")
    secret_key: str = ""


def get_settings() -> Settings:
    database_url = os.getenv("OPENERP_DATABASE_URL", "sqlite:///data/open_erp.db")
    secret_key = os.getenv("OPENERP_SECRET_KEY") or _read_or_create_secret_key()
    return Settings(database_url=database_url, secret_key=secret_key)


def _read_or_create_secret_key() -> str:
    path = Path("data") / ".secret_key"
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_urlsafe(48)
    path.write_text(key, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return key
