"""Cost ledger and daily budgets (SPEC §6, D7). Owner: net.

* :func:`record_call` enqueues one ``cost_ledger`` row on the background writer
  (``radar.db.enqueue_write``), so the caller's thread never writes. The endpoint is stored as a path
  only (scheme, host, query string and fragment are dropped) and ``meta_json`` keeps finite numbers
  only (for OpenAlex: ``limit_usd``, ``remaining_usd``, ``remaining_requests``, ``reset_seconds``).
* :func:`spent_today`, :func:`budget_status` and :func:`check_budget` read the rows of the current UTC
  day (00:00 UTC to 24:00 UTC), after waiting for queued ledger writes unless the calling thread holds a
  write transaction (the writer could not commit until it ends).

OpenAlex is the only metered provider (D7: lookup $0, list $0.0001, search $0.001, content $0.01).
It is **stopped** for the rest of the UTC day when

* the latest OpenAlex row of the day that carries rate-limit headers reports
  ``(limit_usd - remaining_usd) / limit_usd >= stop_fraction`` or no USD left, or
  ``remaining_requests < 20``; or
* the ledger's spend today is ``>= stop_fraction * daily budget`` (``RADAR_OPENALEX_DAILY_BUDGET_USD``,
  else $1.00 with ``OPENALEX_API_KEY`` and $0.10 without).

``BudgetStatus.source`` is ``"headers"`` when such a header row (one with ``remaining_usd``) exists
today, else ``"ledger"``. Comparisons allow 1e-9 of floating-point slack so that exactly 80 % stops.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from radar import db
from radar.config import get_settings
from radar.models import CostLedger

log = logging.getLogger(__name__)

PROVIDERS: tuple[str, ...] = ("arxiv", "openalex", "semantic_scholar", "google", "github", "anthropic")
"""``cost_ledger.provider`` values (SPEC §4)."""

CALL_TYPES: tuple[str, ...] = (
    "feed",
    "query",
    "lookup",
    "list",
    "search",
    "content",
    "batch",
    "auth",
    "generate",
)
"""``cost_ledger.call_type`` values (SPEC §4)."""

METERED_PROVIDERS: frozenset[str] = frozenset({"openalex"})
"""Providers with a daily USD budget (D7)."""

OPENALEX_UNIT_COSTS: dict[str, float] = {"lookup": 0.0, "list": 0.0001, "search": 0.001, "content": 0.01}
"""OpenAlex USD per call by call_type (D7)."""

META_KEYS: tuple[str, ...] = ("limit_usd", "remaining_usd", "remaining_requests", "reset_seconds")
"""``meta_json`` keys of an OpenAlex row (parsed from ``x-ratelimit-*`` response headers)."""

MIN_REMAINING_REQUESTS = 20
"""OpenAlex stops when the latest header row reports fewer remaining requests than this."""

_EPS = 1e-9
_FLUSH_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class BudgetStatus:
    """Budget state of one provider (mirrors API ``BudgetInfo``)."""

    provider: str
    daily_budget_usd: float
    spent_today_usd: float
    remaining_today_usd: float | None
    stop_fraction: float
    stopped: bool
    source: Literal["headers", "ledger"]


# ------------------------------------------------------------------------------------------------
# Small pure helpers
# ------------------------------------------------------------------------------------------------


def _utcnow() -> datetime:
    """Current aware UTC time (tests monkeypatch this to move across a UTC day boundary)."""
    return datetime.now(UTC)


def utc_day_bounds(now: datetime | None = None) -> tuple[datetime, datetime]:
    """``(start, end)`` of the UTC day containing ``now`` (default: the current time)."""
    current = (now or _utcnow()).astimezone(UTC)
    start = current.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def unit_cost_for(provider: str, call_type: str) -> float:
    """USD per call for ``provider``/``call_type`` (only OpenAlex is metered; unknown types cost 0)."""
    if provider in METERED_PROVIDERS:
        return OPENALEX_UNIT_COSTS.get(call_type, 0.0)
    return 0.0


def endpoint_path(endpoint: str) -> str:
    """The path part of ``endpoint`` (a path or a URL): no scheme, host, query string or fragment."""
    text = str(endpoint or "").strip()
    if "://" in text or text.startswith("//"):
        try:
            text = urlsplit(text).path
        except ValueError:
            text = ""
    text = text.split("?", 1)[0].split("#", 1)[0]
    return text or "/"


def _number(value: Any) -> float | None:
    """A finite real number as float (bools and everything else -> ``None``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _clean_meta(meta: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep finite numbers and ``None`` only: the ledger never stores free text."""
    clean: dict[str, Any] = {}
    for key, value in (meta or {}).items():
        if value is None or _number(value) is not None:
            clean[str(key)] = value
    return clean


def _money(value: float) -> float:
    return round(value, 6) + 0.0  # "+ 0.0" turns -0.0 into 0.0


def _check_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"unknown provider {provider!r}")


def evaluate_budget(
    provider: str,
    *,
    daily_budget_usd: float,
    stop_fraction: float,
    ledger_spent_usd: float,
    header_meta: Mapping[str, Any] | None,
) -> BudgetStatus:
    """Budget state of a metered provider from the ledger spend today and the latest header row (pure).

    ``header_meta`` is the ``meta_json`` of the latest row of the day carrying rate-limit values, or
    ``None``. When it has ``remaining_usd`` the header rule applies (``limit_usd`` falls back to the
    daily budget when missing or not positive) and ``source`` is ``"headers"``.
    """
    budget = max(0.0, float(daily_budget_usd))
    spent = max(0.0, float(ledger_spent_usd))
    meta = header_meta or {}
    remaining_usd = _number(meta.get("remaining_usd"))
    limit_usd = _number(meta.get("limit_usd"))
    remaining_requests = _number(meta.get("remaining_requests"))

    stopped = spent >= stop_fraction * budget - _EPS
    if remaining_requests is not None and remaining_requests < MIN_REMAINING_REQUESTS:
        stopped = True

    if remaining_usd is None:
        return BudgetStatus(
            provider=provider,
            daily_budget_usd=budget,
            spent_today_usd=_money(spent),
            remaining_today_usd=_money(max(0.0, budget - spent)),
            stop_fraction=stop_fraction,
            stopped=stopped,
            source="ledger",
        )

    limit = limit_usd if limit_usd is not None and limit_usd > 0 else budget
    header_spent = max(0.0, limit - remaining_usd)
    if limit <= 0 or remaining_usd <= 0 or header_spent / limit >= stop_fraction - _EPS:
        stopped = True
    spent_out = max(spent, header_spent)
    return BudgetStatus(
        provider=provider,
        daily_budget_usd=budget,
        spent_today_usd=_money(spent_out),
        remaining_today_usd=_money(max(0.0, min(remaining_usd, budget - spent_out))),
        stop_fraction=stop_fraction,
        stopped=stopped,
        source="headers",
    )


# ------------------------------------------------------------------------------------------------
# Writing
# ------------------------------------------------------------------------------------------------


def record_call(
    *,
    provider: str,
    call_type: str,
    endpoint: str,
    status_code: int | None,
    units: float = 1.0,
    unit_cost: float | None = None,
    cost_usd: float | None = None,
    job_id: int | None = None,
    project_id: int | None = None,
    meta: dict[str, Any] | None = None,
) -> None:
    """Enqueue one ``cost_ledger`` row through the background writer (endpoint = path only).

    ``unit_cost`` defaults to the provider's rate for ``call_type`` and ``cost_usd`` to
    ``units * unit_cost``. The timestamp is taken now, in the caller's thread.
    """
    _check_provider(provider)
    if call_type not in CALL_TYPES:
        raise ValueError(f"unknown call_type {call_type!r}")
    units_value = _number(units)
    if units_value is None or units_value < 0:
        raise ValueError("units must be a finite, non-negative number")
    rate = unit_cost_for(provider, call_type) if unit_cost is None else _number(unit_cost)
    if rate is None:
        raise ValueError("unit_cost must be a finite number")
    cost = units_value * rate if cost_usd is None else _number(cost_usd)
    if cost is None:
        raise ValueError("cost_usd must be a finite number")
    values: dict[str, Any] = {
        "timestamp": _utcnow(),
        "provider": provider,
        "call_type": call_type,
        "endpoint": endpoint_path(endpoint),
        "status_code": int(status_code) if status_code is not None else None,
        "units": units_value,
        "unit_cost": rate,
        "cost_usd": cost,
        "project_id": project_id,
        "job_id": job_id,
        "meta_json": _clean_meta(meta),
    }

    def _write(session: Session) -> None:
        session.add(CostLedger(**values))

    db.enqueue_write(_write)


def flush(timeout: float = 5.0) -> None:
    """Wait until queued ledger rows are written (``radar.db.flush_writes``)."""
    db.flush_writes(timeout)


def _flush_pending() -> None:
    # The background writer cannot commit while this thread holds a write transaction (it would only
    # time out), so in that case read what is already committed.
    if not db.holding_write_transaction():
        db.flush_writes(_FLUSH_TIMEOUT_S)


# ------------------------------------------------------------------------------------------------
# Reading
# ------------------------------------------------------------------------------------------------


def _sum_cost(session: Session, provider: str, start: datetime, end: datetime) -> float:
    total = session.scalar(
        select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
            CostLedger.provider == provider,
            CostLedger.timestamp >= start,
            CostLedger.timestamp < end,
        )
    )
    return float(total or 0.0)


def _latest_rate_limit_meta(
    session: Session, provider: str, start: datetime, end: datetime
) -> dict[str, Any] | None:
    """``meta_json`` of the newest row of the day that carries any rate-limit value.

    JSON contents are never filtered in SQL (CONVENTIONS), so rows are scanned newest first.
    """
    stmt = (
        select(CostLedger.meta_json)
        .where(
            CostLedger.provider == provider,
            CostLedger.timestamp >= start,
            CostLedger.timestamp < end,
        )
        .order_by(CostLedger.timestamp.desc(), CostLedger.id.desc())
        .execution_options(yield_per=100)
    )
    result = session.execute(stmt)
    try:
        for (meta,) in result:
            if isinstance(meta, dict) and any(_number(meta.get(key)) is not None for key in META_KEYS):
                return dict(meta)
    finally:
        result.close()
    return None


def spent_today(provider: str) -> float:
    """USD recorded for ``provider`` since 00:00 UTC today."""
    _check_provider(provider)
    _flush_pending()
    start, end = utc_day_bounds()
    with db.session_scope() as session:
        return _sum_cost(session, provider, start, end)


def budget_status(provider: str) -> BudgetStatus:
    """Budget state for ``provider`` (headers of the latest OpenAlex row today, else the ledger).

    Unmetered providers report a zero budget, ``remaining_today_usd=None`` and are never stopped.
    """
    _check_provider(provider)
    settings = get_settings()
    stop_fraction = float(settings.budget_stop_fraction)
    _flush_pending()
    start, end = utc_day_bounds()
    metered = provider in METERED_PROVIDERS
    with db.session_scope() as session:
        spent = _sum_cost(session, provider, start, end)
        header_meta = _latest_rate_limit_meta(session, provider, start, end) if metered else None
    if not metered:
        return BudgetStatus(
            provider=provider,
            daily_budget_usd=0.0,
            spent_today_usd=_money(spent),
            remaining_today_usd=None,
            stop_fraction=stop_fraction,
            stopped=False,
            source="ledger",
        )
    return evaluate_budget(
        provider,
        daily_budget_usd=float(settings.openalex_daily_budget_usd),
        stop_fraction=stop_fraction,
        ledger_spent_usd=spent,
        header_meta=header_meta,
    )


def check_budget(provider: str) -> None:
    """Raise ``radar.net.http.BudgetExceeded`` when ``provider`` is stopped for today."""
    _check_provider(provider)
    if provider not in METERED_PROVIDERS:
        return
    status = budget_status(provider)
    if status.stopped:
        from radar.net.http import BudgetExceeded  # radar.net.http imports this module at load time

        log.debug("%s budget stop in effect (source=%s)", provider, status.source)
        raise BudgetExceeded(provider, f"{provider} daily budget reached; calls resume after 00:00 UTC")
