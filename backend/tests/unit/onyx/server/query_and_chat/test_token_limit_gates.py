"""Gate tests for the two budget behaviours Onyx-for-Sales carries on top of
upstream's token/cost enforcement.

Upstream owns the rest of the enforcement layer (and tests it in
`test_token_limit.py`); only our divergences live here:

1. **Group elevation** — a group cost budget raises each member's PERSONAL cap
   to max(own budget, best same-window group budget). Elevation only extends.
2. **Per-member grants** — a group cost budget is an individual grant, never a
   shared pool. One member's spend never blocks another; the group gate pools
   TOKEN budgets only.

Both are enforced on the user gate via `group_elevated_cost_limits`.
"""

import datetime
from collections.abc import Generator
from typing import cast

import pytest
from sqlalchemy import create_engine
from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import JSONB as PGJSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.engine import Engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.orm import sessionmaker

import ee.onyx.server.query_and_chat.token_limit as ee_token_limit
import onyx.server.query_and_chat.token_limit as token_limit
from onyx.db.models import TokenRateLimit
from onyx.db.models import TokenRateLimitScope
from onyx.db.models import UserUsage
from onyx.db.user_usage import get_window_start
from onyx.db.user_usage import TokenUsageBucket
from onyx.error_handling.error_codes import OnyxErrorCode
from onyx.error_handling.exceptions import OnyxError


# Postgres-only column types -> SQLite equivalents so the real UserUsage table
# can back the real cost-source query path.
@compiles(PGUUID, "sqlite")
def _compile_pguuid_sqlite(_e: object, _c: object, **_kw: object) -> str:
    return "CHAR(36)"


@compiles(PGJSONB, "sqlite")
def _compile_jsonb_sqlite(_e: object, _c: object, **_kw: object) -> str:
    return "JSON"


@pytest.fixture
def ledger_session() -> Generator[Session, None, None]:
    engine: Engine = create_engine("sqlite://")
    cast(Table, UserUsage.__table__).create(bind=engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def _cost_limit(
    cost_budget_cents: float | None,
    scope: TokenRateLimitScope,
    period_hours: int = 1,
    token_budget: int | None = 10**12,  # default unbounded; None = cost-only
) -> TokenRateLimit:
    limit = TokenRateLimit(
        enabled=True,
        token_budget=token_budget,
        period_hours=period_hours,
        scope=scope,
    )
    limit.cost_budget_cents = cost_budget_cents
    return limit


def _group_token_limit(token_budget: int, period_hours: int = 1) -> TokenRateLimit:
    return TokenRateLimit(
        enabled=True,
        token_budget=token_budget,
        period_hours=period_hours,
        scope=TokenRateLimitScope.USER_GROUP,
    )


def _usage(token_count: int) -> list[TokenUsageBucket]:
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    return [TokenUsageBucket(window_start=now, tokens=token_count)]


def _recent_cost_buckets(total: float) -> list[tuple[datetime.datetime, float]]:
    """A single just-now cost bucket, so it lands inside any limit's window."""
    return [(datetime.datetime.now(datetime.timezone.utc), total)]


def _assert_structured_429(exc: OnyxError, label: str) -> None:
    """The 429 our gate raises: RATE_LIMITED, USER scope, coherent Retry-After.

    The exact reset instant is upstream's window math (covered by its own
    tests); what this file guards is which users the gate blocks.
    """
    assert exc.error_code is OnyxErrorCode.RATE_LIMITED
    assert exc.status_code == 429
    assert label in exc.detail

    extra = exc.extra or {}
    assert extra["scope"] == TokenRateLimitScope.USER.value

    reset_at = datetime.datetime.fromisoformat(cast(str, extra["reset_at"]))
    now = datetime.datetime.now(tz=datetime.timezone.utc)
    assert reset_at > now

    retry_after = cast(int, extra["retry_after_seconds"])
    assert abs(retry_after - (reset_at - now).total_seconds()) < 60
    assert exc.headers == {"Retry-After": str(retry_after)}


class _SessionCtx:
    """Stand-in for get_session_with_current_tenant; every source is patched."""

    def __enter__(self) -> object:
        return object()

    def __exit__(self, *args: object) -> None:
        return None


class _RealLedgerSessionCtx:
    """Yields a real SQLite session backing the actual UserUsage cost query."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def __enter__(self) -> Session:
        return self._session

    def __exit__(self, *args: object) -> None:
        return None


def _stub_user_sources(
    monkeypatch: pytest.MonkeyPatch,
    limits: list[TokenRateLimit],
    token_usage: list[TokenUsageBucket],
    cost_buckets: list[tuple[datetime.datetime, float]],
    member_group_limits: dict[int, list[TokenRateLimit]] | None = None,
) -> None:
    monkeypatch.setattr(
        ee_token_limit, "get_session_with_current_tenant", lambda: _SessionCtx()
    )
    monkeypatch.setattr(
        ee_token_limit, "fetch_all_user_token_rate_limits", lambda **_: limits
    )
    monkeypatch.setattr(ee_token_limit, "_fetch_user_usage", lambda *_: token_usage)
    monkeypatch.setattr(
        ee_token_limit, "get_user_cost_cents_buckets_since", lambda *_: cost_buckets
    )
    monkeypatch.setattr(
        ee_token_limit,
        "_fetch_all_user_group_rate_limits",
        lambda *_: member_group_limits or {},
    )


def _stub_group_sources(
    monkeypatch: pytest.MonkeyPatch,
    group_limits: dict[int, list[TokenRateLimit]],
    group_token_usage: dict[int, list[TokenUsageBucket]],
) -> None:
    monkeypatch.setattr(
        ee_token_limit, "get_session_with_current_tenant", lambda: _SessionCtx()
    )
    monkeypatch.setattr(
        ee_token_limit, "_fetch_all_user_group_rate_limits", lambda *_: group_limits
    )
    monkeypatch.setattr(
        ee_token_limit, "_fetch_user_group_usage", lambda *_: group_token_usage
    )


class TestGroupElevatedUserBudget:
    """A group cost budget elevates its members' PERSONAL cap (max of own and
    best same-window group budget) — elevation only ever extends the user
    scope, and group budgets never act as a shared pool."""

    def test_member_of_richer_group_passes_user_cap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        user_limit = _cost_limit(2500.0, TokenRateLimitScope.USER, period_hours=168)
        rich_group = _cost_limit(
            10000.0, TokenRateLimitScope.USER_GROUP, period_hours=168
        )
        # $30 spent: over the $25 user cap, under the $100 group elevation.
        _stub_user_sources(
            monkeypatch,
            [user_limit],
            _usage(1),
            _recent_cost_buckets(3000.0),
            member_group_limits={1: [rich_group]},
        )
        ee_token_limit._user_is_rate_limited(uuid.uuid4())  # no raise

    def test_elevated_budget_still_blocks_when_exceeded(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        user_limit = _cost_limit(2500.0, TokenRateLimitScope.USER, period_hours=168)
        rich_group = _cost_limit(
            10000.0, TokenRateLimitScope.USER_GROUP, period_hours=168
        )
        _stub_user_sources(
            monkeypatch,
            [user_limit],
            _usage(1),
            _recent_cost_buckets(10001.0),
            member_group_limits={1: [rich_group]},
        )
        with pytest.raises(OnyxError) as ei:
            ee_token_limit._user_is_rate_limited(uuid.uuid4())
        _assert_structured_429(ei.value, "your account")

    def test_different_window_group_does_not_elevate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        user_limit = _cost_limit(2500.0, TokenRateLimitScope.USER, period_hours=168)
        other_window_group = _cost_limit(
            10000.0, TokenRateLimitScope.USER_GROUP, period_hours=24
        )
        _stub_user_sources(
            monkeypatch,
            [user_limit],
            _usage(1),
            _recent_cost_buckets(3000.0),
            member_group_limits={1: [other_window_group]},
        )
        with pytest.raises(OnyxError):
            ee_token_limit._user_is_rate_limited(uuid.uuid4())

    def test_poorer_group_never_restricts_user_scope(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        user_limit = _cost_limit(2500.0, TokenRateLimitScope.USER, period_hours=168)
        poor_group = _cost_limit(
            1000.0, TokenRateLimitScope.USER_GROUP, period_hours=168
        )
        # $20: under the $25 user cap; the poorer $10 group must not lower it.
        _stub_user_sources(
            monkeypatch,
            [user_limit],
            _usage(1),
            _recent_cost_buckets(2000.0),
            member_group_limits={1: [poor_group]},
        )
        ee_token_limit._user_is_rate_limited(uuid.uuid4())  # no raise


class TestGroupGateEE:
    """The per-group gate enforces TOKEN pools only ('allowed if ANY of the
    user's groups is under budget'); group COST budgets are per-member grants
    handled by the user gate."""

    def test_group_over_token_budget_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        limit = _group_token_limit(token_budget=1)  # 1,000 tokens
        _stub_group_sources(monkeypatch, {1: [limit]}, {1: _usage(5_000)})
        with pytest.raises(OnyxError) as ei:
            ee_token_limit._user_is_rate_limited_by_group(uuid.uuid4())
        assert ei.value.error_code is OnyxErrorCode.RATE_LIMITED

    def test_group_under_token_budget_does_not_raise(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        limit = _group_token_limit(token_budget=10)  # 10,000 tokens
        _stub_group_sources(monkeypatch, {1: [limit]}, {1: _usage(500)})
        ee_token_limit._user_is_rate_limited_by_group(uuid.uuid4())  # no raise

    def test_any_group_under_budget_unblocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        over = _group_token_limit(token_budget=1)
        under = _group_token_limit(token_budget=10)
        _stub_group_sources(
            monkeypatch,
            {1: [over], 2: [under]},
            {1: _usage(5_000), 2: _usage(500)},
        )
        ee_token_limit._user_is_rate_limited_by_group(uuid.uuid4())  # no raise


class TestGroupCostBudgetPerMember:
    """A group cost budget grants each member that much INDIVIDUAL headroom —
    it is not a shared pool. Other members' spend never blocks a user; a
    member is blocked only when their OWN spend exceeds the group budget."""

    def test_other_members_spend_never_blocks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        limit = _cost_limit(500.0, TokenRateLimitScope.USER_GROUP, period_hours=24)
        # Aggregate group spend exceeding the budget is irrelevant to the group
        # gate now — cost budgets are per-member grants, never a pool.
        _stub_group_sources(monkeypatch, {1: [limit]}, {1: _usage(1)})
        ee_token_limit._user_is_rate_limited_by_group(uuid.uuid4())  # no raise

    def test_group_only_budget_blocks_member_own_overspend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        group = _cost_limit(500.0, TokenRateLimitScope.USER_GROUP, period_hours=24)
        # No user-scope limits at all: the group budget alone must cap the
        # member's own spend (600 > 500).
        _stub_user_sources(
            monkeypatch,
            [],
            _usage(0),
            _recent_cost_buckets(600.0),
            member_group_limits={1: [group]},
        )
        with pytest.raises(OnyxError) as ei:
            ee_token_limit._user_is_rate_limited(uuid.uuid4())
        _assert_structured_429(ei.value, "your account")

    def test_group_only_budget_allows_member_under_own_spend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import uuid

        group = _cost_limit(500.0, TokenRateLimitScope.USER_GROUP, period_hours=24)
        _stub_user_sources(
            monkeypatch,
            [],
            _usage(0),
            _recent_cost_buckets(100.0),
            member_group_limits={1: [group]},
        )
        ee_token_limit._user_is_rate_limited(uuid.uuid4())  # no raise

    def test_helper_synthesizes_cost_limit_for_uncovered_window(self) -> None:
        group = _cost_limit(500.0, TokenRateLimitScope.USER_GROUP, period_hours=168)
        out = token_limit.group_elevated_cost_limits([], [group])
        assert len(out) == 1
        assert out[0].cost_budget_cents == 500.0
        assert out[0].period_hours == 168
        assert out[0].token_budget is None

    def test_two_members_real_ledger_independent_budgets(
        self, monkeypatch: pytest.MonkeyPatch, ledger_session: Session
    ) -> None:
        # Two members of one $5 group, through the REAL UserUsage ledger query:
        # the heavy spender (own $6) is blocked, the light spender (own $1) is
        # not — regardless of the group's combined spend exceeding the budget.
        import uuid

        heavy, light = uuid.uuid4(), uuid.uuid4()
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        # Rows are written directly rather than through `record_user_usage`,
        # whose Postgres upsert has no SQLite path. The gate's read query is
        # what's under test here, not the recorder.
        ledger_window = get_window_start(now, 24 * 3600)
        for user_id, cents in ((heavy, 600.0), (light, 100.0)):
            ledger_session.add(
                UserUsage(
                    user_id=str(user_id),
                    window_start=ledger_window,
                    model="m",
                    flow="CHAT",
                    provider="",
                    input_tokens=1,
                    output_tokens=1,
                    cache_read_tokens=0,
                    cost_cents=cents,
                )
            )
        ledger_session.commit()

        group = _cost_limit(500.0, TokenRateLimitScope.USER_GROUP, period_hours=168)
        monkeypatch.setattr(
            ee_token_limit,
            "get_session_with_current_tenant",
            lambda: _RealLedgerSessionCtx(ledger_session),
        )
        monkeypatch.setattr(
            ee_token_limit, "fetch_all_user_token_rate_limits", lambda **_: []
        )
        monkeypatch.setattr(
            ee_token_limit, "_fetch_all_user_group_rate_limits", lambda *_: {1: [group]}
        )

        with pytest.raises(OnyxError) as ei:
            ee_token_limit._user_is_rate_limited(heavy)
        _assert_structured_429(ei.value, "your account")
        ee_token_limit._user_is_rate_limited(light)  # no raise
