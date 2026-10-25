import math
from typing import List

from schemas.expense import EntryPage, ExpenseData


def paginate(records: List[ExpenseData], page: int, page_size: int) -> EntryPage:
    """Slice one 1-based page; a page past the end is empty but keeps the totals."""
    total = len(records)
    total_pages = math.ceil(total / page_size)
    start = (page - 1) * page_size
    return EntryPage(
        content=records[start:start + page_size],
        page=page,
        page_size=page_size,
        total_elements=total,
        total_pages=total_pages,
        first=page == 1,
        last=page >= total_pages,
    )
