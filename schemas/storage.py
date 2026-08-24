from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class NotionConnectRequest(BaseModel):
    token: str = Field(..., min_length=1)
    database_id: str = Field(..., min_length=1)


class StorageStatusResponse(BaseModel):
    connected: bool
    provider: Optional[str] = None
    target_id: Optional[str] = None
    connected_at: Optional[datetime] = None
