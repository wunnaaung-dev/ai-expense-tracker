from schemas.expense import EntryFilter, ExpenseData


def matches(record: ExpenseData, filters: EntryFilter) -> bool:
    """The listing rules both backends apply, so Sheets and Notion return the same records."""
    if filters.entry_type and record.type != filters.entry_type:
        return False
    if filters.currency and record.currency != filters.currency:
        return False
    if filters.category and record.category.casefold() != filters.category.casefold():
        return False
    if filters.date_from and record.date < filters.date_from:
        return False
    if filters.date_to and record.date > filters.date_to:
        return False
    search = (filters.search or "").strip().casefold()
    if search and search not in record.description.casefold() and search not in record.category.casefold():
        return False
    return True
