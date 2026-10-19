from sqlalchemy import CheckConstraint, Column, Integer, String, DateTime, ForeignKey, Text, UniqueConstraint
from datetime import datetime, timezone
from .base import Base

class UserStorageConfig(Base):
    __tablename__ = "user_storage_config"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_storage_config_user_provider"),
        CheckConstraint(
            "provider IN ('notion', 'google_sheets')",
            name="ck_user_storage_config_provider",
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
