from sqlalchemy import CheckConstraint, Column, Integer, String, DateTime
from datetime import datetime, timezone
from .base import Base

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "google_sub IS NOT NULL OR notion_user_id IS NOT NULL",
            name="ck_users_has_identity",
        ),
        CheckConstraint(
            "default_storage_provider IN ('notion', 'google_sheets')",
            name="ck_users_default_storage_provider",
        ),
        CheckConstraint(
            "default_currency IN ('THB', 'MMK', 'USD')",
            name="ck_users_default_currency",
        ),
        CheckConstraint(
            "default_payment_channel IN ('Cash', 'Bank')",
            name="ck_users_default_payment_channel",
        ),
        CheckConstraint(
            "week_start IN ('monday', 'sunday')",
            name="ck_users_week_start",
        ),
    )
    id = Column(Integer, primary_key=True)
    google_sub = Column(String, unique=True, nullable=True)
    notion_user_id = Column(String, unique=True, nullable=True)
    # Notion only returns the owner's email when the integration has that capability.
    email = Column(String, nullable=True)
    name = Column(String)
    avatar_url = Column(String)
    # Set from the login provider on first login; changed only by the user afterwards.
    default_storage_provider = Column(String, nullable=False)
    # Entry settings (GET/PATCH /api/v1/settings). The defaults fill in what Gemini can't detect.
    default_currency = Column(String, nullable=False, default="THB", server_default="THB")
    default_payment_channel = Column(String, nullable=False, default="Cash", server_default="Cash")
    # Where Insights weeks start (the week period and its weekly buckets).
    week_start = Column(String, nullable=False, default="monday", server_default="monday")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_login_at = Column(DateTime(timezone=True))
