from sqlalchemy import Column, Integer, String, DateTime
from datetime import datetime, timezone
from .base import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    google_sub = Column(String, unique=True, nullable=False)
    email = Column(String, nullable=False)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.now(timezone.utc), nullable=False)