from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path


def backup_sqlite(database_url: str, backup_dir: Path) -> Path:
    if not database_url.startswith("sqlite:///"):
        raise ValueError("SQLite backup is only available for sqlite:/// databases")
    source = Path(database_url.removeprefix("sqlite:///"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    destination = backup_dir / f"{source.stem}-{stamp}{source.suffix or '.db'}"
    shutil.copy2(source, destination)
    return destination


def verify_backup(path: Path) -> bool:
    return path.exists() and path.stat().st_size > 0
