"""radar.net.ledger (SPEC §6, D7): ledger rows through the background writer, today's spend, budget
status from headers or ledger sums, the UTC-day reset and check_budget."""

from __future__ import annotations

import dataclasses
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from radar import db, models
from radar.net import ledger
from radar.net.http import BudgetExceeded
from radar.net.ledger import BudgetStatus
from radar.schemas import BudgetInfo

NOW = datetime(2026, 10, 12, 15, 30, tzinfo=UTC)
HEADERS_OK = {"limit_usd": 0.1, "remaining_usd": 0.095, "remaining_requests": 950, "reset_seconds": 30600}


class FrozenTime:
    """Replaces ``ledger._utcnow``: ledger timestamps and the 'current UTC day' follow ``now``."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> FrozenTime:
    clock = FrozenTime(NOW)
    monkeypatch.setattr(ledger, "_utcnow", clock)
    return clock


def rows() -> list[models.CostLedger]:
    ledger.flush()
    with db.session_scope() as session:
        return list(session.scalars(select(models.CostLedger).order_by(models.CostLedger.id)))


def record(
    provider: str = "openalex", call_type: str = "list", status: int | None = 200, **kwargs: Any
) -> None:
    ledger.record_call(
        provider=provider, call_type=call_type, endpoint="/works", status_code=status, **kwargs
    )


def insert_row(
    timestamp: datetime, cost_usd: float, provider: str = "openalex", meta: dict[str, Any] | None = None
) -> None:
    with db.session_scope(write=True) as session:
        session.add(
            models.CostLedger(
                timestamp=timestamp,
                provider=provider,
                call_type="list",
                endpoint="/works",
                status_code=200,
                units=1.0,
                unit_cost=cost_usd,
                cost_usd=cost_usd,
                meta_json=meta or {},
            )
        )


# ------------------------------------------------------------------------------------------------
# record_call
# ------------------------------------------------------------------------------------------------


def test_record_call_stores_path_only_cost_and_numeric_meta(frozen: FrozenTime) -> None:
    ledger.record_call(
        provider="openalex",
        call_type="list",
        endpoint="https://api.openalex.org/works?filter=cites:W1&api_key=zqsecretkey9#frag",
        status_code=200,
        job_id=3,
        project_id=4,
        meta={
            **HEADERS_OK,
            "note": "free text is dropped",
            "flag": True,
            "nested": {"a": 1},
            "bad": float("nan"),
        },
    )
    ledger.record_call(
        provider="arxiv", call_type="query", endpoint="/api/query?id_list=2504.08385", status_code=None
    )
    first, second = rows()
    assert (first.provider, first.call_type, first.endpoint, first.status_code) == (
        "openalex",
        "list",
        "/works",
        200,
    )
    assert (first.units, first.unit_cost, first.cost_usd) == (1.0, 0.0001, 0.0001)
    assert (first.job_id, first.project_id) == (3, 4)
    assert first.meta_json == HEADERS_OK
    assert first.timestamp == NOW
    assert (second.endpoint, second.status_code, second.cost_usd, second.meta_json) == (
        "/api/query",
        None,
        0.0,
        {},
    )


def test_unit_costs_and_explicit_costs() -> None:
    assert [ledger.unit_cost_for("openalex", t) for t in ("lookup", "list", "search", "content")] == [
        0.0,
        0.0001,
        0.001,
        0.01,
    ]
    assert ledger.unit_cost_for("openalex", "batch") == 0.0
    assert ledger.unit_cost_for("github", "auth") == 0.0
    assert ledger.unit_cost_for("anthropic", "generate") == 0.0
    assert ledger.unit_cost_for("semantic_scholar", "batch") == 0.0
    record(status=429, cost_usd=0.0)
    record(units=3)
    record(call_type="lookup")
    assert [(r.units, r.unit_cost, r.cost_usd) for r in rows()] == [
        (1.0, 0.0001, 0.0),
        (3.0, 0.0001, pytest.approx(0.0003)),
        (1.0, 0.0, 0.0),
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "s2"},
        {"call_type": "fetch"},
        {"units": -1},
        {"units": float("nan")},
        {"unit_cost": float("inf")},
        {"cost_usd": float("nan")},
    ],
)
def test_record_call_rejects_values_outside_the_contract(overrides: dict[str, Any]) -> None:
    values: dict[str, Any] = {
        "provider": "openalex",
        "call_type": "lookup",
        "endpoint": "/works/W1",
        "status_code": 200,
    }
    with pytest.raises(ValueError):
        ledger.record_call(**{**values, **overrides})
    assert rows() == []


def test_record_call_never_writes_from_the_callers_thread(assert_no_open_write: Callable[[], None]) -> None:
    with db.session_scope(write=True) as session:  # strict mode: writing here from the ledger would raise
        session.execute(select(models.User.id)).all()
        record()
        status = ledger.budget_status("openalex")  # reads committed data; does not wait for the writer
        assert status.source == "ledger"
    assert_no_open_write()
    assert len(rows()) == 1


@pytest.mark.parametrize(
    ("endpoint", "path"),
    [
        ("/works/W1", "/works/W1"),
        ("/works?filter=cites:W1", "/works"),
        ("https://api.github.com/user/emails?per_page=zq@example.invalid", "/user/emails"),
        ("//api.openalex.org/works#x", "/works"),
        ("", "/"),
    ],
)
def test_endpoint_path_drops_host_query_and_fragment(endpoint: str, path: str) -> None:
    assert ledger.endpoint_path(endpoint) == path


# ------------------------------------------------------------------------------------------------
# Today's spend and budget status
# ------------------------------------------------------------------------------------------------


def test_spent_today_counts_only_the_current_utc_day(frozen: FrozenTime) -> None:
    start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
    insert_row(start - timedelta(microseconds=1), 0.5)  # yesterday
    insert_row(start, 0.0002)
    insert_row(start + timedelta(days=1), 0.7)  # tomorrow (never counted)
    insert_row(NOW, 1.0, provider="github")
    record()  # 0.0001, through the background writer
    assert ledger.spent_today("openalex") == pytest.approx(0.0003)
    assert ledger.spent_today("github") == pytest.approx(1.0)
    assert ledger.spent_today("arxiv") == 0.0
    with pytest.raises(ValueError):
        ledger.spent_today("s2")


def test_budget_status_defaults_and_settings(set_env: Callable[..., None]) -> None:
    assert ledger.budget_status("openalex") == BudgetStatus(
        provider="openalex",
        daily_budget_usd=0.1,
        spent_today_usd=0.0,
        remaining_today_usd=0.1,
        stop_fraction=0.8,
        stopped=False,
        source="ledger",
    )
    set_env(OPENALEX_API_KEY="zq-key")
    assert ledger.budget_status("openalex").daily_budget_usd == 1.0
    set_env(RADAR_OPENALEX_DAILY_BUDGET_USD="0.5", RADAR_BUDGET_STOP_FRACTION="0.5")
    status = ledger.budget_status("openalex")
    assert (status.daily_budget_usd, status.stop_fraction) == (0.5, 0.5)
    BudgetInfo(**dataclasses.asdict(status))  # maps 1:1 onto the API model


def test_unmetered_providers_are_never_stopped() -> None:
    record(provider="github", call_type="auth", cost_usd=2.0)
    for provider in ("arxiv", "github", "semantic_scholar", "google", "anthropic"):
        ledger.check_budget(provider)
        status = ledger.budget_status(provider)
        assert (status.daily_budget_usd, status.remaining_today_usd, status.stopped, status.source) == (
            0.0,
            None,
            False,
            "ledger",
        )
    assert ledger.budget_status("github").spent_today_usd == 2.0
    with pytest.raises(ValueError):
        ledger.check_budget("s2")


def test_ledger_spend_stops_openalex_at_the_stop_fraction(
    frozen: FrozenTime, set_env: Callable[..., None]
) -> None:
    set_env(RADAR_OPENALEX_DAILY_BUDGET_USD="0.001")  # stop at $0.0008 = eight list calls
    for _ in range(7):
        record()
    status = ledger.budget_status("openalex")
    assert (status.stopped, status.source) == (False, "ledger")
    assert status.spent_today_usd == pytest.approx(0.0007)
    assert status.remaining_today_usd == pytest.approx(0.0003)
    ledger.check_budget("openalex")
    record()
    status = ledger.budget_status("openalex")
    assert status.stopped and status.spent_today_usd == pytest.approx(0.0008)
    with pytest.raises(BudgetExceeded) as info:
        ledger.check_budget("openalex")
    assert info.value.provider == "openalex"
    assert str(info.value) == "openalex daily budget reached; calls resume after 00:00 UTC"


def test_latest_header_row_of_the_day_drives_the_budget(frozen: FrozenTime) -> None:
    frozen.now = NOW - timedelta(hours=2)
    record(meta={**HEADERS_OK, "remaining_usd": 0.01})  # 90 % used two hours ago
    frozen.now = NOW - timedelta(hours=1)
    record(meta=HEADERS_OK)  # 5 % used now (the provider's window was reset)
    frozen.now = NOW
    record(status=None, cost_usd=0.0)  # a connection error carries no headers
    status = ledger.budget_status("openalex")
    assert (status.source, status.stopped) == ("headers", False)
    assert status.spent_today_usd == pytest.approx(0.005)
    assert status.remaining_today_usd == pytest.approx(0.095)
    ledger.check_budget("openalex")

    record(meta={**HEADERS_OK, "remaining_usd": 0.02})  # exactly 80 % of the provider's limit
    status = ledger.budget_status("openalex")
    assert (status.source, status.stopped) == ("headers", True)
    assert status.spent_today_usd == pytest.approx(0.08)
    with pytest.raises(BudgetExceeded):
        ledger.check_budget("openalex")


def test_just_below_the_header_stop_keeps_running(frozen: FrozenTime) -> None:
    record(meta={**HEADERS_OK, "remaining_usd": 0.0201})
    assert not ledger.budget_status("openalex").stopped


def test_few_remaining_requests_stop_openalex(frozen: FrozenTime) -> None:
    record(meta={**HEADERS_OK, "remaining_requests": 20})
    assert not ledger.budget_status("openalex").stopped
    record(meta={**HEADERS_OK, "remaining_requests": 19})
    assert ledger.budget_status("openalex").stopped
    with pytest.raises(BudgetExceeded):
        ledger.check_budget("openalex")


def test_the_stop_resets_at_the_next_utc_day(frozen: FrozenTime, set_env: Callable[..., None]) -> None:
    set_env(RADAR_OPENALEX_DAILY_BUDGET_USD="0.0001")
    record(meta={**HEADERS_OK, "remaining_usd": 0.0})
    record()
    assert ledger.budget_status("openalex").stopped
    frozen.now = (NOW + timedelta(days=1)).replace(hour=0, minute=0, second=1)
    status = ledger.budget_status("openalex")
    assert status == BudgetStatus("openalex", 0.0001, 0.0, 0.0001, 0.8, False, "ledger")
    assert ledger.spent_today("openalex") == 0.0
    ledger.check_budget("openalex")


def test_zero_budget_is_always_stopped(set_env: Callable[..., None]) -> None:
    set_env(RADAR_OPENALEX_DAILY_BUDGET_USD="0")
    assert ledger.budget_status("openalex").stopped
    with pytest.raises(BudgetExceeded):
        ledger.check_budget("openalex")


@pytest.mark.parametrize(
    ("meta", "stopped", "source", "spent", "remaining"),
    [
        (None, False, "ledger", 0.0, 0.1),
        ({}, False, "ledger", 0.0, 0.1),
        ({"remaining_requests": 5}, True, "ledger", 0.0, 0.1),
        ({"remaining_usd": 0.05}, False, "headers", 0.05, 0.05),  # no limit header: the budget is the limit
        ({"remaining_usd": 0.0}, True, "headers", 0.1, 0.0),
        ({"limit_usd": 0, "remaining_usd": 0.03}, False, "headers", 0.07, 0.03),
        ({"limit_usd": 1.0, "remaining_usd": 0.95}, False, "headers", 0.05, 0.05),  # budget below the limit
        ({"limit_usd": 1.0, "remaining_usd": 0.2}, True, "headers", 0.8, 0.0),
        ({"limit_usd": True, "remaining_usd": "0.0"}, False, "ledger", 0.0, 0.1),  # non-numbers are ignored
    ],
)
def test_evaluate_budget_rules(
    meta: dict[str, Any] | None, stopped: bool, source: str, spent: float, remaining: float
) -> None:
    status = ledger.evaluate_budget(
        "openalex", daily_budget_usd=0.1, stop_fraction=0.8, ledger_spent_usd=0.0, header_meta=meta
    )
    assert (status.stopped, status.source) == (stopped, source)
    assert status.spent_today_usd == pytest.approx(spent)
    assert status.remaining_today_usd == pytest.approx(remaining)


def test_budget_checks_see_rows_recorded_by_other_threads(frozen: FrozenTime) -> None:
    def worker() -> None:
        record(meta={**HEADERS_OK, "remaining_usd": 0.0})

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()
    with pytest.raises(BudgetExceeded):  # check_budget waits for the queued row before reading
        ledger.check_budget("openalex")
