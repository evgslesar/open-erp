from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_name: str = "Open ERP"
    data_dir: Path = Path("data")
    backup_dir: Path = Path("backups")


def get_settings() -> Settings:
    database_url = os.getenv("OPENERP_DATABASE_URL", "sqlite:///data/open_erp.db")
    return Settings(database_url=database_url)
