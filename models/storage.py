from sqlalchemy import CheckConstraint, Column, Integer, String, DateTime, ForeignKey, Text, UniqueConstraint
from datetime import datetime, timezone
from constants.storage_status import STATUS_NONE, STORAGE_STATUSES
from .base import Base

_STATUS_VALUES = ", ".join(f"'{status}'" for status in STORAGE_STATUSES)

class UserStorageConfig(Base):
    __tablename__ = "user_storage_config"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_storage_config_user_provider"),
        CheckConstraint(
            "provider IN ('notion', 'google_sheets')",
            name="ck_user_storage_config_provider",
        ),
        CheckConstraint(
            f"storage_status IN ({_STATUS_VALUES})",
            name="ck_user_storage_config_status",
        ),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String, nullable=False)
    # Fernet-encrypted JSON: access_token, refresh_token, expires_at, provider extras.
    encrypted_credentials = Column(Text, nullable=False)
    # Notion database ID or spreadsheet ID; NULL until provisioning succeeds.
    target_id = Column(String, nullable=True)
    connected_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    # Lifecycle of target_id: none | provisioning | ready | failed | needs_permission | schema_broken | missing.
    storage_status = Column(String, nullable=False, default=STATUS_NONE, server_default=STATUS_NONE)
    # Also the provisioning lease start while storage_status is "provisioning".
    status_changed_at = Column(DateTime(timezone=True), nullable=True)

class ExpensesDedup(Base):
    __tablename__ = "expenses_dedup"
    __table_args__ = (
        UniqueConstraint("user_id", "hash", name="uq_expenses_dedup_user_hash"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    hash = Column(String, index=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
