from typing import Optional, Tuple

from constants.currencies import CURRENCIES
from constants.payment_channels import PAYMENT_CHANNELS
from models.users import User
from schemas.expense import Currency, DefaultSource, PaymentChannel


class EntryDefaults:
    """Fills in what Gemini couldn't detect from the user's settings, and says which it was."""

    def __init__(self, user: User):
        self.user = user

    def currency(self, detected: Optional[str]) -> Tuple[Currency, DefaultSource]:
        if detected in CURRENCIES:
            return detected, "detected"
        return self.user.default_currency, "default"

    def payment_channel(self, detected: Optional[str]) -> Tuple[PaymentChannel, DefaultSource]:
        if detected in PAYMENT_CHANNELS:
            return detected, "detected"
        return self.user.default_payment_channel, "default"
