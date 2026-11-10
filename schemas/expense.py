from decimal import Decimal
from typing import Literal, Optional, List

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from datetime import date

from constants.categories import CATEGORIES_BY_TYPE

EntryType = Literal["expense", "income"]
ExpenseSource = Literal["text", "slip", "voice", "manual"]
ExtractMode = Literal["text", "slip", "voice"]
Currency = Literal["THB", "MMK", "USD"]
# How a draft value was decided: read from the input, or the user's setting.
DefaultSource = Literal["detected", "default"]
PaymentChannel = Literal["Cash", "Bank"]

class ExpenseData(BaseModel):
    date: date
    amount: float
    description: str
    category: str = "General"
    # Optional until every backend and extraction prompt supplies them.
    id: Optional[str] = None
    type: EntryType = "expense"
    # Free text on rows written before channels were limited to Cash/Bank.
    payment_channel: Optional[str] = None
    source: Optional[ExpenseSource] = None
    # None only between extraction and EntryDefaults; readers always set it.
    currency: Optional[Currency] = None

class BatchResult(BaseModel):
    filename: str
    status: Literal["success", "failed"]
    data: ExpenseData | None = None
    error: str | None = None

class EntryFilter(BaseModel):
    """Listing filters; every field left as None matches everything."""
    entry_type: Optional[EntryType] = None
    currency: Optional[Currency] = None
    category: Optional[str] = None
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    search: Optional[str] = None

class EntryPage(BaseModel):
    """One page of a listing; `page` is 1-based."""
    model_config = ConfigDict(populate_by_name=True)

    content: List[ExpenseData]
    page: int
    page_size: int = Field(alias="pageSize")
    total_elements: int = Field(alias="totalElements")
    total_pages: int = Field(alias="totalPages")
    first: bool
    last: bool


# --- review step ----------------------------------------------------------------

class EntryDraft(BaseModel):
    """What the review screen shows. Never stored server-side; the client sends it back edited."""
    type: EntryType
    amount: float
    currency: Currency
    currency_source: DefaultSource
    date: date
    category: str
    description: str
    payment_channel: PaymentChannel
    payment_channel_source: DefaultSource
    source: ExtractMode
    source_line: str
    slip_hash: Optional[str] = None

class ExtractResponse(BaseModel):
    status: Literal["draft", "duplicate", "unreadable"]
    draft: Optional[EntryDraft] = None
    # Set for "unreadable" so the frontend can word the message per input mode.
    mode: Optional[ExtractMode] = None

class EntryCreate(BaseModel):
    """The reviewed entry. Saved as is: Gemini is not run again."""
    type: EntryType
    amount: float = Field(gt=0)
    currency: Currency
    date: date
    category: str
    description: str = ""
    payment_channel: PaymentChannel
    source: ExpenseSource
    slip_hash: Optional[str] = None

    @field_validator("description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def _check(self) -> "EntryCreate":
        if self.category not in CATEGORIES_BY_TYPE[self.type]:
            raise ValueError(f"Category {self.category!r} is not allowed for {self.type}")
        exponent = Decimal(str(self.amount)).normalize().as_tuple().exponent
        decimals = max(0, -exponent) if isinstance(exponent, int) else 0
        allowed = 0 if self.currency == "MMK" else 2
        if decimals > allowed:
            raise ValueError(f"{self.currency} amounts allow at most {allowed} decimal places")
        return self

class EntrySaveResponse(BaseModel):
    entry: ExpenseData
    # Link to the saved row ("Open in Google Sheets"); None when it can't be built.
    storage_url: Optional[str] = None

class EntryUpdate(BaseModel):
    """PATCH body: only the fields sent are changed (mirrors UserSettingsUpdate).

    `id` and `source` are deliberately absent — the first identifies the record and the
    second records how it was captured, so neither is user-editable.
    """
    model_config = ConfigDict(extra="forbid")

    date: date = None
    amount: float = None
    description: str = None
    category: str = None
    type: EntryType = None
    payment_channel: PaymentChannel = None
    currency: Currency = None

    @field_validator("description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()
