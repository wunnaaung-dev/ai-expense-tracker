from datetime import date, timedelta
from typing import Optional

from core import clock
from models.users import User
from schemas.dashboard import DashboardPeriod, DashboardResponse
from schemas.expense import Currency
from schemas.insights import InsightsResponse, PeriodKind
from services.insights.aggregate import build_dashboard, build_insights
from services.insights.periods import month_ranges, resolve_period
from services.storage import record_cache
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.record_cache import StorageTag


class InsightsService:
    """Insights and Home: one read of the user's records (cached briefly), aggregated in memory."""

    def __init__(self, storage: StorageStrategy, user: User, storage_tag: StorageTag):
        self.storage = storage
        self.user = user
        self.storage_tag = storage_tag

    async def get(
        self,
        kind: PeriodKind,
        currency: Optional[Currency] = None,
        date_from: Optional[date] = None,
        date_to: Optional[date] = None,
        months: int = 3,
        refresh: bool = False,
    ) -> InsightsResponse:
        records, fetched_at = await record_cache.get_records(self.storage, self.user.id, self.storage_tag, refresh)
        today = clock.today()
        period = resolve_period(kind, today, self.user.week_start, date_from, date_to)
        return build_insights(
            records,
            currency or self.user.default_currency,
            period,
            month_ranges(period.current.end, months),
            today,
            fetched_at,
        )

    async def dashboard(self, kind: DashboardPeriod) -> DashboardResponse:
        """Home, in the user's default currency. The spending flow is always the last 7 days."""
        records, fetched_at = await record_cache.get_records(self.storage, self.user.id, self.storage_tag)
        today = clock.today()
        week_start = self.user.week_start
        period = resolve_period(kind, today, week_start)
        flow = resolve_period("custom", today, week_start, today - timedelta(days=6), today)
        return build_dashboard(records, self.user.default_currency, period, flow, fetched_at)
