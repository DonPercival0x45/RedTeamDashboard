"""HTTP layer.

Each router lives in its own module here; ``app.main`` wires them onto the
FastAPI app. Cross-cutting dependencies (DB session, Redis, current user) live
in ``app.api.deps``.
"""
