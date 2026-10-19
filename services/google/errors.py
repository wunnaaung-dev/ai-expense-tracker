class GoogleApiError(Exception):
    """Raised when a Google Sheets/Drive API call returns an error status."""

    def __init__(self, status: int, reason: str = ""):
        super().__init__(f"Google API error {status}: {reason}")
        self.status = status
        self.reason = reason


class GooglePermissionError(Exception):
    """Raised when the user's Google grant is missing, revoked or lacks the Drive scope."""
