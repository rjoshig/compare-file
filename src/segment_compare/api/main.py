"""FastAPI app factory for the Phase 3 backend.

Run locally::

    uvicorn segment_compare.api.main:app --reload --port 8000

The Vue dev server proxies ``/api/*`` requests here (see
``ui/vite.config.js``).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from segment_compare.api import db
from segment_compare.api.routes import router


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Ensure the SQLite index schema exists before serving requests."""
    db.init_db()
    yield


app = FastAPI(
    title="segment-compare API",
    version="0.0.1",
    description=(
        "Backend for the segment-compare UIs. Serves template layouts, "
        "persists user configs, runs the comparison engine, serves the "
        "resulting HTML report, and exposes a SQLite-backed history + "
        "dashboard for the Next.js UI (ui2)."
    ),
    lifespan=lifespan,
)

# Dev-time CORS — the Vue Vite server runs on :5173, the Next.js (ui2)
# dev server on :3000. Production should narrow this to the real UI origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
