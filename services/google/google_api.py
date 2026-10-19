from typing import Any

import httpx

from services.google.errors import GoogleApiError

SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
REQUEST_TIMEOUT_SECONDS = 20


class GoogleApi:
    """Thin async client for the Sheets/Drive endpoints this app uses.

    Every Sheets call addresses tabs by sheetId (never by title or A1 notation),
    so renaming a tab can't break anything.
    """

    def __init__(self, access_token: str, http_client: httpx.AsyncClient | None = None):
        self._headers = {"Authorization": f"Bearer {access_token}"}
        self._http_client = http_client

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict:
        if self._http_client is None:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.request(method, url, headers=self._headers, **kwargs)
        else:
            response = await self._http_client.request(method, url, headers=self._headers, **kwargs)

        if response.is_error:
            try:
                reason = response.json().get("error", {}).get("message", "")
            except ValueError:
                reason = response.text
            raise GoogleApiError(response.status_code, reason)
        return response.json() if response.content else {}

    # --- Drive ---------------------------------------------------------------

    async def get_file(self, file_id: str) -> dict:
        return await self._request("GET", f"{DRIVE_FILES_URL}/{file_id}", params={"fields": "id,trashed"})

    async def find_app_files(self, key: str, value: str) -> list[dict]:
        """Untrashed files tagged with our appProperty, oldest first."""
        query = f"appProperties has {{ key='{key}' and value='{value}' }} and trashed = false"
        body = await self._request(
            "GET",
            DRIVE_FILES_URL,
            params={"q": query, "orderBy": "createdTime", "fields": "files(id,createdTime)", "spaces": "drive"},
        )
        return body.get("files", [])

    async def set_app_properties(self, file_id: str, properties: dict[str, str]) -> None:
        await self._request("PATCH", f"{DRIVE_FILES_URL}/{file_id}", json={"appProperties": properties})

    # --- Sheets --------------------------------------------------------------

    async def create_spreadsheet(self, body: dict) -> dict:
        return await self._request("POST", SHEETS_URL, json=body)

    async def get_sheet_properties(self, spreadsheet_id: str) -> list[dict]:
        body = await self._request(
            "GET",
            f"{SHEETS_URL}/{spreadsheet_id}",
            params={"fields": "sheets.properties"},
        )
        return [sheet["properties"] for sheet in body.get("sheets", [])]

    async def batch_update(self, spreadsheet_id: str, requests: list[dict]) -> dict:
        return await self._request(
            "POST",
            f"{SHEETS_URL}/{spreadsheet_id}:batchUpdate",
            json={"requests": requests},
        )

    async def search_developer_metadata(self, spreadsheet_id: str, keys: list[str]) -> list[dict]:
        body = await self._request(
            "POST",
            f"{SHEETS_URL}/{spreadsheet_id}/developerMetadata:search",
            json={"dataFilters": [{"developerMetadataLookup": {"metadataKey": key}} for key in keys]},
        )
        return [match["developerMetadata"] for match in body.get("matchedDeveloperMetadata", [])]

    async def get_grid_values(self, spreadsheet_id: str, grid_range: dict) -> list[list]:
        """Raw cell values for a gridRange; dates come back as serial numbers."""
        body = await self._request(
            "POST",
            f"{SHEETS_URL}/{spreadsheet_id}/values:batchGetByDataFilter",
            json={
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
        """Write raw values to each (gridRange, values) pair."""
        await self._request(
            "POST",
            f"{SHEETS_URL}/{spreadsheet_id}/values:batchUpdateByDataFilter",
            json={
                "valueInputOption": "RAW",
                "data": [
                    {"dataFilter": {"gridRange": grid_range}, "majorDimension": "ROWS", "values": values}
                    for grid_range, values in updates
                ],
            },
        )
