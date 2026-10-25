class DuplicateEntryError(Exception):
    """Raised when a slip is saved that was already saved (e.g. from another tab)."""
