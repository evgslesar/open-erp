from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RequestContext:
    user_id: int | None
    organization_id: int
    is_admin: bool = False
