import json
from google import genai
from constants.ai_models import GEMINI_MODEL_FOR_OCR
from constants.categories import VALID_CATEGORIES
from services.text_extraction.abstract_extraction_strategy import ExtractionStrategy
from schemas.expense import ExpenseData
from core.config import settings


PROMPT = f"""
You are a receipt/slip extraction assistant.

First, determine if this image contains an actual receipt, invoice, payment slip,
or transaction confirmation (digital or printed).

If the image is NOT a receipt/slip (e.g. a photo of a person, random object,
screenshot unrelated to a transaction, blank/unclear image), respond with:
{{"is_valid_receipt": false}}

If it IS a valid receipt/slip, extract:
- date (YYYY-MM-DD, use today if not visible)
- amount (number only, no currency symbol)
- description (merchant/store name or short description)
- category (choose EXACTLY one from this list: {", ".join(VALID_CATEGORIES)}.
  If nothing matches clearly, respond with "General")

Respond with ONLY a JSON object, no markdown fences, no other text:
{{"is_valid_receipt": true, "date": "YYYY-MM-DD", "amount": 0.0, "description": "...", "category": "..."}}
"""

class GeminiExtractor(ExtractionStrategy):
    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def extract(self, image_bytes: bytes, mime_type: str) -> ExpenseData:
        response = self.client.models.generate_content(
            model=GEMINI_MODEL_FOR_OCR,
            contents=[
                {"inline_data": {"mime_type": mime_type, "data": image_bytes}},
                PROMPT,
            ]
        )
        if response.text is None:
            raise ValueError(
                f"Gemini blocked the request: {response.prompt_feedback}"
            )
        text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(text)
        return ExpenseData(**data)