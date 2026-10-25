from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from services.entries.errors import DuplicateEntryError
from services.extraction.errors import ExtractionUnavailableError
from services.google.errors import GooglePermissionError
from services.google_sheets.errors import (
    RecordNotFoundError,
    SheetSchemaBrokenError,
    SpreadsheetMissingError,
)
from services.storage.errors import StorageNotReadyError, StorageWriteError


def _error(status_code: int, code: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"code": code, **extra})


def register_exception_handlers(app: FastAPI) -> None:
    """Map storage and extraction domain errors to the typed bodies the frontend switches on."""

    @app.exception_handler(ExtractionUnavailableError)
    async def extraction_unavailable(request: Request, exc: ExtractionUnavailableError):
        # Nothing was extracted or saved; the frontend keeps the input and offers Retry.
        return _error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "extraction_unavailable",
            retryable=exc.retryable,
            message=str(exc),
        )

    @app.exception_handler(StorageWriteError)
    async def storage_write_failed(request: Request, exc: StorageWriteError):
        # Nothing is kept server-side: the frontend keeps the draft and offers Retry.
        return _error(status.HTTP_502_BAD_GATEWAY, "storage_write_failed", message=str(exc))

    @app.exception_handler(DuplicateEntryError)
    async def duplicate_entry(request: Request, exc: DuplicateEntryError):
        # The slip was saved since extract (e.g. from another tab); nothing was written.
        return _error(status.HTTP_409_CONFLICT, "duplicate_entry")

    @app.exception_handler(StorageNotReadyError)
    async def storage_not_ready(request: Request, exc: StorageNotReadyError):
        return _error(status.HTTP_409_CONFLICT, "storage_not_ready", status=exc.status)

    @app.exception_handler(SheetSchemaBrokenError)
    async def schema_broken(request: Request, exc: SheetSchemaBrokenError):
        return _error(status.HTTP_409_CONFLICT, "schema_broken", missing=exc.missing)

    @app.exception_handler(SpreadsheetMissingError)
    async def storage_missing(request: Request, exc: SpreadsheetMissingError):
        return _error(status.HTTP_409_CONFLICT, "storage_missing")

    @app.exception_handler(GooglePermissionError)
    async def needs_permission(request: Request, exc: GooglePermissionError):
        return _error(status.HTTP_403_FORBIDDEN, "needs_permission")

    @app.exception_handler(RecordNotFoundError)
    async def record_not_found(request: Request, exc: RecordNotFoundError):
        return _error(status.HTTP_404_NOT_FOUND, "record_not_found")
