from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from core.dependencies import get_storage_service, get_storage_tag
from core.security import get_current_user
from models.users import User
from schemas.expense import Currency, EntryFilter, EntryPage, EntryType
from services.storage import record_cache
from services.storage.abstract_storage_strategy import StorageStrategy
from services.storage.filters import matches
from services.storage.pagination import paginate
from services.storage.record_cache import StorageTag

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
        refresh: bool = Query(default=False, description="Read storage again instead of the short-lived cache"),
    ):
        if date_from and date_to and date_from > date_to:
            raise HTTPException(status_code=422, detail="date_from must be on or before date_to")
        self.page = page
        self.page_size = page_size
        self.refresh = refresh
        self.filters = EntryFilter(
            search=search.strip() if search and search.strip() else None,
            date_from=date_from,
            date_to=date_to,
            category=category.strip() if category and category.strip() else None,
            currency=currency,
        )


async def _list(
    params: _ListParams,
    storage: StorageStrategy,
    current_user: User,
    tag: StorageTag,
    entry_type: EntryType,
) -> EntryPage:
    # One unfiltered read per user (shared with Insights and Dashboard), kept briefly;
    # every page and filter change is applied to it here instead of re-reading storage.
    records, _ = await record_cache.get_records(
        storage, current_user.id, tag, params.refresh
    )
    filters = params.filters.model_copy(update={"entry_type": entry_type})
    # Both the comprehension and paginate allocate, so the cached list is never mutated.
    filtered = [record for record in records if matches(record, filters)]
    return paginate(filtered, params.page, params.page_size)


@router.get("/expense", response_model=EntryPage)
async def get_all_expenses(
    params: _ListParams = Depends(),
    storage: StorageStrategy = Depends(get_storage_service),
    current_user: User = Depends(get_current_user),
    tag: StorageTag = Depends(get_storage_tag),
):
    return await _list(params, storage, current_user, tag, "expense")

@router.get("/income", response_model=EntryPage)
async def get_all_income(
    params: _ListParams = Depends(),
    storage: StorageStrategy = Depends(get_storage_service),
    current_user: User = Depends(get_current_user),
    tag: StorageTag = Depends(get_storage_tag),
):
    return await _list(params, storage, current_user, tag, "income")
