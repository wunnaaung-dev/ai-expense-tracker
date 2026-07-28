from services.text_extraction.abstract_extraction_strategy import ExtractionStrategy
from services.text_extraction.gemini_extractor import GeminiExtractor


def get_extraction_service() -> ExtractionStrategy:
    return GeminiExtractor()