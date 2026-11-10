import json
from datetime import date
from typing import Optional
from google import genai
from google.genai import errors as genai_errors
from google.genai import types as genai_types
from constants.ai_models import GEMINI_FALLBACK_MODEL, GEMINI_MODEL_FOR_OCR
from constants.categories import CATEGORIES_BY_TYPE
from constants.currencies import CURRENCIES

from schemas.expense import ExpenseData, EntryType
from core.clock import today
from core.config import settings
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy
from services.extraction.errors import ExtractionUnavailableError

CURRENCY_RULES = """
- currency: exactly one of "THB", "MMK", "USD", decided from signals in the input:
  - THB: "baht", "฿", "บาท", Thai banks (KBank, SCB, Bangkok Bank, Krungsri), PromptPay
  - MMK: "Ks", "kyat", "ကျပ်", KBZPay, Wave Pay, AYA Pay, CB Pay
  - USD: "$", "USD", "dollar", "ဒေါ်လာ"
  If there is no clear signal, use null. Never guess and never convert the amount.
"""

CHANNEL_RULES = """
- payment_channel: exactly "Bank" or "Cash". Bank names, bank transfers, cards,
  PromptPay and e-wallets (KBZPay, Wave Pay, AYA Pay, CB Pay, TrueMoney, ...) are
  "Bank". Cash in hand is "Cash". If there is no clear signal, use null.
"""


def _fields(entry_type: EntryType) -> str:
    """The per-entry extraction rules shared by every prompt."""
    money = "money spent" if entry_type == "expense" else "money received"
    return f"""- date (YYYY-MM-DD; if "today"/"ဒီနေ့" is mentioned or no date is given, use today: {today().isoformat()})
- amount ({money}; number only, no currency symbol, exactly as given)
- description (short description: merchant, payer or what it was for)
- category (choose EXACTLY one from this list: {", ".join(CATEGORIES_BY_TYPE[entry_type])}.
  If nothing matches clearly, respond with "General")
{CURRENCY_RULES}{CHANNEL_RULES}"""


ENTRY_JSON = '{"date": "YYYY-MM-DD", "amount": 0.0, "description": "...", "category": "...", "currency": "THB", "payment_channel": "Cash"}'


def image_prompt(entry_type: EntryType) -> str:
    return f"""
You are a receipt/slip extraction assistant. The user is logging an {entry_type}.

First, determine if this image contains an actual receipt, invoice, payment slip,
or transaction confirmation (digital or printed).

If the image is NOT a receipt/slip (e.g. a photo of a person, random object,
screenshot unrelated to a transaction, blank/unclear image), or no amount is
readable, respond with:
{{"is_valid_receipt": false}}

If it IS a valid receipt/slip, extract:
{_fields(entry_type)}
Respond with ONLY a JSON object, no markdown fences, no other text:
{{"is_valid_receipt": true, {ENTRY_JSON[1:]}
"""


def text_prompt(entry_type: EntryType) -> str:
    return f"""
You are a money-logging assistant. The user describes one {entry_type} in free text
(possibly in Burmese, English or Thai). If several are mentioned, use only the main one.

Extract:
{_fields(entry_type)}
Respond with ONLY a JSON object, no markdown fences, no other text:
{ENTRY_JSON}

If the text contains no amount, respond with: {{"amount": null}}
"""


def audio_prompt(entry_type: EntryType) -> str:
    return f"""
You are a money-logging assistant. Listen to this audio carefully.
The speaker may be speaking in Burmese (Myanmar language), English, Thai,
or a mix of languages — transcribe accurately regardless of language,
paying close attention to Burmese numerals and currency terms
(ဘတ်, ကျပ်, ဒေါ်လာ) if spoken. The user is logging an {entry_type}.

First, transcribe what was said. Then decide if it describes a real {entry_type}
with an amount. If it does NOT (silence, noise, unrelated speech, unclear
audio, no amount), respond with:
{{"is_valid": false, "transcript": "..."}}

If it does, extract the main one only:
{_fields(entry_type)}
Respond with ONLY a JSON object, no markdown fences, no other text:
{{"is_valid": true, "transcript": "...", "entry": {ENTRY_JSON}}}
"""

def _currency(raw) -> str | None:
    """Gemini's currency if it's one we support; None lets the user's last currency apply."""
    if not isinstance(raw, str):
        return None
    code = raw.strip().upper()
    return code if code in CURRENCIES else None


class GeminiExtractor(ImageExtractionStrategy, TextExtractionStrategy, AudioExtractionStrategy):
    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)

    def _generate(self, contents):
        models = [GEMINI_MODEL_FOR_OCR, GEMINI_FALLBACK_MODEL]
        for i, model in enumerate(models):
            try:
                return self.client.models.generate_content(
                    model=model,
                    contents=contents,
                    # JSON mode: the API guarantees parseable JSON, unlike a prompt-only request.
                    config=genai_types.GenerateContentConfig(response_mime_type="application/json"),
                )
            except genai_errors.APIError as e:
                # 429 and 5xx (e.g. 503 "high demand") clear up on their own: try the
                # fallback model, and if that is busy too the client can retry later.
                retryable = e.code == 429 or isinstance(e, genai_errors.ServerError)
                if retryable and i < len(models) - 1:
                    continue
                raise ExtractionUnavailableError(e.message or str(e), retryable=retryable) from e

    @staticmethod
    def _parse_json(text: str):
        text = text.strip().removeprefix("```json").removesuffix("```").strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionUnavailableError(f"AI returned malformed JSON: {e}", retryable=True) from e

    async def extract(self, image_bytes: bytes, mime_type: str, entry_type: EntryType) -> Optional[ExpenseData]:
        response = self._generate([
            {"inline_data": {"mime_type": mime_type, "data": image_bytes}},
            image_prompt(entry_type),
        ])
        if response.text is None:  # blocked by Gemini's safety filters
            return None
        raw = self._parse_json(response.text)
        if not isinstance(raw, dict) or not raw.get("is_valid_receipt", False):
            return None
        return _entry(raw)

    async def extract_from_text(self, text: str, entry_type: EntryType) -> Optional[ExpenseData]:
        response = self._generate([text, text_prompt(entry_type)])
        if response.text is None:
            return None
        raw = self._parse_json(response.text)
        return _entry(raw) if isinstance(raw, dict) else None

    async def extract_from_audio(
        self, audio_bytes: bytes, mime_type: str, entry_type: EntryType
    ) -> tuple[Optional[ExpenseData], str]:
        response = self._generate([audio_prompt(entry_type), {"inline_data": {"mime_type": mime_type, "data": audio_bytes}}])
        raw = self._parse_json(response.text or '{"is_valid": false}')
        if not isinstance(raw, dict):
            return None, ""
        transcript = str(raw.get("transcript") or "").strip()
        entry = raw.get("entry")
        if not raw.get("is_valid", False) or not isinstance(entry, dict):
            return None, transcript
        return _entry(entry), transcript


def _entry(raw: dict) -> Optional[ExpenseData]:
    """Gemini's JSON as an ExpenseData, or None when there's no usable amount.

    Values the review form must constrain (category, channel) are passed through
    as given; EntryService replaces anything outside the allowed lists.
    """
    amount = raw.get("amount")
    if isinstance(amount, str):
        try:
            amount = float(amount.replace(",", ""))
        except ValueError:
            return None
    if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
        return None
    try:
        entry_date = date.fromisoformat(str(raw.get("date")))
    except ValueError:
        entry_date = today()
    return ExpenseData(
        date=entry_date,
        amount=amount,
        description=str(raw.get("description") or ""),
        category=str(raw.get("category") or "General"),
        payment_channel=raw.get("payment_channel") if isinstance(raw.get("payment_channel"), str) else None,
        currency=_currency(raw.get("currency")),
    )
