from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, Boolean
from datetime import datetime, timezone
from .base import Base

class UserStorageConfig(Base):
    __tablename__ = "user_storage_config"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    provider = Column(String)
    encrypted_credentials = Column(Text)
    target_id = Column(String)
    is_active = Column(Boolean, default=True)
    connected_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

class ExpensesDedup(Base):
    __tablename__ = "expenses_dedup"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    hash = Column(String, index=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )