from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String

from .base import Base


class UserSession(Base):
    """One signed-in browser. The JWT carries its id (`sid`); revoking the row signs that browser out."""
    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("device_kind IN ('desktop', 'phone')", name="ck_user_sessions_device_kind"),
    )
    # uuid4 hex
    id = Column(String, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    # "Chrome on Windows", parsed from the User-Agent at sign-in.
    device = Column(String, nullable=False)
    device_kind = Column(String, nullable=False)
    # Only the masked IP ("203.81.x.x") is stored, never the full address.
    ip_masked = Column(String, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
