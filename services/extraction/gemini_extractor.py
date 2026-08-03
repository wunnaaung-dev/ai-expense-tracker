from datetime import date
import json
from typing import List
from google import genai
from constants.ai_models import GEMINI_MODEL_FOR_OCR
from constants.categories import VALID_CATEGORIES

from schemas.expense import ExpenseData
from core.config import settings
from services.extraction.abstract_audio_extraction_strategy import AudioExtractionStrategy
from services.extraction.abstract_image_extraction_strategy import ImageExtractionStrategy
from services.extraction.abstract_text_extraction_strategy import TextExtractionStrategy

IMAGE_EXTRACTION_PROMPT = f"""
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

TEXT_EXTRACTION_PROMPT = """
You are an expense-logging assistant. The user will describe expenses in free text
(possibly in Burmese or English, possibly multiple expenses in one message).

Extract EVERY distinct expense mentioned. For each one, extract:
- date (YYYY-MM-DD; if "today"/"ဒီနေ့" is mentioned or no date given, use today's date: __TODAY__)
- amount (number only)
- description (short description of what it was for)
- category (choose EXACTLY one from this list: {categories}.
  If nothing matches clearly, respond with "General")

Respond with ONLY a JSON array, no markdown fences, no other text:
[
  {{"date": "YYYY-MM-DD", "amount": 0.0, "description": "...", "category": "..."}}
]

If no valid expense is found in the text, respond with an empty array: []
""".replace("{categories}", ", ".join(VALID_CATEGORIES))

AUDIO_EXTRACTION_PROMPT = f"""
You are an expense-logging assistant. Listen to this audio carefully.
The speaker may be speaking in Burmese (Myanmar language), English, Thai,
or a mix of languages — transcribe accurately regardless of language,
paying close attention to Burmese numerals and currency terms
(ဘတ်, ကျပ်, ဒေါ်လာ) if spoken.

First, determine if the audio contains a real expense description
(the speaker mentioning something they spent money on).

If the audio is NOT a valid expense description (silence, noise,
unrelated speech, unclear audio), respond with:
{{"is_valid": false}}

If it IS a valid expense description, extract EVERY distinct expense
mentioned. For each one:
- date (YYYY-MM-DD; if "today"/"ဒီနေ့" or no date mentioned, use today: {{today}})
- amount (number only)
- description (short description of what it was for)
- category (choose EXACTLY one from this list: {", ".join(VALID_CATEGORIES)}.
  If nothing matches clearly, respond with "General")

Respond with ONLY a JSON object, no markdown fences, no other text:
{{
  "is_valid": true,
  "expenses": [
    {{"date": "YYYY-MM-DD", "amount": 0.0, "description": "...", "category": "..."}}
  ]
}}
"""

class GeminiExtractor(ImageExtractionStrategy, TextExtractionStrategy, AudioExtractionStrategy):
    def __init__(self):
        self.client = genai.Client(api_key=settings.gemini_api_key)

    async def extract(self, image_bytes: bytes, mime_type: str) -> ExpenseData:
        response = self.client.models.generate_content(
            model=GEMINI_MODEL_FOR_OCR,
            contents=[
                {"inline_data": {"mime_type": mime_type, "data": image_bytes}},
                IMAGE_EXTRACTION_PROMPT,
            ]
        )
        if response.text is None:
            raise ValueError(
                f"Gemini blocked the request: {response.prompt_feedback}"
            )
        text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        data = json.loads(text)
        return ExpenseData(**data)
    
    async def extract_from_text(self, text: str) -> List[ExpenseData]:
        prompt = TEXT_EXTRACTION_PROMPT.replace("__TODAY__", date.today().isoformat())

        response = self.client.models.generate_content(
            model=GEMINI_MODEL_FOR_OCR,
            contents=[text, prompt]
        )

        if response.text is None:
            raise ValueError(
                f"Gemini blocked the request: {response.prompt_feedback}"
            )

        text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        raw_list = json.loads(text)
        
        items = []

        for raw in raw_list:
            if raw.get("amount", 0) <= 0:
                continue
            items.append(
                ExpenseData(
                    date=raw.get("date", date.today().isoformat()),
                    amount=raw["amount"],
                    description=raw.get("description", ""),
                    category=raw.get("category", "General"),
                )
            )
        
        return items
    
    async def extract_from_audio(self, audio_bytes: bytes, mime_type: str) -> list[ExpenseData]:
        prompt = AUDIO_EXTRACTION_PROMPT.replace("{today}", date.today().isoformat())

        response = self.client.models.generate_content( 
            model=GEMINI_MODEL_FOR_OCR,
            contents=[prompt, {"inline_data": {"mime_type": mime_type, "data": audio_bytes}}]
        )

        raw = json.loads(response.text or '{"is_valid": false}')

        if not raw.get("is_valid", False):
            return []

        items = []
        for expense in raw.get("expenses", []):
            if expense.get("amount", 0) <= 0:
                continue
            items.append(
                ExpenseData(
                    date=expense["date"],
                    amount=expense["amount"],
                    description=expense["description"],
                    category=expense.get("category", "General"),
                )
            )
        return items