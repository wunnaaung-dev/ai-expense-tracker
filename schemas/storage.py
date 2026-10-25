from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class NotionConnectRequest(BaseModel):
    token: str = Field(..., min_length=1)


class NotionSetupRequest(BaseModel):
    # None: the first page shared with the integration.
    parent_page_id: Optional[str] = Field(None, min_length=1)


class NotionPage(BaseModel):
    id: str
    title: str
    icon: Optional[str] = None


class StorageStatusResponse(BaseModel):
    connected: bool
    provider: Optional[str] = None
    target_id: Optional[str] = None
    connected_at: Optional[datetime] = None
    # none | provisioning | ready | failed | needs_permission | schema_broken | missing
    status: Optional[str] = None


class StorageSwitchRequest(BaseModel):
    provider: Literal["notion", "google_sheets"]


class StorageSwitchResponse(BaseModel):
    default_storage_provider: str
    storage: StorageStatusResponse
