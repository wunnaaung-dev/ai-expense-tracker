from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse

from core.dependencies import get_insights_service
from core.security import get_current_user
from schemas.expense import Currency
from schemas.insights import InsightsResponse, PeriodKind
from services.insights.insights_service import InsightsService

router = APIRouter(prefix="/api/v1", tags=["insights"], dependencies=[Depends(get_current_user)])

# Custom ranges are capped at one year, counted inclusively (date_from and date_to
# both count). 366 lets a full leap year (Jan 1 – Dec 31, 2028) through.
MAX_CUSTOM_DAYS = 366


def _range_error(code: str, **extra) -> JSONResponse:
    """Typed 422 body, not FastAPI's {"detail": ...}: the frontend switches on `code`."""
    return JSONResponse(status_code=422, content={"code": code, **extra})


@router.get("/insights", response_model=InsightsResponse)
async def get_insights(
    period: PeriodKind = Query(default="month", description="Presets end today (Bangkok)"),
    date_from: Optional[date] = Query(default=None, description="Inclusive; custom only"),
    date_to: Optional[date] = Query(default=None, description="Inclusive; custom only"),
    currency: Optional[Currency] = Query(default=None, description="Defaults to the user's default currency"),
    months: int = Query(default=3, ge=1, le=12, description="Months in the monthly report"),
    refresh: bool = Query(default=False, description="Read storage again instead of the short-lived cache"),
    service: InsightsService = Depends(get_insights_service),
):
    if period == "custom":
        if not date_from or not date_to:
            return _range_error("custom_range_required")
        if date_from > date_to:
            return _range_error("invalid_range")
        if (date_to - date_from).days + 1 > MAX_CUSTOM_DAYS:
            return _range_error("range_too_long", max_days=MAX_CUSTOM_DAYS)
    elif date_from or date_to:
        return _range_error("range_not_allowed")
    return await service.get(period, currency, date_from, date_to, months, refresh)