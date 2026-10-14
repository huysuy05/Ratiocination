"""Global (not per-user) settings stored in ``app_settings`` (SPEC §4). Owner: store.

Keys in use: ``scheduler_enabled`` (bool). Values are JSON; a missing key returns ``default``.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from radar.models import AppSetting


def get_setting(session: Session, key: str, default: Any = None) -> Any:
    """The stored value of ``key`` or ``default`` when the key was never set."""
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    return default if row is None else row.value_json


def set_setting(session: Session, key: str, value: Any) -> None:
    """Insert or replace ``key`` (write session)."""
    row = session.scalar(select(AppSetting).where(AppSetting.key == key))
    if row is None:
        session.add(AppSetting(key=key, value_json=value))
    elif row.value_json != value:
        row.value_json = value


__all__ = ["get_setting", "set_setting"]
