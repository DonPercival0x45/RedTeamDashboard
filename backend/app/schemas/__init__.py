"""Pydantic request/response schemas — the HTTP layer's contract.

Kept separate from ``app.models`` (SQLAlchemy ORM) so changes to wire formats
don't bleed into the DB layer and vice-versa.
"""
