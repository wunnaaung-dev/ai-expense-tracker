from fastapi import APIRouter, Depends, Query

from core.dependencies import get_insights_service
from core.security import get_current_user
from schemas.dashboard import DashboardPeriod, DashboardResponse
from services.insights.insights_service import InsightsService

router = APIRouter(prefix="/api/v1", tags=["dashboard"], dependencies=[Depends(get_current_user)])


@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard(
    period: DashboardPeriod = Query(default="month", description="Ends today (Bangkok)"),
    service: InsightsService = Depends(get_insights_service),
):
    return await service.dashboard(period)
