class StorageWriteError(Exception):
    """Raised when the user's storage backend rejects a write."""


class StorageNotReadyError(Exception):
    """Raised when the user's storage can't be used yet (see storage_status)."""

    def __init__(self, status: str):
        super().__init__(f"Storage is not ready: {status}")
        self.status = status
