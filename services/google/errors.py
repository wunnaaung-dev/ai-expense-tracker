# Google's reason when the Drive/Sheets API is off in our Cloud project: a server config
# problem, not the user's grant, so reconsent or retries can't fix it.
SERVICE_DISABLED = "SERVICE_DISABLED"
SERVICE_DISABLED_HINT = (
    " -> Enable the Google Drive API and Google Sheets API in the Google Cloud project"
    " that owns GOOGLE_CLIENT_ID, then retry."
)


class GoogleApiError(Exception):
    """Raised when a Google Sheets/Drive API call returns an error status."""

    def __init__(self, status: int, reason: str = "", error_reason: str = ""):
        # reason: Google's human-readable message; error_reason: its machine code (e.g. SERVICE_DISABLED).
        code = f" ({error_reason})" if error_reason else ""
        super().__init__(f"Google API error {status}{code}: {reason}")
        self.status = status
        self.reason = reason
        self.error_reason = error_reason


class GooglePermissionError(Exception):
    """Raised when the user's Google grant is missing, revoked or lacks the Drive scope."""
