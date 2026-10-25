from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_storage_service

from core.security import get_current_user
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.pagination import paginate
from schemas.expense import Currency, EntryFilter, EntryPage, ExpenseType

from typing import Optional

router = APIRouter(prefix="/api/v1", tags=["expense"], dependencies=[Depends(get_current_user)])


class _ListParams:
    def __init__(
        self,
        page: int = Query(default=1, ge=1, description="1-based page number"),
        page_size: int = Query(default=10, ge=1, le=100),
        search: Optional[str] = Query(default=None, description="Substring of description or category"),
        date_from: Optional[date] = Query(default=None, description="Inclusive"),
        date_to: Optional[date] = Query(default=None, description="Inclusive"),
        category: Optional[str] = Query(default=None),
        currency: Optional[Currency] = Query(default=None),
    ):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
        self.page = page
        self.page_size = page_size
        self.filters = EntryFilter(
            search=search.strip() if search and search.strip() else None,
            date_from=date_from,
            date_to=date_to,
            category=category.strip() if category and category.strip() else None,
            currency=currency,
        )


async def _list(params: _ListParams, storage: StorageStrategy, entry_type: ExpenseType) -> EntryPage:
    records = await storage.get_all(params.filters.model_copy(update={"entry_type": entry_type}))
    return paginate(records, params.page, params.page_size)


@router.get("/expense", response_model=EntryPage)
async def get_all_expenses(
    params: _ListParams = Depends(),
    storage: StorageStrategy = Depends(get_storage_service)
):
    return await _list(params, storage, "expense")

@router.get("/income", response_model=EntryPage)
async def get_all_income(
    params: _ListParams = Depends(),
    storage: StorageStrategy = Depends(get_storage_service)
):
    return await _list(params, storage, "income")
