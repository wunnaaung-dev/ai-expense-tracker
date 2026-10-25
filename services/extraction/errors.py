class ExtractionUnavailableError(Exception):
    """Raised when the AI provider rejects or can't serve an extraction request."""

    def __init__(self, message: str, retryable: bool):
        super().__init__(message)
        self.retryable = retryable
