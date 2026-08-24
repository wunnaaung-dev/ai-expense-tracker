from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean, UniqueConstraint
from datetime import datetime, timezone
from .base import Base

class UserStorageConfig(Base):
    __tablename__ = "user_storage_config"
    __table_args__ = (
        UniqueConstraint("user_id", "provider", name="uq_user_storage_config_user_provider"),
    )
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    provider = Column(String, nullable=False)
    encrypted_credentials = Column(Text, nullable=False)
    target_id = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
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
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    hash = Column(String, index=True, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )