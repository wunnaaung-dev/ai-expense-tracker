from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_insights_service
from core.security import get_current_user
from schemas.expense import Currency
from schemas.insights import InsightsResponse, PeriodKind
from services.insights.insights_service import InsightsService

router = APIRouter(prefix="/api/v1", tags=["insights"], dependencies=[Depends(get_current_user)])


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
            raise HTTPException(status_code=422, detail="date_from and date_to are required for a custom period")
        if date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    elif date_from or date_to:
        raise HTTPException(status_code=422, detail="date_from and date_to are only used with period=custom")
    return await service.get(period, currency, date_from, date_to, months, refresh)
