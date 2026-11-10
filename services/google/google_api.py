from typing import Any

import httpx

from services.google.errors import GoogleApiError

SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
REQUEST_TIMEOUT_SECONDS = 20


def _error_reason(error: dict) -> str:
    """Google's machine-readable cause, e.g. SERVICE_DISABLED or insufficientPermissions."""
    for detail in error.get("details", []):
        if detail.get("@type", "").endswith("google.rpc.ErrorInfo") and detail.get("reason"):
            return detail["reason"]
    errors = error.get("errors") or [{}]
    return errors[0].get("reason") or error.get("status", "")


class GoogleApi:
    """Thin async client for the Sheets/Drive endpoints this app uses.

    Every Sheets call addresses tabs by sheetId (never by title or A1 notation),
    so renaming a tab can't break anything.
    """

    def __init__(self, access_token: str, http_client: httpx.AsyncClient | None = None):
        """Store the bearer header; pass `http_client` to reuse one connection per caller."""
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._http_client = http_client

    # --- transport ----------------------------------------------------------

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        """Send the request with auth headers; interpret the response via `_check`."""
        if self._http_client is None:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                return self._check(await client.request(method, url, headers=self._headers, **kwargs))
        return self._check(await self._http_client.request(method, url, headers=self._headers, **kwargs))

    @staticmethod
    def _check(response: httpx.Response) -> dict:
        """Non-2xx → GoogleApiError(status, message, reason); empty body (PATCH) → {}."""
        if not response.is_error:
            return response.json() if response.content else {}
        try:
            error = response.json().get("error", {})
        except ValueError:
            raise GoogleApiError(response.status_code, response.text)
        raise GoogleApiError(response.status_code, error.get("message", ""), _error_reason(error))

    async def _get(self, url: str, **params: Any) -> dict:
        return await self._request("GET", url, params=params)

    async def _post(self, url: str, payload: dict) -> dict:
        return await self._request("POST", url, json=payload)

    async def _patch(self, url: str, payload: dict) -> None:
        await self._request("PATCH", url, json=payload)

    # --- Drive ---------------------------------------------------------------

    async def get_file(self, file_id: str) -> dict:
        """Drive metadata: `id` + `trashed` — Drive says whether the file is really gone.

        Sheets still serves trashed files, so a trashed/404 file means the storage
        reports `missing`.
        """
        return await self._get(f"{DRIVE_FILES_URL}/{file_id}", fields="id,trashed")

    async def find_app_files(self, key: str, value: str) -> list[dict]:
        """Untrashed files tagged with our appProperty, oldest first.

        Provisioning reuses the first match so repeat logins never create a second
        spreadsheet (the tag comes from `set_app_properties`).
        """
        query = f"appProperties has {{ key='{key}' and value='{value}' }} and trashed = false"
        body = await self._get(
            DRIVE_FILES_URL,
            q=query,
            orderBy="createdTime",
            fields="files(id,createdTime)",
            spaces="drive",
        )
        return body.get("files", [])

    async def set_app_properties(self, file_id: str, properties: dict[str, str]) -> None:
        """Tag a file with our own appProperties (e.g. app=ai-expense-tracker).

        Written before sheet setup runs, so a half-failed setup is still findable
        by `find_app_files` on the next attempt.
        """
        await self._patch(f"{DRIVE_FILES_URL}/{file_id}", {"appProperties": properties})

    # --- Sheets --------------------------------------------------------------

    async def create_spreadsheet(self, body: dict) -> dict:
        """Create a spreadsheet from `body`; its `spreadsheetId` becomes the target_id."""
        return await self._post(SHEETS_URL, body)

    async def get_sheet_properties(self, spreadsheet_id: str) -> list[dict]:
        """`sheets.properties` of every tab (sheetId, title, grid size).

        Repair uses this to tell "no tab at all" (create one) from "columns are
        missing" (add them).
        """
        body = await self._get(f"{SHEETS_URL}/{spreadsheet_id}", fields="sheets.properties")
        return [sheet["properties"] for sheet in body.get("sheets", [])]

    async def batch_update(self, spreadsheet_id: str, requests: list[dict]) -> dict:
        """One call for a batch of Sheets requests (appendCells, updateCells,
        deleteDimension, addSheet + header/validation setup)."""
        return await self._post(f"{SHEETS_URL}/{spreadsheet_id}:batchUpdate", {"requests": requests})

    async def search_developer_metadata(self, spreadsheet_id: str, keys: list[str]) -> list[dict]:
        """Developer metadata matching each key — our column markers.

        The storage turns these into its ColumnMap (which tab/column holds each
        field); no match means the schema is broken and needs repair.
        """
        payload = {"dataFilters": [{"developerMetadataLookup": {"metadataKey": key}} for key in keys]}
        body = await self._post(f"{SHEETS_URL}/{spreadsheet_id}/developerMetadata:search", payload)
        return [match["developerMetadata"] for match in body.get("matchedDeveloperMetadata", [])]

    async def get_grid_values(self, spreadsheet_id: str, grid_range: dict) -> list[list]:
        """Raw cell values for a gridRange; dates come back as serial numbers.

        UNFORMATTED_VALUE returns the underlying value rather than the displayed
        text; an empty range comes back as [].
        """
        body = await self._post(
            f"{SHEETS_URL}/{spreadsheet_id}/values:batchGetByDataFilter",
            {
                "dataFilters": [{"gridRange": grid_range}],
                "majorDimension": "ROWS",
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "SERIAL_NUMBER",
            },
        )
        value_ranges = body.get("valueRanges", [])
        if not value_ranges:
            return []
        return value_ranges[0].get("valueRange", {}).get("values", [])

    async def update_grid_values(self, spreadsheet_id: str, updates: list[tuple[dict, list[list]]]) -> None:
        """Write raw values to each (gridRange, values) pair in one call.

        `valueInputOption=RAW` stores what we send without Google coercing it into
        a date/number format; used to save edited cells.
        """
        await self._post(
            f"{SHEETS_URL}/{spreadsheet_id}/values:batchUpdateByDataFilter",
            {
                "valueInputOption": "RAW",
                "data": [
                    {"dataFilter": {"gridRange": grid_range}, "majorDimension": "ROWS", "values": values}
                    for grid_range, values in updates
                ],
            },
        )
