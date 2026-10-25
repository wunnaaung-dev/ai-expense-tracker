import csv
import io
from typing import List

from openpyxl import Workbook

from schemas.expense import ExpenseData

HEADERS = ["Date", "Type", "Amount", "Currency", "Category", "Description", "Payment channel", "Source"]


def _row(record: ExpenseData) -> list:
    # Amounts stay as entered, in their own currency: never converted.
    return [
        record.date.isoformat(),
        record.type,
        record.amount,
        record.currency,
        record.category,
        record.description,
        record.payment_channel or "",
        record.source or "",
    ]


def to_csv(records: List[ExpenseData]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADERS)
    writer.writerows(_row(record) for record in records)
    # BOM so Excel reads the file as UTF-8 (Burmese and Thai descriptions).
    return buffer.getvalue().encode("utf-8-sig")


def to_xlsx(records: List[ExpenseData]) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Entries"
    sheet.append(HEADERS)
    for record in records:
        sheet.append(_row(record))
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
