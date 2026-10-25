from datetime import date
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from core.clock import today
from core.dependencies import get_storage_service
from core.security import get_current_user
from schemas.expense import Currency, EntryFilter, ExpenseType
from services.export.exporter import to_csv, to_xlsx
from services.storage.abstract_storage_strategy import StorageStrategy

router = APIRouter(prefix="/api/v1", tags=["export"], dependencies=[Depends(get_current_user)])

ExportFormat = Literal["csv", "xlsx"]

_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}


def _export_filters(
    date_from: Optional[date] = Query(default=None, description="Inclusive"),
    date_to: Optional[date] = Query(default=None, description="Inclusive"),
    currency: Optional[Currency] = Query(default=None),
    type: Optional[ExpenseType] = Query(default=None, description="Omit for expenses and income"),
) -> EntryFilter:
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
    return EntryFilter(date_from=date_from, date_to=date_to, currency=currency, entry_type=type)


@router.get("/export/count")
async def export_count(
    filters: EntryFilter = Depends(_export_filters),
    storage: StorageStrategy = Depends(get_storage_service),
):
    """How many entries the same export would contain."""
    return {"count": len(await storage.get_all(filters))}


@router.get("/export")
async def export_entries(
    format: ExportFormat = Query(default="csv"),
    filters: EntryFilter = Depends(_export_filters),
    storage: StorageStrategy = Depends(get_storage_service),
):
    """Every matching entry as a CSV or Excel download, newest first."""
    records = await storage.get_all(filters)
    content = to_csv(records) if format == "csv" else to_xlsx(records)
    filename = f"expense-tracker-{today().isoformat()}.{format}"
    return Response(
        content=content,
        media_type=_MEDIA_TYPES[format],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
