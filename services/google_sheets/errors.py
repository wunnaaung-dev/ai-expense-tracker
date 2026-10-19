class SheetSchemaBrokenError(Exception):
    """Raised when a required column (or the transactions tab) can't be found."""

    def __init__(self, missing: list[str]):
        super().__init__(f"Spreadsheet is missing required columns: {', '.join(missing)}")
        self.missing = missing


class SpreadsheetMissingError(Exception):
    """Raised when the user's spreadsheet was deleted or trashed."""


class RecordNotFoundError(Exception):
    """Raised when no row carries the requested record ID."""
