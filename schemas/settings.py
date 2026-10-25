from typing import Literal

from pydantic import BaseModel, ConfigDict

from schemas.expense import Currency, PaymentChannel

WeekStart = Literal["monday", "sunday"]


class UserSettings(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_currency: Currency
    default_payment_channel: PaymentChannel
    week_start: WeekStart


class UserSettingsUpdate(BaseModel):
    """PATCH body: only the fields sent are changed.

    Defaults are None but the types aren't Optional, so an explicit null is a 422.
    """
    model_config = ConfigDict(extra="forbid")

    default_currency: Currency = None
    default_payment_channel: PaymentChannel = None
    week_start: WeekStart = None
