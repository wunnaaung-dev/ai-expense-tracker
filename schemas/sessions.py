from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel


class SessionOut(BaseModel):
    """One signed-in browser on Settings → Security."""
    id: str
    device: str
    device_kind: Literal["desktop", "phone"]
    ip_masked: Optional[str]
    created_at: datetime
    last_seen_at: datetime
    # The browser making this request ("This device")
    current: bool
