# Both are stable models with a Gemini API free tier; avoid preview models and
# moving aliases like "-latest", whose free-tier terms aren't documented.
GEMINI_MODEL_FOR_OCR = "gemini-3.6-flash"
# Tried once when the primary model is overloaded or out of free quota (429/5xx).
GEMINI_FALLBACK_MODEL = "gemini-3.5-flash-lite"

__all__ = ["GEMINI_MODEL_FOR_OCR", "GEMINI_FALLBACK_MODEL"]
