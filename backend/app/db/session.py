"""Sync SQLAlchemy engine + session factory.

Phase 0 picks sync over async on purpose: small-team tool, no heavy concurrency,
and Alembic + most LangGraph examples are sync. Async can be layered in later
if API endpoints need it.
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
)

SessionLocal: sessionmaker[Session] = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)
