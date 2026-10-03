"""Shared database access; the API and bot use the same repository."""

from backend.database import Base, SessionLocal, engine
from backend.repository import apply_bot_mutation, read_bot_state

__all__ = ["Base", "SessionLocal", "engine", "apply_bot_mutation", "read_bot_state"]